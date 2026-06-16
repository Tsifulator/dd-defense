"""Airtable integration package — REST client, schema setup, case sync.

All Airtable-related code lives here. Other modules import from this package:

    from dd_defense.airtable import client   # or just: from dd_defense import airtable
    from dd_defense.airtable import setup
    from dd_defense.airtable import sync

For backward compat, the public names from client.py are re-exported here so
`from dd_defense import airtable; airtable.ping()` still works.
"""
from .client import (  # noqa: F401 — re-export for backward compat
    API_ROOT,
    TABLE_PROSPECTS,
    TABLE_LEADS,
    TABLE_CASES,
    AirtableError,
    clean_fields,
    create_record,
    create_records,
    find_one,
    list_records,
    ping,
    update_record,
    upsert_by_field,
    # internal but used by tests:
    _api_key,
    _records_payload,
    _chunk,
)
