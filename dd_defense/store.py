r"""Case + savings tracker.

DUAL-BACKEND DRAFT (port target): SQLite (default — used by the test-suite via
:memory: and for local files) and PostgreSQL (Supabase, selected only when a
Postgres DSN is given explicitly or via DD_DATABASE_URL). The SQLite path is
byte-for-byte the original behaviour, so the existing tests are unaffected; the
Postgres support is additive.

Every audit can be saved as a CASE and tracked to resolution. This is what makes
the value provable: it records what the carrier was billed, what the tool flagged
as disputable, and — crucially — what the carrier actually waived/credited
(`amount_recovered`).

The three money columns:
    amount_billed    what the carrier charged (invoice total)
    amount_flagged   what the tool says is in play
    amount_recovered what the carrier ACTUALLY waived/credited
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 2

STATUSES = ("drafted", "sent", "responded", "resolved", "rejected", "withdrawn")
OPEN_STATUSES = ("drafted", "sent", "responded")
CLOSED_STATUSES = ("resolved", "rejected", "withdrawn")

DEFAULT_DB = os.path.join("data", "cases.db")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def case_ref(case_id):
    """Human-facing case reference, e.g. 7 -> 'C-0007'."""
    return f"C-{int(case_id):04d}"


# ---------------------------------------------------------------------------
# Postgres support (additive). store.py is written with sqlite '?' placeholders;
# the wrapper below translates them and adapts the small slice of the
# sqlite3.Connection API the rest of this module relies on.
# ---------------------------------------------------------------------------

_PG_PREFIXES = ("postgres://", "postgresql://")


def _pg_dsn(db_path):
    """Decide whether this connect() should use Postgres; return the DSN if so.

    - An explicit Postgres DSN passed as db_path always wins.
    - ':memory:' is the test sentinel and is never routed to Postgres.
    - Otherwise, if DD_DATABASE_URL is set the app uses Postgres for ALL
      connections — so the many connect(<path>) call sites need no edits. We use
      the app-specific name (not the generic DATABASE_URL) so a stray DATABASE_URL
      in someone's shell can't silently hijack the database.
    """
    if isinstance(db_path, str) and db_path.startswith(_PG_PREFIXES):
        return db_path
    if db_path == ":memory:":
        return None
    env = os.environ.get("DD_DATABASE_URL")
    if env and env.startswith(_PG_PREFIXES):
        return env
    return None


class _PgResult:
    """Stand-in for a sqlite3 cursor: fetchone/fetchall + .lastrowid (filled from
    INSERT ... RETURNING id)."""
    __slots__ = ("_cur", "lastrowid")

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _PgConn:
    """Adapts a psycopg connection to the subset of sqlite3.Connection store.py
    uses: execute('... ? ...'), commit/rollback/close, dict-like rows, and the
    INSERT->lastrowid idiom (via RETURNING id)."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        q = sql.replace("?", "%s")
        stripped = sql.lstrip().lower()
        cur = self._conn.cursor()
        if stripped.startswith("insert") and "returning" not in stripped:
            cur.execute(q.rstrip().rstrip(";") + " RETURNING id", params)
            row = cur.fetchone()
            return _PgResult(cur, lastrowid=(row["id"] if row else None))
        cur.execute(q, params)
        return _PgResult(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    # mirror sqlite3's context-manager semantics (commit on success, rollback on
    # error; does NOT close) in case any caller uses `with connect() as conn:`
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        return False

    # row_factory assignment is a no-op for PG (rows are already dict-like)
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, value):
        pass


_PG_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS cases (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, client TEXT,
        invoice_number TEXT, carrier TEXT, importer TEXT,
        currency TEXT DEFAULT 'USD',
        amount_billed DOUBLE PRECISION DEFAULT 0,
        amount_obligation_eliminated DOUBLE PRECISION DEFAULT 0,
        amount_disputable DOUBLE PRECISION DEFAULT 0,
        amount_flagged DOUBLE PRECISION DEFAULT 0,
        amount_recovered DOUBLE PRECISION DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'drafted',
        sent_at TEXT, resolved_at TEXT, notes TEXT DEFAULT '',
        report_json TEXT, letter_text TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS case_events (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        case_id BIGINT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status)",
    "CREATE INDEX IF NOT EXISTS idx_cases_client ON cases(client)",
    "CREATE INDEX IF NOT EXISTS idx_events_case ON case_events(case_id)",
)


def _connect_pg(dsn):
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(dsn, connect_timeout=15, row_factory=dict_row)
    wrapped = _PgConn(conn)
    for stmt in _PG_SCHEMA:
        wrapped.execute(stmt)
    wrapped.commit()
    return wrapped


# ---------------------------------------------------------------------------
# connection / schema
# ---------------------------------------------------------------------------


def connect(db_path=DEFAULT_DB):
    """Open (creating parent dirs + schema if needed) and return a connection.
    Routes to Postgres when db_path is a DSN (or DD_DATABASE_URL is set and
    db_path is the default); otherwise the original SQLite behaviour."""
    dsn = _pg_dsn(db_path)
    if dsn:
        return _connect_pg(dsn)

    # ---- SQLite path (unchanged from the original) ----
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL = readers don't block the writer and vice-versa; safer under concurrent
    # web requests. Not available for in-memory DBs.
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
    init_db(conn)
    _migrate(conn)
    return conn


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn):
    """Idempotent, additive migrations for DBs created by an older version.
    SQLite only — the Postgres schema is created complete in _connect_pg."""
    cols = _columns(conn, "cases")
    if "client" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN client TEXT")
    # safe now that the column is guaranteed to exist (fresh or migrated)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_client ON cases(client)")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cases (
            id                            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at                    TEXT NOT NULL,
            updated_at                    TEXT NOT NULL,
            client                        TEXT,
            invoice_number                TEXT,
            carrier                       TEXT,
            importer                      TEXT,
            currency                      TEXT DEFAULT 'USD',
            amount_billed                 REAL DEFAULT 0,
            amount_obligation_eliminated  REAL DEFAULT 0,
            amount_disputable             REAL DEFAULT 0,
            amount_flagged                REAL DEFAULT 0,
            amount_recovered              REAL DEFAULT 0,
            status                        TEXT NOT NULL DEFAULT 'drafted',
            sent_at                       TEXT,
            resolved_at                   TEXT,
            notes                         TEXT DEFAULT '',
            report_json                   TEXT,
            letter_text                   TEXT
        );

        CREATE TABLE IF NOT EXISTS case_events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id  INTEGER NOT NULL,
            at       TEXT NOT NULL,
            event    TEXT NOT NULL,
            detail   TEXT DEFAULT '',
            FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
        CREATE INDEX IF NOT EXISTS idx_events_case ON case_events(case_id);
        """
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _log(conn, case_id, event, detail=""):
    conn.execute(
        "INSERT INTO case_events (case_id, at, event, detail) VALUES (?,?,?,?)",
        (case_id, _now(), event, detail),
    )


# ---------------------------------------------------------------------------
# create / read
# ---------------------------------------------------------------------------


def flagged_amount(report):
    """The headline 'in play' figure. If a facial defect eliminates the whole
    obligation, the full invoice is in play; otherwise the per-line disputable
    sum. max() avoids double-counting the overlap."""
    oblig = report.get("amount_obligation_eliminated") or 0
    disp = report.get("amount_disputable") or 0
    return round(max(oblig, disp), 2)


def create_case(conn, report, letter="", importer=None, status="drafted", client=None):
    """Persist an audit report as a new case. `report` is AuditReport.to_dict().
    `client` is the account this work belongs to (e.g. the forwarder). Returns id."""
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = _now()
    billed = report.get("total_amount_due") or 0
    flagged = flagged_amount(report)
    cur = conn.execute(
        """INSERT INTO cases
           (created_at, updated_at, client, invoice_number, carrier, importer, currency,
            amount_billed, amount_obligation_eliminated, amount_disputable,
            amount_flagged, amount_recovered, status, report_json, letter_text)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now, now, client,
         report.get("invoice_number"), report.get("issuing_party"),
         importer or report.get("billed_party"), report.get("currency") or "USD",
         billed, report.get("amount_obligation_eliminated") or 0,
         report.get("amount_disputable") or 0, flagged, 0.0, status,
         json.dumps(report), letter),
    )
    case_id = cur.lastrowid
    _log(conn, case_id, "created",
         f"client={client or '-'} billed={billed} flagged={flagged} status={status}")
    conn.commit()
    return case_id


def get_case(conn, case_id):
    row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def get_events(conn, case_id):
    rows = conn.execute(
        "SELECT * FROM case_events WHERE case_id = ? ORDER BY id", (case_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_cases(conn, status=None, client=None, limit=None):
    sql = "SELECT * FROM cases"
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if client:
        where.append("client = ?")
        params.append(client)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def clients(conn):
    """Distinct client names that have at least one case (excludes NULL)."""
    rows = conn.execute(
        "SELECT DISTINCT client FROM cases WHERE client IS NOT NULL AND client <> '' "
        "ORDER BY client").fetchall()
    return [r["client"] for r in rows]


def set_client(conn, case_id, client):
    if not get_case(conn, case_id):
        raise KeyError(f"no such case: {case_id}")
    conn.execute("UPDATE cases SET client = ?, updated_at = ? WHERE id = ?",
                 (client, _now(), case_id))
    _log(conn, case_id, "client_set", client or "")
    conn.commit()


_CSV_COLUMNS = (
    "id", "created_at", "client", "invoice_number", "carrier", "importer",
    "currency", "amount_billed", "amount_flagged", "amount_recovered",
    "status", "sent_at", "resolved_at",
)


def export_csv(conn, client=None):
    """Return all cases as a CSV string (optionally filtered to one client).
    Excludes the large report_json/letter_text blobs — this is the savings ledger."""
    import csv
    import io
    rows = list_cases(conn, client=client)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_CSV_COLUMNS)
    for c in reversed(rows):  # chronological in the export
        w.writerow([c.get(col, "") for col in _CSV_COLUMNS])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def set_status(conn, case_id, status, note=""):
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status} (allowed: {', '.join(STATUSES)})")
    if not get_case(conn, case_id):
        raise KeyError(f"no such case: {case_id}")
    now = _now()
    extra = {}
    if status == "sent":
        extra["sent_at"] = now
    if status in CLOSED_STATUSES:
        extra["resolved_at"] = now
    sets = "status = ?, updated_at = ?" + "".join(f", {k} = ?" for k in extra)
    params = [status, now, *extra.values(), case_id]
    conn.execute(f"UPDATE cases SET {sets} WHERE id = ?", params)
    _log(conn, case_id, "status_changed", status + (f": {note}" if note else ""))
    conn.commit()


def set_recovered(conn, case_id, amount, note="", mark_resolved=True):
    """Record the amount the carrier actually waived/credited. By default also
    marks the case resolved (the normal closing action)."""
    if not get_case(conn, case_id):
        raise KeyError(f"no such case: {case_id}")
    amount = round(float(amount), 2)
    now = _now()
    conn.execute(
        "UPDATE cases SET amount_recovered = ?, updated_at = ? WHERE id = ?",
        (amount, now, case_id),
    )
    _log(conn, case_id, "recovered_set", f"{amount}" + (f": {note}" if note else ""))
    if mark_resolved:
        status = "resolved" if amount > 0 else "rejected"
        set_status(conn, case_id, status, note)
    else:
        conn.commit()


def add_note(conn, case_id, note):
    case = get_case(conn, case_id)
    if not case:
        raise KeyError(f"no such case: {case_id}")
    now = _now()
    combined = (case.get("notes") or "")
    combined = (combined + "\n" if combined else "") + f"[{now}] {note}"
    conn.execute("UPDATE cases SET notes = ?, updated_at = ? WHERE id = ?",
                 (combined, now, case_id))
    _log(conn, case_id, "note", note)
    conn.commit()


# ---------------------------------------------------------------------------
# portfolio rollup (the dashboard numbers)
# ---------------------------------------------------------------------------


def portfolio_stats(conn, fee_rate=0.20, client=None):
    """Aggregate the book into the figures that answer 'did we save money, and
    what's my cut?'. fee_rate models a contingency fee on recovered $."""
    rows = list_cases(conn, client=client)
    by_status = {s: 0 for s in STATUSES}
    total_billed = total_flagged = total_recovered = 0.0
    flagged_on_resolved = 0.0
    open_flagged = 0.0  # still-in-play pipeline
    for c in rows:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        total_billed += c["amount_billed"] or 0
        total_flagged += c["amount_flagged"] or 0
        total_recovered += c["amount_recovered"] or 0
        if c["status"] in CLOSED_STATUSES:
            flagged_on_resolved += c["amount_flagged"] or 0
        if c["status"] in OPEN_STATUSES:
            open_flagged += c["amount_flagged"] or 0
    recovery_rate = (total_recovered / flagged_on_resolved) if flagged_on_resolved else 0.0
    return {
        "total_cases": len(rows),
        "by_status": by_status,
        "open_cases": sum(by_status[s] for s in OPEN_STATUSES),
        "closed_cases": sum(by_status[s] for s in CLOSED_STATUSES),
        "total_billed": round(total_billed, 2),
        "total_flagged": round(total_flagged, 2),
        "total_recovered": round(total_recovered, 2),
        "open_flagged_pipeline": round(open_flagged, 2),
        "recovery_rate": round(recovery_rate, 4),   # recovered / flagged on closed cases
        "fee_rate": fee_rate,
        "estimated_fee": round(total_recovered * fee_rate, 2),
    }
