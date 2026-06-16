"""Sync the local SQLite case/savings tracker into the Airtable Cases table.

One-way push (SQLite -> Airtable), upserting on "Case Ref" so re-running updates
existing rows instead of duplicating. Lets the savings dashboard live in Airtable
(mobile-friendly, shareable) while the engine keeps writing to SQLite.
"""
from __future__ import annotations

from . import client as airtable
from dd_defense import store


def case_to_fields(case):
    """Map a SQLite case row (dict) to Airtable Cases fields."""
    return {
        "Case Ref": store.case_ref(case["id"]),
        "Invoice Number": case.get("invoice_number"),
        "Carrier": case.get("carrier"),
        "Client": case.get("client"),
        "Amount Billed": case.get("amount_billed") or 0,
        "Amount Flagged": case.get("amount_flagged") or 0,
        "Amount Recovered": case.get("amount_recovered") or 0,
        "Status": case.get("status"),
    }


def sync(db_path=None, api_key=None, base_id=None, on_progress=None):
    """Push all cases from the SQLite DB into Airtable. Returns a summary dict."""
    conn = store.connect(db_path or store.DEFAULT_DB)
    cases = store.list_cases(conn)
    conn.close()

    created = updated = 0
    for c in cases:
        fields = case_to_fields(c)
        _rec, was_created = airtable.upsert_by_field(
            airtable.TABLE_CASES, "Case Ref", fields["Case Ref"], fields,
            api_key=api_key, base_id=base_id)
        if was_created:
            created += 1
        else:
            updated += 1
        if on_progress:
            on_progress(fields["Case Ref"], was_created)
    return {"total": len(cases), "created": created, "updated": updated}
