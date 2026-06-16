"""Create the D&D operations base schema in Airtable, from your API token.

So you never hand-build tables. Given an empty base + a token with schema scope,
this creates three tables with the right fields:

  Prospects — the outreach queue (scrape -> draft -> you approve -> you send)
  Leads     — inbound free-audit requests from the dnddefense.com form
  Cases     — mirror of the audit/savings tracker (billed / flagged / recovered)

Run:  python -m dd_defense.airtable_setup
(needs AIRTABLE_API_KEY with schema.bases:write + AIRTABLE_BASE_ID, see AIRTABLE_SETUP.md)

Idempotent-ish: it skips tables that already exist by name, and adds any missing
fields to existing tables. Safe to re-run.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

META_ROOT = "https://api.airtable.com/v0/meta/bases"

# ---------------------------------------------------------------------------
# Desired schema (data — edit here to evolve the base)
# ---------------------------------------------------------------------------

_SINGLE_SELECT = lambda name, choices: {
    "name": name, "type": "singleSelect",
    "options": {"choices": [{"name": c} for c in choices]},
}

SCHEMA = {
    "Prospects": [
        {"name": "Company", "type": "singleLineText"},
        _SINGLE_SELECT("Type", ["Forwarder", "Importer", "Broker", "Drayage", "Other"]),
        {"name": "Contact Name", "type": "singleLineText"},
        {"name": "Title", "type": "singleLineText"},
        {"name": "Email", "type": "email"},
        {"name": "Phone", "type": "singleLineText"},
        {"name": "LinkedIn / URL", "type": "url"},
        {"name": "Location / Port", "type": "singleLineText"},
        {"name": "Est. Containers/mo", "type": "number", "options": {"precision": 0}},
        {"name": "Fit Score", "type": "number", "options": {"precision": 0}},
        {"name": "Source", "type": "singleLineText"},
        _SINGLE_SELECT("Status", [
            "New", "Drafted", "Needs Approval", "Approved", "Sent",
            "Replied", "Call Booked", "Pilot", "Won", "Lost", "Not a Fit"]),
        {"name": "Draft Subject", "type": "singleLineText"},
        {"name": "Draft Email", "type": "multilineText"},
        {"name": "Notes", "type": "multilineText"},
    ],
    "Leads": [
        {"name": "Company", "type": "singleLineText"},
        {"name": "Contact Name", "type": "singleLineText"},
        {"name": "Email", "type": "email"},
        {"name": "Monthly Invoices", "type": "singleLineText"},
        {"name": "Message", "type": "multilineText"},
        {"name": "Source", "type": "singleLineText"},
        _SINGLE_SELECT("Status", ["New", "Contacted", "Pilot", "Won", "Lost"]),
    ],
    "Cases": [
        {"name": "Case Ref", "type": "singleLineText"},
        {"name": "Invoice Number", "type": "singleLineText"},
        {"name": "Carrier", "type": "singleLineText"},
        {"name": "Client", "type": "singleLineText"},
        {"name": "Amount Billed", "type": "currency", "options": {"precision": 2, "symbol": "$"}},
        {"name": "Amount Flagged", "type": "currency", "options": {"precision": 2, "symbol": "$"}},
        {"name": "Amount Recovered", "type": "currency", "options": {"precision": 2, "symbol": "$"}},
        _SINGLE_SELECT("Status", [
            "drafted", "sent", "responded", "resolved", "rejected", "withdrawn"]),
    ],
}

# The first field of each table becomes the primary field.
_PRIMARY = {"Prospects": "Company", "Leads": "Company", "Cases": "Case Ref"}


def _request(method, url, key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as ex:
        msg = ""
        try:
            msg = ex.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"{method} {url} -> HTTP {ex.code}: {msg[:500]}")


def _existing_tables(base_id, key):
    resp = _request("GET", f"{META_ROOT}/{base_id}/tables", key)
    return {t["name"]: t for t in resp.get("tables", [])}


def _create_table(base_id, key, name, fields):
    # primary field first
    primary = _PRIMARY[name]
    ordered = sorted(fields, key=lambda f: 0 if f["name"] == primary else 1)
    body = {"name": name, "fields": ordered}
    return _request("POST", f"{META_ROOT}/{base_id}/tables", key, body)


def _add_field(base_id, key, table_id, field):
    return _request("POST", f"{META_ROOT}/{base_id}/tables/{table_id}/fields", key, field)


def setup(base_id=None, api_key=None, verbose=True):
    key = api_key or os.environ.get("AIRTABLE_API_KEY")
    base_id = base_id or os.environ.get("AIRTABLE_BASE_ID")
    if not key or not base_id:
        raise RuntimeError("Set AIRTABLE_API_KEY and AIRTABLE_BASE_ID first (see AIRTABLE_SETUP.md).")

    existing = _existing_tables(base_id, key)
    summary = {"created": [], "updated": [], "unchanged": []}

    for table_name, fields in SCHEMA.items():
        if table_name not in existing:
            _create_table(base_id, key, table_name, fields)
            summary["created"].append(table_name)
            if verbose:
                print(f"  + created table '{table_name}' ({len(fields)} fields)")
        else:
            have = {f["name"] for f in existing[table_name].get("fields", [])}
            tid = existing[table_name]["id"]
            added = 0
            for f in fields:
                if f["name"] not in have:
                    _add_field(base_id, key, tid, f)
                    added += 1
            if added:
                summary["updated"].append(table_name)
                if verbose:
                    print(f"  ~ table '{table_name}': added {added} missing field(s)")
            else:
                summary["unchanged"].append(table_name)
                if verbose:
                    print(f"  = table '{table_name}' already up to date")
    return summary


def main(argv=None):
    print("Setting up the D&D Airtable base schema...\n")
    try:
        s = setup()
    except RuntimeError as ex:
        print(f"error: {ex}")
        return 1
    print(f"\nDone. created={s['created']} updated={s['updated']} unchanged={s['unchanged']}")
    print("Next: python -m dd_defense.outreach ... to start queuing drafts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
