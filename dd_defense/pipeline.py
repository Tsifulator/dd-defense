"""Autonomous invoice pipeline: source -> audit -> SQLite case -> Airtable.

Ties the existing engine into one hands-off flow. A source yields invoice file
paths (an email inbox, or a watched folder); each is audited, saved as a tracked
case, optionally pushed to Airtable, and triaged (auto-clear vs needs-review).

Idempotency: each invoice file is fingerprinted (sha256 of its bytes); already-
processed fingerprints are skipped, so re-running the pipeline never double-audits
or duplicates a case. The ledger of seen fingerprints lives next to the case DB.
"""
from __future__ import annotations

import hashlib
import json
import os

from .audit import run_audit
from .batch import _triage
from .letter import draft_letter
from .schema import Evidence


def file_fingerprint(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ledger_path(db_path):
    base = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(base, "processed.json")


def _load_ledger(db_path):
    p = _ledger_path(db_path)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return set(json.load(fh))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def _save_ledger(db_path, seen):
    try:
        with open(_ledger_path(db_path), "w", encoding="utf-8") as fh:
            json.dump(sorted(seen), fh)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# sources (yield invoice file paths)
# ---------------------------------------------------------------------------


def source_folder(folder):
    """Yield invoice file paths from a folder (non-recursive)."""
    from .batch import find_invoices
    return list(find_invoices(folder))


def source_inbox(save_dir="inbox_attachments", **imap_kwargs):
    """Fetch invoice attachments from the email inbox; yield their saved paths.
    Returns (paths, email_meta_by_path) so the pipeline can tag cases by sender."""
    from . import inbox
    msgs = inbox.fetch_invoice_emails(save_dir=save_dir, **imap_kwargs)
    paths, meta_by_path = [], {}
    for m in msgs:
        for p in m["saved_paths"]:
            paths.append(p)
            meta_by_path[p] = m["meta"]
    return paths, meta_by_path


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------


def process_paths(paths, db_path=None, client=None, evidence=None, push_airtable=False,
                  extract_model="claude-haiku-4-5", on_progress=None, meta_by_path=None):
    """Audit each invoice path, save as a case, optionally push to Airtable.
    Skips files already processed (by content fingerprint). Returns a summary."""
    from . import store
    from .extract import ExtractionError, extract_from_file

    db_path = db_path or store.DEFAULT_DB
    seen = _load_ledger(db_path)
    meta_by_path = meta_by_path or {}

    results = []
    conn = store.connect(db_path)
    try:
        for path in paths:
            base = os.path.basename(path)
            try:
                fp = file_fingerprint(path)
            except OSError as ex:
                results.append({"file": base, "ok": False, "skipped": False, "error": str(ex)})
                continue
            if fp in seen:
                results.append({"file": base, "ok": True, "skipped": True, "reason": "already processed"})
                if on_progress:
                    on_progress(results[-1])
                continue

            try:
                inv = extract_from_file(path, model=extract_model)
            except ExtractionError as ex:
                results.append({"file": base, "ok": False, "skipped": False, "error": str(ex)})
                if on_progress:
                    on_progress(results[-1])
                continue

            report = run_audit(inv, evidence)
            letter = draft_letter(report)
            rd = report.to_dict()
            decision, reasons = _triage(inv, report)

            # tag client from the sender's domain if not given
            this_client = client
            meta = meta_by_path.get(path)
            if not this_client and meta:
                this_client = _client_from_sender(meta.get("from"))

            case_id = store.create_case(conn, rd, letter=letter, client=this_client)
            seen.add(fp)

            r = {"file": base, "ok": True, "skipped": False, "case_id": case_id,
                 "decision": decision, "reasons": reasons,
                 "invoice_number": report.invoice_number, "carrier": report.issuing_party,
                 "currency": report.currency or "USD",
                 "amount_flagged": round(max(report.amount_obligation_eliminated or 0,
                                             report.amount_disputable or 0), 2),
                 "amount_billed": report.total_amount_due or 0,
                 "client": this_client}
            results.append(r)
            if on_progress:
                on_progress(r)
    finally:
        conn.close()
        _save_ledger(db_path, seen)

    # optional Airtable push of all (new) cases
    pushed = 0
    if push_airtable:
        try:
            from . import airtable_sync
            s = airtable_sync.sync(db_path=db_path)
            pushed = s["created"] + s["updated"]
        except Exception as ex:
            for r in results:
                r.setdefault("airtable_error", str(ex))

    return {"results": results, "summary": _summary(results, pushed, push_airtable)}


def _summary(results, pushed, push_airtable):
    ok = [r for r in results if r.get("ok") and not r.get("skipped")]
    return {
        "total": len(results),
        "processed": len(ok),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "auto_clear": sum(1 for r in ok if r.get("decision") == "auto_clear"),
        "needs_review": sum(1 for r in ok if r.get("decision") == "needs_review"),
        "total_flagged": round(sum(r.get("amount_flagged", 0) for r in ok), 2),
        "airtable_pushed": pushed if push_airtable else None,
    }


def _client_from_sender(from_header):
    """Best-effort client label from an email 'From' (the domain)."""
    if not from_header or "@" not in from_header:
        return None
    domain = from_header.split("@")[-1].strip(">").split(">")[0].split()[0]
    name = domain.split(".")[0]
    return name.capitalize() if name else None
