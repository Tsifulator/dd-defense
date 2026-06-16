"""Migrate the local SQLite cases.db into Postgres (Supabase), preserving ids.

Source : CASES_DB env var, or data/cases.db by default.
Target : DD_DATABASE_URL (the same var store.py uses to switch backends).
Dry-run by default; pass --commit to write. Idempotent — --commit TRUNCATEs the
target first, so re-running it re-syncs (handy right before the final cutover).
"""
import os
import sqlite3
import sys

DRY = "--commit" not in sys.argv
SRC = os.environ.get("CASES_DB", os.path.join("data", "cases.db"))
DSN = os.environ.get("DD_DATABASE_URL")

src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
cases = [dict(r) for r in src.execute("SELECT * FROM cases ORDER BY id")]
events = [dict(r) for r in src.execute("SELECT * FROM case_events ORDER BY id")]
src.close()
print(f"source {SRC}: cases={len(cases)} case_events={len(events)}")

if DRY:
    print("DRY RUN — pass --commit to load into DD_DATABASE_URL.")
    sys.exit(0)
if not DSN or not DSN.startswith(("postgres://", "postgresql://")):
    sys.exit("set DD_DATABASE_URL to a Postgres DSN before --commit")

import psycopg
from psycopg.rows import dict_row


def _insert(cur, table, rows):
    for r in rows:
        cols = list(r.keys())
        ph = ", ".join(["%s"] * len(cols))
        cur.execute(
            f'INSERT INTO {table} ({", ".join(cols)}) OVERRIDING SYSTEM VALUE VALUES ({ph})',
            [r[c] for c in cols],
        )


with psycopg.connect(DSN, connect_timeout=15, row_factory=dict_row) as conn, conn.cursor() as cur:
    cur.execute("TRUNCATE case_events, cases RESTART IDENTITY CASCADE")
    _insert(cur, "cases", cases)
    _insert(cur, "case_events", events)
    for t in ("cases", "case_events"):  # keep identity sequences ahead of migrated ids
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)"
        )
    conn.commit()
print(f"✅ migrated: cases={len(cases)} case_events={len(events)}")
