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


def extract_from_file(path, model="claude-haiku-4-5", api_key=None, max_pages=3):
    """Extract a ParsedInvoice from a PDF/image using a cheap vision model."""
    import anthropic  # lazy

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (or pass api_key=...).")
    client = anthropic.Anthropic(api_key=key)

    content = []
    is_pdf = str(path).lower().endswith(".pdf")
    text = _pdf_text(path) if is_pdf else ""

    if text and len(text.strip()) > 200:
        content.append({"type": "text", "text": "Invoice text:\n\n" + text})
    else:
        # scanned PDF or image -> send images
        import base64
        images = _render_first_pages_png(path, max_pages) if is_pdf else None
        if images is None:  # raw image file
            with open(path, "rb") as fh:
                raw = fh.read()
            ext = str(path).lower().rsplit(".", 1)[-1]
            media = "image/png" if ext == "png" else "image/jpeg"
            images = [raw]
            medias = [media]
        else:
            medias = ["image/png"] * len(images)
        if not images:
            raise RuntimeError(
                "Could not read text or render images from the file. Install extraction "
                "extras: pip install pypdf pypdfium2 pillow")
        for img, media in zip(images, medias):
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": media,
                "data": base64.standard_b64encode(img).decode("ascii")}})
        content.append({"type": "text", "text": "Extract the D&D invoice fields from the image(s)."})

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
    raise RuntimeError("Model did not return a structured invoice.")
