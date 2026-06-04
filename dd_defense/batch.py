"""Batch processing: audit a whole folder of invoices in one run.

This is the operational workhorse for the service model (a forwarder sends you 30
invoices at once) AND the foundation of the future autonomous agent — the agent is
just this loop on a schedule against an inbox.

The key idea is TRIAGE, not autopilot. Each invoice is classified:
  * auto_clear  — extracted cleanly, high confidence, no integrity flags. Safe to
                  fast-track (you still send the letter; nothing goes out alone).
  * needs_review — low confidence, a container check-digit failure, a high dollar
                  amount, or an extraction error. These get a human's eyes first.
Human-in-the-loop is preserved: triage decides what to REVIEW, never what to send.
"""
from __future__ import annotations

import os

from .audit import run_audit
from .letter import draft_letter
from .schema import Evidence

# files we treat as invoices
_INVOICE_EXTS = (".pdf", ".png", ".jpg", ".jpeg")

# triage thresholds (tunable; later the agent can read these from config)
LOW_CONFIDENCE = 0.6          # any required field below this -> review
HIGH_VALUE = 25_000.0         # invoices above this $ always get human eyes


def find_invoices(folder):
    """Return sorted list of invoice file paths directly in `folder`."""
    out = []
    for name in sorted(os.listdir(folder)):
        if name.startswith("."):
            continue
        if name.lower().endswith(_INVOICE_EXTS):
            out.append(os.path.join(folder, name))
    return out


def _min_confidence(inv):
    """Lowest confidence among present, required-ish fields (proxy for extraction quality)."""
    confs = []
    for name in ("invoice_number", "invoice_date", "issuing_party", "billed_party",
                 "container_numbers", "total_amount_due"):
        f = getattr(inv, name, None)
        if f is not None and f.present_on_invoice and f.has_value():
            confs.append(f.confidence)
    return min(confs) if confs else 0.0


def _triage(inv, report):
    """Return (decision, reasons[]). decision in {auto_clear, needs_review}."""
    reasons = []
    # 1. integrity flags from the audit (e.g. container check-digit failure)
    for fnd in report.findings:
        if fnd.status == "review" and fnd.rule_id == "CONTAINER_CHECK_DIGIT":
            reasons.append("container number failed its check digit (possible misread)")
        if fnd.status == "review" and fnd.category == "required_element":
            reasons.append(f"low-confidence required field: {fnd.title}")
    # 2. overall extraction confidence
    mc = _min_confidence(inv)
    if mc < LOW_CONFIDENCE:
        reasons.append(f"low extraction confidence ({mc:.2f})")
    # 3. high dollar value -> always review
    total = report.total_amount_due or 0
    if total and total >= HIGH_VALUE:
        reasons.append(f"high invoice value ({report.currency or 'USD'} {total:,.0f})")
    # 4. nothing actionable found is itself worth a glance (did extraction work?)
    fails = [f for f in report.findings if f.status == "fail"]
    if not fails and not reasons:
        reasons.append("no dispute grounds found — confirm extraction was correct")

    return ("needs_review" if reasons else "auto_clear"), reasons


def process_invoice(path, evidence=None, db_path=None, client=None,
                    extract_model="claude-haiku-4-5", out_dir=None, save=True):
    """Run one invoice end to end. Returns a result dict (never raises — failures
    are captured so a bad file can't kill the batch)."""
    from .extract import ExtractionError, extract_from_file

    base = os.path.basename(path)
    result = {"file": base, "ok": False, "decision": "needs_review",
              "reasons": [], "case_id": None, "error": None,
              "invoice_number": None, "carrier": None,
              "amount_flagged": 0.0, "amount_billed": 0.0, "currency": "USD"}
    try:
        inv = extract_from_file(path, model=extract_model)
    except ExtractionError as ex:
        result["error"] = str(ex)
        result["reasons"] = ["extraction failed"]
        return result
    except Exception as ex:  # unexpected — capture, keep going
        result["error"] = f"unexpected: {ex}"
        result["reasons"] = ["extraction error"]
        return result

    report = run_audit(inv, evidence)
    letter = draft_letter(report)
    rd = report.to_dict()
    decision, reasons = _triage(inv, report)

    result.update({
        "ok": True, "decision": decision, "reasons": reasons,
        "invoice_number": report.invoice_number, "carrier": report.issuing_party,
        "currency": report.currency or "USD",
        "amount_billed": report.total_amount_due or 0.0,
        "amount_flagged": round(max(report.amount_obligation_eliminated or 0,
                                    report.amount_disputable or 0), 2),
    })

    if out_dir:
        import json
        sub = os.path.join(out_dir, os.path.splitext(base)[0])
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "report.json"), "w") as fh:
            json.dump(rd, fh, indent=2)
        with open(os.path.join(sub, "letter.md"), "w") as fh:
            fh.write(letter)

    if save and db_path:
        from . import store
        conn = store.connect(db_path)
        result["case_id"] = store.create_case(conn, rd, letter=letter, client=client)
        conn.close()

    return result


def process_folder(folder, evidence=None, db_path=None, client=None,
                   extract_model="claude-haiku-4-5", out_dir=None, save=True,
                   on_progress=None):
    """Process every invoice in `folder`. Returns (results[], summary{}).
    `on_progress(i, n, result)` is called after each file if provided."""
    files = find_invoices(folder)
    results = []
    for i, path in enumerate(files, start=1):
        r = process_invoice(path, evidence=evidence, db_path=db_path, client=client,
                            extract_model=extract_model, out_dir=out_dir, save=save)
        results.append(r)
        if on_progress:
            on_progress(i, len(files), r)
    return results, summarize(results)


def summarize(results):
    ok = [r for r in results if r["ok"]]
    return {
        "total_files": len(results),
        "processed": len(ok),
        "failed": len(results) - len(ok),
        "auto_clear": sum(1 for r in ok if r["decision"] == "auto_clear"),
        "needs_review": sum(1 for r in ok if r["decision"] == "needs_review"),
        "total_flagged": round(sum(r["amount_flagged"] for r in ok), 2),
        "total_billed": round(sum(r["amount_billed"] for r in ok), 2),
        "currency": (ok[0]["currency"] if ok else "USD"),
    }
