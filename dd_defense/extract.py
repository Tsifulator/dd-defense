"""Invoice -> ParsedInvoice.

Two entry points:
  * load_parsed(path)      — read a ParsedInvoice from JSON (no LLM). Use this to
                             develop/iterate on rules without spending tokens.
  * extract_from_file(...) — real extraction: pull text from a PDF, fall back to a
                             rendered image for scanned files, and ask a cheap
                             vision-capable model to return the structured fields.

Third-party deps (anthropic, pypdf, pypdfium2, pillow) are imported lazily so the
audit/report/letter path runs with stdlib only.
"""
from __future__ import annotations

import json
import os

from .schema import ParsedInvoice

# JSON Schema handed to the model as a tool, mirroring ParsedInvoice. Each field
# is an object {value, present_on_invoice, confidence, source_text} so the model
# reports provenance and — crucially — marks elements that are absent.
_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "number", "array", "null"]},
        "present_on_invoice": {"type": "boolean"},
        "confidence": {"type": "number"},
        "source_text": {"type": "string"},
    },
    "required": ["value", "present_on_invoice", "confidence"],
}

_FIELD_NAMES = [
    "invoice_number", "invoice_date", "due_date", "issuing_party", "billed_party",
    "currency", "bl_numbers", "container_numbers", "port_of_discharge",
    "basis_for_liability", "charge_type", "free_time_allowed_days",
    "free_time_start", "free_time_end", "rate_rule_reference", "per_diem_rates",
    "dispute_contact", "mitigation_process", "stmt_fmc_consistent",
    "stmt_no_fault", "total_amount_due",
]

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        **{name: _FIELD_SCHEMA for name in _FIELD_NAMES},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "container_number": {"type": ["string", "null"]},
                    "charge_type": {"type": ["string", "null"]},
                    "start_date": {"type": ["string", "null"]},
                    "end_date": {"type": ["string", "null"]},
                    "days_charged": {"type": ["number", "null"]},
                    "rate_applied": {"type": ["number", "null"]},
                    "line_total": {"type": ["number", "null"]},
                },
            },
        },
    },
    "required": _FIELD_NAMES + ["line_items"],
}

_SYSTEM = (
    "You extract structured data from an ocean carrier demurrage & detention (D&D) "
    "invoice. Return ONLY the structured tool call. For every field set "
    "present_on_invoice=false if the invoice does not actually contain it — do NOT "
    "guess or infer a value to fill a gap; a missing element is a meaningful signal. "
    "Use confidence in [0,1]. Dates as ISO YYYY-MM-DD when possible. Put each charge "
    "row in line_items. bl_numbers and container_numbers are arrays."
)


def load_parsed(path):
    """Load a ParsedInvoice from a JSON file (no LLM)."""
    with open(path, "r", encoding="utf-8") as fh:
        return ParsedInvoice.from_dict(json.load(fh))


# Magic bytes for the formats we accept. Validating content (not just the
# filename extension) stops a mislabeled or corrupt upload from reaching the
# (paid) extractor and failing in a confusing way.
_MAGIC = {
    b"%PDF": "pdf",
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
}


def sniff_filetype(data):
    """Return 'pdf' | 'png' | 'jpeg' from the leading bytes, or None if unknown."""
    if not data:
        return None
    head = data[:16]
    for magic, kind in _MAGIC.items():
        if head.startswith(magic):
            return kind
    # PDFs occasionally carry leading whitespace/BOM before %PDF
    if b"%PDF" in data[:1024]:
        return "pdf"
    return None


class ExtractionError(RuntimeError):
    """Raised when an invoice cannot be read into structured fields. The message
    is safe to show a user."""


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _render_first_pages_png(path, max_pages=3):
    """Render PDF pages to PNG bytes for scanned invoices. Returns [] if unavailable."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out = []
    try:
        doc = pdfium.PdfDocument(path)
        for i in range(min(len(doc), max_pages)):
            bitmap = doc[i].render(scale=2.0)
            pil = bitmap.to_pil()
            import io
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            out.append(buf.getvalue())
    except Exception:
        return []
    return out


def _build_content(path, kind, max_pages):
    """Assemble the message content blocks (text or images) for the model.
    Returns (content_list, raw_text). Raises ExtractionError if nothing usable."""
    import base64

    content = []
    text = _pdf_text(path) if kind == "pdf" else ""

    if text and len(text.strip()) > 200:
        # Text-based PDF: cheapest path, no image tokens.
        content.append({"type": "text", "text": "Invoice text:\n\n" + text})
        return content, text

    # Scanned PDF or image file -> send page images.
    if kind == "pdf":
        images = _render_first_pages_png(path, max_pages)
        medias = ["image/png"] * len(images)
        if not images:
            raise ExtractionError(
                "This looks like a scanned PDF, but the page images could not be "
                "rendered. Ensure pypdfium2 + pillow are installed, or upload a clearer file.")
    else:
        with open(path, "rb") as fh:
            raw = fh.read()
        media = "image/png" if kind == "png" else "image/jpeg"
        images, medias = [raw], [media]

    for img, media in zip(images, medias):
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media,
            "data": base64.standard_b64encode(img).decode("ascii")}})
    content.append({"type": "text",
                    "text": "Extract the D&D invoice fields from the image(s)."})
    return content, text


def extract_from_file(path, model="claude-haiku-4-5", api_key=None, max_pages=5, max_retries=2):
    """Extract a ParsedInvoice from a PDF/image using a cheap vision model.

    Robustness:
      * validates file CONTENT by magic bytes (not just the extension);
      * text-PDF fast path, image fallback for scans/photos, up to `max_pages`;
      * retries transient API errors with exponential backoff;
      * raises ExtractionError with a user-safe message on give-up.
    """
    import anthropic  # lazy

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ExtractionError("No API key configured (set ANTHROPIC_API_KEY).")

    try:
        with open(path, "rb") as fh:
            head = fh.read(2048)
    except OSError as ex:
        raise ExtractionError(f"Could not open the uploaded file: {ex}")

    kind = sniff_filetype(head)
    if kind is None:
        raise ExtractionError(
            "The file does not look like a PDF, PNG, or JPEG. Please upload a real "
            "invoice file (a mislabeled or corrupt file can cause this).")

    content, text = _build_content(path, kind, max_pages)
    client = anthropic.Anthropic(api_key=key)

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=2000,
                system=_SYSTEM,
                tools=[{"name": "emit_invoice", "description": "Return the structured invoice.",
                        "input_schema": _TOOL_SCHEMA}],
                tool_choice={"type": "tool", "name": "emit_invoice"},
                messages=[{"role": "user", "content": content}],
            )
            for block in msg.content:
                if getattr(block, "type", None) == "tool_use":
                    data = dict(block.input)
                    data["raw_text"] = text
                    return ParsedInvoice.from_dict(data)
            raise ExtractionError("The model did not return structured invoice data. Try re-running.")
        except ExtractionError:
            raise
        except Exception as ex:  # network / rate-limit / transient API errors
            last_err = ex
            transient = _is_transient(ex)
            if attempt < max_retries and transient:
                _sleep_backoff(attempt)
                continue
            break

    raise ExtractionError(f"Extraction failed after {max_retries + 1} attempt(s): {last_err}")


def _is_transient(ex):
    """Heuristic: retry on rate-limit / overloaded / 5xx / timeouts."""
    name = type(ex).__name__.lower()
    if any(k in name for k in ("ratelimit", "overloaded", "timeout", "connection", "apistatus", "internalserver")):
        return True
    status = getattr(ex, "status_code", None)
    if status in (408, 409, 429, 500, 502, 503, 504):
        return True
    msg = str(ex).lower()
    return any(k in msg for k in ("overloaded", "rate limit", "timeout", "temporarily"))


def _sleep_backoff(attempt):
    import time
    time.sleep(min(2 ** attempt, 8))  # 1s, 2s, 4s, capped at 8s
