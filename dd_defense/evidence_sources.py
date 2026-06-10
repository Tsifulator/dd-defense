"""Evidence enrichment — auto-build the Evidence sidecar that powers Layer-2 grounds.

The substantive (incentive-principle) checks need facts the invoice doesn't carry:
weekends/holidays, terminal closures, tariff rates. This module assembles those from
sources we CAN obtain, so disputes get auto-evidenced instead of returning
needs_evidence.

Honest scope:
  * Holidays/weekends — fully automatic (computed; no network). Already used by the
    HOLIDAY_WEEKEND rule via free_time_tolls_holidays.
  * Terminal/port closures — there is no single free public API. We read a
    LOCAL, OPERATOR-MAINTAINED file (closures.json) you keep updated from terminal
    notices, plus auto-add federal holidays as closure days. This is the honest,
    reliable approach; a live terminal-feed integration can be added later per port.
  * Tariff rates — read from a LOCAL tariffs.json you maintain per carrier/rule
    (carriers publish these as PDFs/pages; we don't scrape them live in v1).

Everything is shaped into a `schema.Evidence` the audit already understands.
"""
from __future__ import annotations

import json
import os

from .calendars import us_federal_holidays
from .schema import Evidence

DEFAULT_DATA_DIR = os.environ.get("DD_EVIDENCE_DIR", "evidence_data")


def _read_json(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def holiday_closures(years):
    """Return closure dicts for US federal holidays across the given years —
    so the CLOSURE check treats them as non-operating days automatically."""
    out = []
    for y in years:
        for d in sorted(us_federal_holidays(y)):
            out.append({"location": "US federal holiday", "start": d.isoformat(),
                        "end": d.isoformat(), "reason": "US federal holiday"})
    return out


def build_evidence(years=(2024, 2025, 2026), data_dir=None, include_holiday_closures=True):
    """Assemble an Evidence sidecar from local operator data + computed holidays.

    Reads (all optional) from data_dir:
      closures.json        [{location,start,end,reason}, ...]
      no_appointment.json  ["YYYY-MM-DD", ...]
      government_holds.json[{container_number,start,end,reason}, ...]
      tariffs.json         {"<rate basis or 'default'>": rate, ...}
      containers.json      [{container_number,last_free_day,available_for_pickup,...}]
    Returns a schema.Evidence ready for run_audit()."""
    data_dir = data_dir or DEFAULT_DATA_DIR

    closures = list(_read_json(os.path.join(data_dir, "closures.json"), []))
    if include_holiday_closures:
        closures = closures + holiday_closures(years)

    d = {
        "free_time_tolls_holidays": True,   # default: tariff excludes weekends/holidays
        "closures": closures,
        "no_appointment_dates": _read_json(os.path.join(data_dir, "no_appointment.json"), []),
        "government_holds": _read_json(os.path.join(data_dir, "government_holds.json"), []),
        "tariff_rates": _read_json(os.path.join(data_dir, "tariffs.json"), {}),
        "containers": _read_json(os.path.join(data_dir, "containers.json"), []),
    }
    return Evidence.from_dict(d)


def scaffold(data_dir=None):
    """Create an evidence_data/ folder with example files the operator fills in.
    Returns the list of files written."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    examples = {
        "closures.json": [
            {"location": "Port of Los Angeles", "start": "2025-01-13", "end": "2025-01-13",
             "reason": "terminal congestion closure (example — replace with real notices)"}
        ],
        "no_appointment.json": ["2025-01-14", "2025-01-15"],
        "government_holds.json": [
            {"container_number": "EXAMPLE1234567", "start": "2025-01-10", "end": "2025-01-12",
             "reason": "CBP exam hold (example)"}
        ],
        "tariffs.json": {"default": 120, "PBLU US Demurrage Tariff Rule 210": 120},
        "containers.json": [
            {"container_number": "EXAMPLE1234567", "last_free_day": "2025-01-05",
             "available_for_pickup": "2025-01-09"}
        ],
    }
    written = []
    for name, content in examples.items():
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):  # don't clobber the operator's real data
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(content, fh, indent=2)
            written.append(path)
    return written
