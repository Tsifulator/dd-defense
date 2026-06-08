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

    # 3b. optional PDF export of the letter + full report
    pdf_written = False
    if getattr(args, "pdf", False):
        try:
            from . import pdfout
            rd = report.to_dict()
            with open(os.path.join(args.out, "letter.pdf"), "wb") as fh:
                fh.write(pdfout.letter_pdf_bytes(rd, letter))
            with open(os.path.join(args.out, "report.pdf"), "wb") as fh:
                fh.write(pdfout.report_pdf_bytes(rd, letter))
            pdf_written = True
        except ImportError:
            print("  (PDF export skipped: pip install reportlab)", file=sys.stderr)

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
    if pdf_written:
        print(f"  -> {args.out}/letter.pdf, report.pdf")
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


def cmd_batch(args):
    """Audit every invoice in a folder, save each as a case, print a triage summary."""
    from . import batch

    if not os.path.isdir(args.folder):
        print(f"error: not a folder: {args.folder}", file=sys.stderr)
        return 2
    files = batch.find_invoices(args.folder)
    if not files:
        print(f"No invoices (.pdf/.png/.jpg) found in {args.folder}")
        return 0
    evidence = _load_evidence(args.evidence)

    print(f"\nProcessing {len(files)} invoice(s) from {args.folder} ...\n")

    def progress(i, n, r):
        mark = "ok " if r["ok"] else "ERR"
        tag = {"auto_clear": "auto-clear  ", "needs_review": "NEEDS REVIEW"}.get(r["decision"], r["decision"])
        ref = (store_case_ref(r["case_id"]) if r["case_id"] else "—")
        amt = f"{r['currency']} {r['amount_flagged']:,.0f}" if r["amount_flagged"] else "—"
        print(f"  [{i:>3}/{n}] {mark} {tag}  {ref:8} {amt:>14}  {r['file']}")
        if r["reasons"]:
            print(f"            ↳ {'; '.join(r['reasons'])}")
        if r["error"]:
            print(f"            ↳ error: {r['error']}")

    results, s = batch.process_folder(
        args.folder, evidence=evidence, db_path=args.db, client=args.client,
        extract_model=args.extract_model, out_dir=(args.out or None),
        save=not args.no_save, on_progress=progress)

    cur = s["currency"]
    print("\n" + "=" * 66)
    print(f"  BATCH SUMMARY — {s['processed']}/{s['total_files']} processed"
          + (f", {s['failed']} failed" if s["failed"] else ""))
    print(f"  auto-clear:   {s['auto_clear']:>4}   (clean — fast-track your review)")
    print(f"  needs review: {s['needs_review']:>4}   (look at these first)")
    print(f"  total flagged disputable: {cur} {s['total_flagged']:,.2f}")
    print(f"  total billed across batch: {cur} {s['total_billed']:,.2f}")
    print("=" * 66)
    print("  Review the flagged cases:  python -m dd_defense.cli cases"
          + (f" --client \"{args.client}\"" if args.client else ""))
    print()
    return 0


def store_case_ref(cid):
    from .store import case_ref
    return case_ref(cid)


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


def cmd_airtable_setup(args):
    """Create the Airtable base schema (Prospects/Leads/Cases)."""
    from . import airtable_setup
    try:
        airtable_setup.setup()
    except RuntimeError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


def cmd_airtable_ping(args):
    """Verify Airtable connectivity (lists 1 record)."""
    from . import airtable
    try:
        airtable.ping()
    except airtable.AirtableError as ex:
        print(f"Airtable NOT reachable: {ex}", file=sys.stderr)
        return 1
    print("Airtable reachable ✓ (base + key OK)")
    return 0


def cmd_outreach(args):
    """Draft + queue prospects from a CSV into the Airtable Prospects table."""
    from . import outreach
    try:
        prospects = outreach.load_prospects_csv(args.csv)
    except FileNotFoundError:
        print(f"error: no such CSV: {args.csv}", file=sys.stderr)
        return 1
    if not prospects:
        print("No prospects found in the CSV.")
        return 0
    print(f"Drafting + queuing {len(prospects)} prospect(s) (status: Needs Approval)...")
    try:
        result = outreach.queue_prospects(
            prospects, sender_name=args.sender_name, sender_phone=args.sender_phone,
            use_llm=args.polish, dedupe=not args.allow_duplicates)
    except Exception as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    n_created, n_skipped = len(result["created"]), len(result["skipped"])
    print(f"Queued {n_created} new draft(s) to Airtable.", end="")
    if n_skipped:
        print(f" Skipped {n_skipped} already in the queue: {', '.join(result['skipped'][:6])}"
              + ("…" if n_skipped > 6 else "") + ".")
    else:
        print()
    if n_created:
        print("Review + send them from the Prospects table.")
    return 0


def cmd_airtable_sync(args):
    """Push the local case/savings tracker into the Airtable Cases table."""
    from . import airtable_sync
    def prog(ref, created):
        print(f"  {'+' if created else '~'} {ref}")
    try:
        s = airtable_sync.sync(db_path=args.db, on_progress=prog)
    except Exception as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    print(f"\nSynced {s['total']} case(s) -> Airtable (created {s['created']}, updated {s['updated']}).")
    return 0


def main(argv=None):
    _load_dotenv()  # pick up ANTHROPIC_API_KEY (+ AIRTABLE_*) from a local .env if present
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
    a.add_argument("--pdf", action="store_true", help="also write letter.pdf + report.pdf (needs reportlab)")
    a.add_argument("--save", action="store_true", help="save this audit as a tracked case")
    a.add_argument("--client", help="client/account this case belongs to (e.g. the forwarder)")
    a.add_argument("--db", default=DEFAULT_DB, help=f"case database path (default: {DEFAULT_DB})")
    a.set_defaults(func=cmd_audit)

    b = sub.add_parser("batch", help="audit every invoice in a folder (triage + save as cases)")
    b.add_argument("folder", help="folder containing invoice PDFs/images")
    b.add_argument("--client", help="client/account these invoices belong to")
    b.add_argument("--evidence", help="optional evidence JSON applied to all invoices in the batch")
    b.add_argument("--out", help="optional dir to also write per-invoice report.json + letter.md")
    b.add_argument("--db", default=DEFAULT_DB, help=f"case database path (default: {DEFAULT_DB})")
    b.add_argument("--extract-model", default="claude-haiku-4-5", help="model for extraction")
    b.add_argument("--no-save", action="store_true", help="don't save cases (dry run)")
    b.set_defaults(func=cmd_batch)

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

    # ── Airtable operations base ────────────────────────────────────────────
    asetup = sub.add_parser("airtable-setup", help="create the Airtable base schema (Prospects/Leads/Cases)")
    asetup.set_defaults(func=cmd_airtable_setup)

    aping = sub.add_parser("airtable-ping", help="check Airtable connectivity (key + base)")
    aping.set_defaults(func=cmd_airtable_ping)

    out = sub.add_parser("outreach", help="draft + queue prospects from a CSV into Airtable (draft-only, no send)")
    out.add_argument("csv", help="CSV of prospects (company,type,contact_name,title,email,phone,url,location,containers_per_mo,source,notes)")
    out.add_argument("--sender-name", default="[Your name]", help="your name for the email signature")
    out.add_argument("--sender-phone", default="[phone]", help="your phone for the signature")
    out.add_argument("--polish", action="store_true", help="LLM-personalize each draft (needs ANTHROPIC_API_KEY)")
    out.add_argument("--allow-duplicates", action="store_true", help="don't skip companies already in the queue")
    out.set_defaults(func=cmd_outreach)

    async_ = sub.add_parser("airtable-sync", help="push the local case/savings tracker into Airtable")
    async_.add_argument("--db", default=DEFAULT_DB)
    async_.set_defaults(func=cmd_airtable_sync)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
