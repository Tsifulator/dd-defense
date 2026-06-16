"""Minimal Airtable REST client (stdlib only — urllib).

Powers the D&D "operations base": a Prospects outreach queue, inbound Leads, and a
mirror of the Cases/savings tracker. Kept dependency-free to match the rest of the
package. HTTP is isolated in `_request` so the payload-shaping helpers can be unit
tested without a network or an API key.

Config (env, see .env.example / AIRTABLE_SETUP.md):
  AIRTABLE_API_KEY     personal access token (scopes: data.records:read/write,
                       schema.bases:read/write for the setup script)
  AIRTABLE_BASE_ID     appXXXXXXXXXXXXXX

Table names default to Prospects / Leads / Cases but can be overridden via
AIRTABLE_TABLE_PROSPECTS etc.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.airtable.com/v0"

TABLE_PROSPECTS = os.environ.get("AIRTABLE_TABLE_PROSPECTS", "Prospects")
TABLE_LEADS = os.environ.get("AIRTABLE_TABLE_LEADS", "Leads")
TABLE_CASES = os.environ.get("AIRTABLE_TABLE_CASES", "Cases")


class AirtableError(RuntimeError):
    """Raised on an Airtable API failure. Message is safe to surface."""


def _api_key(explicit=None):
    key = explicit or os.environ.get("AIRTABLE_API_KEY")
    if not key:
        raise AirtableError("AIRTABLE_API_KEY not set (or pass api_key=...).")
    return key


def _base_id(explicit=None):
    bid = explicit or os.environ.get("AIRTABLE_BASE_ID")
    if not bid:
        raise AirtableError("AIRTABLE_BASE_ID not set (or pass base_id=...).")
    return bid


# ---------------------------------------------------------------------------
# payload shaping (pure, unit-testable — no network)
# ---------------------------------------------------------------------------

# Airtable rejects null/empty on some field types; strip them and coerce types.
def clean_fields(fields):
    """Drop None/empty values and coerce to Airtable-friendly types."""
    out = {}
    for k, v in (fields or {}).items():
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                continue
        if isinstance(v, (list, tuple)):
            v = [x for x in v if x not in (None, "")]
            if not v:
                continue
            v = list(v)
        out[k] = v
    return out


def _records_payload(list_of_field_dicts, typecast=True):
    """Build the body for a batch create/update (Airtable caps at 10 per call)."""
    return {
        "records": [{"fields": clean_fields(f)} for f in list_of_field_dicts],
        "typecast": typecast,
    }


def _chunk(seq, n=10):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------------------
# HTTP (isolated; everything above is testable without this)
# ---------------------------------------------------------------------------


def _request(method, url, api_key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as ex:
        detail = ""
        try:
            detail = ex.read().decode("utf-8")
        except Exception:
            pass
        raise AirtableError(f"Airtable {method} {url} -> HTTP {ex.code}: {detail[:400]}")
    except urllib.error.URLError as ex:
        raise AirtableError(f"Airtable network error: {ex}")


# ---------------------------------------------------------------------------
# public operations
# ---------------------------------------------------------------------------


def _table_url(table, base_id):
    return f"{API_ROOT}/{base_id}/{urllib.parse.quote(table)}"


def create_records(table, field_dicts, api_key=None, base_id=None, typecast=True):
    """Create many records (auto-batched by 10). Returns the created records."""
    key, bid = _api_key(api_key), _base_id(base_id)
    created = []
    for batch in _chunk(list(field_dicts), 10):
        resp = _request("POST", _table_url(table, bid), key, _records_payload(batch, typecast))
        created.extend(resp.get("records", []))
    return created


def create_record(table, fields, api_key=None, base_id=None, typecast=True):
    recs = create_records(table, [fields], api_key=api_key, base_id=base_id, typecast=typecast)
    return recs[0] if recs else None


def list_records(table, api_key=None, base_id=None, formula=None, max_records=None, page_size=100):
    """List records, following pagination. Optional Airtable filterByFormula."""
    key, bid = _api_key(api_key), _base_id(base_id)
    out, offset = [], None
    while True:
        params = {"pageSize": page_size}
        if formula:
            params["filterByFormula"] = formula
        if offset:
            params["offset"] = offset
        url = _table_url(table, bid) + "?" + urllib.parse.urlencode(params)
        resp = _request("GET", url, key)
        out.extend(resp.get("records", []))
        if max_records and len(out) >= max_records:
            return out[:max_records]
        offset = resp.get("offset")
        if not offset:
            return out


def update_record(table, record_id, fields, api_key=None, base_id=None, typecast=True):
    key, bid = _api_key(api_key), _base_id(base_id)
    url = f"{_table_url(table, bid)}/{record_id}"
    return _request("PATCH", url, key, {"fields": clean_fields(fields), "typecast": typecast})


def find_one(table, formula, api_key=None, base_id=None):
    """Return the first record matching an Airtable formula, or None."""
    recs = list_records(table, api_key=api_key, base_id=base_id, formula=formula, max_records=1)
    return recs[0] if recs else None


def upsert_by_field(table, key_field, key_value, fields, api_key=None, base_id=None):
    """Create-or-update keyed on a unique field (e.g. invoice number / case ref).
    Returns (record, created: bool)."""
    safe = str(key_value).replace("'", "\\'")
    existing = find_one(table, f"{{{key_field}}}='{safe}'", api_key=api_key, base_id=base_id)
    merged = dict(fields)
    merged[key_field] = key_value
    if existing:
        return update_record(table, existing["id"], merged, api_key=api_key, base_id=base_id), False
    return create_record(table, merged, api_key=api_key, base_id=base_id), True


def ping(api_key=None, base_id=None):
    """Cheap connectivity check: list 1 record from Prospects. Returns True/raises."""
    list_records(TABLE_PROSPECTS, api_key=api_key, base_id=base_id, max_records=1)
    return True
