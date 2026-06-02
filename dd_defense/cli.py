"""Command-line entry point.

  # Audit a real invoice file (needs ANTHROPIC_API_KEY + extraction extras):
  python -m dd_defense.cli audit --invoice path/to/invoice.pdf --evidence evidence.json

  # Audit an already-parsed invoice JSON (no LLM, no key) — for dev + rule iteration:
  python -m dd_defense.cli audit --parsed samples/sample_parsed_invoice.json \
      --evidence samples/sample_evidence.json

Writes report.md, report.json, letter.md, and parsed_invoice.json to --out (./out).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .audit import run_audit
from .letter import draft_letter, polish
from .report import render_markdown
from .schema import Evidence


def _load_dotenv(path=".env"):
    """Minimal .env loader (no dependency): copy KEY=VALUE lines into the
    environment. Lets you keep the API key in a gitignored .env file instead of
    exporting it every terminal session.

    A real value in .env overrides an env var that is *unset or blank* (some
    shells/harnesses pre-export ANTHROPIC_API_KEY="" which would otherwise
    shadow the key), but never overrides a non-empty exported value."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if not key or not val:
                    continue
                existing = os.environ.get(key)
                if existing is None or existing.strip() == "":
                    os.environ[key] = val
    except FileNotFoundError:
        pass


def _load_evidence(path):
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return Evidence.from_dict(json.load(fh))


def cmd_audit(args):
    # 1. obtain the parsed invoice
    if args.parsed:
        from .extract import load_parsed
        inv = load_parsed(args.parsed)
    elif args.invoice:
        from .extract import extract_from_file
        inv = extract_from_file(args.invoice, model=args.extract_model)
    else:
        print("error: provide --invoice <file> or --parsed <json>", file=sys.stderr)
        return 2

    evidence = _load_evidence(args.evidence)

    # 2. audit
    report = run_audit(inv, evidence)

    # 3. render outputs
    md = render_markdown(report, inv)
    letter = draft_letter(report, inv)
    if args.polish:
        letter = polish(letter)

    os.makedirs(args.out, exist_ok=True)
    _write(os.path.join(args.out, "report.md"), md)
    _write(os.path.join(args.out, "report.json"), json.dumps(report.to_dict(), indent=2))
    _write(os.path.join(args.out, "letter.md"), letter)
    _write(os.path.join(args.out, "parsed_invoice.json"), json.dumps(inv.to_dict(), indent=2))

    # 4. optionally save as a tracked case
    saved_id = None
    if getattr(args, "save", False):
        from . import store
        conn = store.connect(args.db)
        saved_id = store.create_case(conn, report.to_dict(), letter=letter,
                                     client=getattr(args, "client", None))
        conn.close()

    # 5. console summary
    c = report.currency or "USD"
    print(f"\nAudited invoice {report.invoice_number or '(unknown)'} — {report.issuing_party or ''}")
    if report.amount_obligation_eliminated:
        print(f"  obligation may be eliminated: {c} {report.amount_obligation_eliminated:,.2f} (full invoice)")
    print(f"  additional disputable:        {c} {report.amount_disputable:,.2f}")
    fails = sum(1 for f in report.findings if f.status == "fail")
    print(f"  {fails} disputable finding(s), {report.needs_evidence_count} pending evidence")
    print(f"  -> {args.out}/report.md, letter.md, report.json")
    if saved_id:
        from .store import case_ref
        print(f"  saved as case {case_ref(saved_id)} in {args.db}")
    print()
    return 0


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _money(v, c):
    return f"{c} {v:,.2f}" if v is not None else "n/a"


def cmd_cases(args):
    """List tracked cases + a portfolio rollup."""
    from . import store
    conn = store.connect(args.db)
    client = getattr(args, "client", None)
    rows = store.list_cases(conn, status=args.status, client=client)
    s = store.portfolio_stats(conn, fee_rate=args.fee_rate, client=client)
    conn.close()
    if not rows:
        scope = f" for client '{client}'" if client else ""
        print(f"No cases yet{scope} in {args.db}. Run: audit --invoice ... --save")
        return 0
    print(f"\n{'CASE':8} {'STATUS':10} {'INVOICE':18} {'CARRIER':22} {'BILLED':>12} {'FLAGGED':>12} {'RECOVERED':>12}")
    print("-" * 98)
    for cse in rows:
        print(f"{store.case_ref(cse['id']):8} {cse['status']:10} "
              f"{(cse['invoice_number'] or '')[:18]:18} {(cse['carrier'] or '')[:22]:22} "
              f"{cse['amount_billed']:>12,.2f} {cse['amount_flagged']:>12,.2f} {cse['amount_recovered']:>12,.2f}")
    print("-" * 98)
    print(f"\nPortfolio ({s['total_cases']} cases — {s['open_cases']} open, {s['closed_cases']} closed):")
    print(f"  total billed:      {s['total_billed']:>14,.2f}")
    print(f"  total flagged:     {s['total_flagged']:>14,.2f}  (in play across all cases)")
    print(f"  open pipeline:     {s['open_flagged_pipeline']:>14,.2f}  (flagged on still-open cases)")
    print(f"  TOTAL RECOVERED:   {s['total_recovered']:>14,.2f}  (carrier waived/credited)")
    print(f"  recovery rate:     {s['recovery_rate']*100:>13.1f}%  (recovered / flagged on closed cases)")
    print(f"  est. fee @ {s['fee_rate']*100:.0f}%:    {s['estimated_fee']:>14,.2f}\n")
    return 0


def cmd_case(args):
    """Show one case in detail, with its event history."""
    from . import store
    conn = store.connect(args.db)
    cse = store.get_case(conn, args.id)
    if not cse:
        conn.close()
        print(f"No case {args.id} in {args.db}", file=sys.stderr)
        return 1
    cur = cse["currency"] or "USD"
    print(f"\n{store.case_ref(cse['id'])}  [{cse['status']}]")
    print(f"  invoice:   {cse['invoice_number']}   carrier: {cse['carrier']}")
    print(f"  importer:  {cse['importer']}")
    print(f"  billed:    {_money(cse['amount_billed'], cur)}")
    print(f"  flagged:   {_money(cse['amount_flagged'], cur)}")
    print(f"  recovered: {_money(cse['amount_recovered'], cur)}")
    if cse["notes"]:
        print("  notes:\n    " + cse["notes"].replace("\n", "\n    "))
    print("  history:")
    for e in store.get_events(conn, cse["id"]):
        print(f"    {e['at']}  {e['event']}  {e['detail']}")
    conn.close()
    print()
    return 0


def cmd_status(args):
    from . import store
    conn = store.connect(args.db)
    try:
        store.set_status(conn, args.id, args.new_status, note=args.note or "")
    except (KeyError, ValueError) as ex:
        conn.close()
        print(f"error: {ex}", file=sys.stderr)
        return 1
    conn.close()
    from .store import case_ref
    print(f"{case_ref(args.id)} -> {args.new_status}")
    return 0


def cmd_export(args):
    """Export the case ledger as CSV."""
    from . import store
    conn = store.connect(args.db)
    csv_text = store.export_csv(conn, client=getattr(args, "client", None))
    conn.close()
    if args.out:
        _write(args.out, csv_text)
        n = csv_text.count("\n") - 1
        print(f"wrote {max(n, 0)} case(s) -> {args.out}")
    else:
        sys.stdout.write(csv_text)
    return 0


def cmd_recover(args):
    """Record what the carrier actually waived/credited (closes the case)."""
    from . import store
    conn = store.connect(args.db)
    try:
        store.set_recovered(conn, args.id, args.amount, note=args.note or "")
    except KeyError as ex:
        conn.close()
        print(f"error: {ex}", file=sys.stderr)
        return 1
    cse = store.get_case(conn, args.id)
    conn.close()
    print(f"{store.case_ref(args.id)}: recovered {args.amount:,.2f} -> {cse['status']}")
    return 0


def main(argv=None):
    _load_dotenv()  # pick up ANTHROPIC_API_KEY from a local .env if present
    from .store import DEFAULT_DB
    _STATUSES = ("drafted", "sent", "responded", "resolved", "rejected", "withdrawn")
    p = argparse.ArgumentParser(prog="dd_defense", description="Audit D&D invoices and track dispute outcomes.")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("audit", help="audit one invoice and draft a dispute letter")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--invoice", help="path to invoice PDF or image (uses the LLM extractor)")
    src.add_argument("--parsed", help="path to an already-parsed invoice JSON (no LLM)")
    a.add_argument("--evidence", help="path to optional evidence JSON (Layer 2 substantive checks)")
    a.add_argument("--out", default="out", help="output directory (default: out)")
    a.add_argument("--extract-model", default="claude-haiku-4-5", help="model for extraction")
    a.add_argument("--polish", action="store_true", help="LLM-polish the letter tone (needs API key)")
    a.add_argument("--save", action="store_true", help="save this audit as a tracked case")
    a.add_argument("--client", help="client/account this case belongs to (e.g. the forwarder)")
    a.add_argument("--db", default=DEFAULT_DB, help=f"case database path (default: {DEFAULT_DB})")
    a.set_defaults(func=cmd_audit)

    lc = sub.add_parser("cases", help="list tracked cases + portfolio savings rollup")
    lc.add_argument("--status", choices=_STATUSES)
    lc.add_argument("--client", help="filter to one client/account")
    lc.add_argument("--fee-rate", type=float, default=0.20, help="contingency fee rate for the estimate (default 0.20)")
    lc.add_argument("--db", default=DEFAULT_DB)
    lc.set_defaults(func=cmd_cases)

    ex = sub.add_parser("export", help="export the case ledger as CSV (to stdout or a file)")
    ex.add_argument("--client", help="filter to one client/account")
    ex.add_argument("--out", help="write CSV here (default: stdout)")
    ex.add_argument("--db", default=DEFAULT_DB)
    ex.set_defaults(func=cmd_export)

    sc = sub.add_parser("case", help="show one case in detail")
    sc.add_argument("id", type=int)
    sc.add_argument("--db", default=DEFAULT_DB)
    sc.set_defaults(func=cmd_case)

    st = sub.add_parser("status", help="change a case's status")
    st.add_argument("id", type=int)
    st.add_argument("new_status", choices=_STATUSES)
    st.add_argument("--note", default="")
    st.add_argument("--db", default=DEFAULT_DB)
    st.set_defaults(func=cmd_status)

    rec = sub.add_parser("recover", help="record the amount the carrier waived/credited (closes the case)")
    rec.add_argument("id", type=int)
    rec.add_argument("amount", type=float)
    rec.add_argument("--note", default="")
    rec.add_argument("--db", default=DEFAULT_DB)
    rec.set_defaults(func=cmd_recover)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
