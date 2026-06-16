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
    from .airtable import setup as airtable_setup
    try:
        airtable_setup.setup()
    except RuntimeError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


def cmd_airtable_ping(args):
    """Verify Airtable connectivity (lists 1 record)."""
    from .airtable import client as airtable
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
    from .airtable import sync as airtable_sync
    def prog(ref, created):
        print(f"  {'+' if created else '~'} {ref}")
    try:
        s = airtable_sync.sync(db_path=args.db, on_progress=prog)
    except Exception as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    print(f"\nSynced {s['total']} case(s) -> Airtable (created {s['created']}, updated {s['updated']}).")
    return 0


def cmd_ingest(args):
    """Autonomous intake: pull invoices from the email inbox or a folder, audit
    each, save as a case, optionally push to Airtable."""
    from . import pipeline

    evidence = _load_evidence(args.evidence)
    if getattr(args, "enrich", False) and evidence is None:
        from . import evidence_sources
        evidence = evidence_sources.build_evidence(data_dir=args.evidence_dir)
        print(f"Evidence enrichment on (from {args.evidence_dir}).")
    meta_by_path = {}
    if args.source == "inbox":
        try:
            paths, meta_by_path = pipeline.source_inbox(
                save_dir=args.save_dir, unseen_only=not args.all,
                mark_seen=not args.no_mark, limit=args.limit)
        except RuntimeError as ex:
            print(f"error: {ex}", file=sys.stderr)
            return 1
        print(f"Inbox: {len(paths)} invoice attachment(s) found.")
    else:  # folder
        if not args.folder or not os.path.isdir(args.folder):
            print("error: --folder <dir> required for source=folder", file=sys.stderr)
            return 2
        paths = pipeline.source_folder(args.folder)
        print(f"Folder: {len(paths)} invoice file(s).")

    if not paths:
        print("Nothing to ingest.")
        return 0

    def prog(r):
        if r.get("skipped"):
            print(f"  · skipped {r['file']} (already processed)")
        elif not r.get("ok"):
            print(f"  ✗ {r['file']}: {r.get('error', 'failed')}")
        else:
            from .store import case_ref
            tag = {"auto_clear": "auto-clear", "needs_review": "NEEDS REVIEW"}.get(r["decision"], r["decision"])
            amt = f"{r['currency']} {r['amount_flagged']:,.0f}" if r["amount_flagged"] else "—"
            print(f"  ✓ {case_ref(r['case_id'])} {tag:12} {amt:>12}  {r['file']}")
            if r.get("reasons"):
                print(f"      ↳ {'; '.join(r['reasons'])}")

    out = pipeline.process_paths(
        paths, db_path=args.db, client=args.client, evidence=evidence,
        push_airtable=args.airtable, extract_model=args.extract_model,
        on_progress=prog, meta_by_path=meta_by_path)
    s = out["summary"]
    print("\n" + "=" * 60)
    print(f"  INGEST: {s['processed']} audited, {s['skipped']} skipped, {s['failed']} failed")
    print(f"  auto-clear {s['auto_clear']} · needs-review {s['needs_review']}")
    print(f"  total flagged disputable: {s['total_flagged']:,.2f}")
    if s["airtable_pushed"] is not None:
        print(f"  pushed to Airtable: {s['airtable_pushed']} case(s)")
    print("=" * 60)
    return 0


def cmd_prospect_status(args):
    """Show outreach pipeline: prospect counts by status."""
    from .airtable import client as airtable
    try:
        records = airtable.list_records(airtable.TABLE_PROSPECTS)
    except airtable.AirtableError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    if not records:
        print("No prospects in Airtable.")
        return 0
    counts = {}
    for rec in records:
        status = rec.get("fields", {}).get("Status", "(no status)")
        counts[status] = counts.get(status, 0) + 1
    # Sort: Needs Approval first, then by count
    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    print(f"\nOutreach pipeline — {len(records)} prospects:\n")
    for status, n in ordered:
        bar = "█" * min(n, 40)
        print(f"  {status:18} {n:>4}  {bar}")
    print()
    # Quick stats
    with_email = sum(1 for r in records if r.get("fields", {}).get("Email"))
    with_name = sum(1 for r in records if r.get("fields", {}).get("Contact Name"))
    print(f"  with email:   {with_email}/{len(records)}")
    print(f"  with name:    {with_name}/{len(records)}")
    print()
    return 0


def cmd_send_outreach(args):
    """Send outreach emails to Approved prospects via Resend."""
    from .airtable.send import send_approved
    try:
        send_approved(
            limit=args.limit,
            dry_run=args.dry_run,
            from_addr=args.sender,
            reply_to=args.reply_to,
        )
    except RuntimeError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


def cmd_prospect_export(args):
    """Export prospects to a CSV for review (sorted by Fit Score)."""
    import csv as csvmod
    from .airtable import client as airtable
    try:
        records = airtable.list_records(airtable.TABLE_PROSPECTS)
    except airtable.AirtableError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    fields_order = ["Company", "Type", "Contact Name", "Title", "Email", "Phone",
                    "Location / Port", "Est. Containers/mo", "Fit Score", "Source",
                    "Status", "LinkedIn / URL", "Draft Subject", "Notes"]
    out = args.out
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csvmod.DictWriter(fh, fieldnames=fields_order + ["Proceed?", "Your Notes"])
        w.writeheader()
        for rec in sorted(records, key=lambda r: -(r.get("fields", {}).get("Fit Score") or 0)):
            f = rec.get("fields", {})
            row = {k: f.get(k, "") for k in fields_order}
            row["Proceed?"] = ""
            row["Your Notes"] = ""
            w.writerow(row)
    print(f"Exported {len(records)} prospects -> {out}")
    print("Open in Excel, mark 'Proceed?' column Y/N, then run: prospect-approve <csv>")
    return 0


def cmd_prospect_approve(args):
    """Read back a reviewed CSV and update Airtable statuses based on Proceed? column."""
    import csv as csvmod
    from .airtable import client as airtable
    try:
        with open(args.csv, newline="", encoding="utf-8-sig") as fh:
            rows = list(csvmod.DictReader(fh))
    except FileNotFoundError:
        print(f"error: no such file: {args.csv}", file=sys.stderr)
        return 1
    # Build a lookup of company -> proceed decision
    decisions = {}
    for row in rows:
        company = (row.get("Company") or "").strip()
        proceed = (row.get("Proceed?") or "").strip().upper()
        if company and proceed:
            decisions[company] = proceed
    if not decisions:
        print("No decisions found. Mark the 'Proceed?' column with Y or N and re-save.")
        return 0
    # Fetch current prospects and match by company name
    records = airtable.list_records(airtable.TABLE_PROSPECTS)
    approved = skipped = unchanged = 0
    for rec in records:
        f = rec.get("fields", {})
        company = f.get("Company", "")
        decision = decisions.get(company)
        if not decision:
            unchanged += 1
            continue
        if decision in ("Y", "YES", "1", "GO"):
            new_status = "Approved"
            approved += 1
        elif decision in ("N", "NO", "0", "SKIP", "X"):
            new_status = "Not a Fit"
            skipped += 1
        else:
            unchanged += 1
            continue
        if f.get("Status") != new_status:
            airtable.update_record(airtable.TABLE_PROSPECTS, rec["id"], {"Status": new_status})
            print(f"  {'✓' if new_status == 'Approved' else '✗'} {company} -> {new_status}")
    print(f"\nDone. approved={approved} not-a-fit={skipped} unchanged={unchanged}")
    return 0


def cmd_prospect_cleanup(args):
    """Fix placeholder brackets + fake phones in prospect drafts."""
    from .airtable.cleanup import run
    run(dry_run=args.dry_run)
    return 0


def cmd_evidence_scaffold(args):
    """Create the evidence_data/ folder with editable example files."""
    from . import evidence_sources
    written = evidence_sources.scaffold(data_dir=args.dir)
    if written:
        print(f"Created {len(written)} example evidence file(s) in {args.dir}:")
        for p in written:
            print(f"  {p}")
        print("Edit these with real closure/tariff data, then run: ingest ... --enrich")
    else:
        print(f"Evidence files already exist in {args.dir} (left untouched).")
    return 0


def main(argv=None):
    _load_dotenv()  # pick up ANTHROPIC_API_KEY (+ AIRTABLE_* / DD_IMAP_*) from .env
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

    ing = sub.add_parser("ingest", help="autonomous intake: audit invoices from the inbox or a folder -> Airtable")
    ing.add_argument("source", choices=("inbox", "folder"), help="where invoices come from")
    ing.add_argument("--folder", help="folder of invoices (for source=folder)")
    ing.add_argument("--client", help="tag all cases with this client (else inferred from sender domain)")
    ing.add_argument("--evidence", help="optional evidence JSON applied to all invoices")
    ing.add_argument("--enrich", action="store_true", help="auto-build evidence (holidays + local closures/tariffs) for substantive grounds")
    ing.add_argument("--evidence-dir", default="evidence_data", help="folder of operator evidence files (for --enrich)")
    ing.add_argument("--airtable", action="store_true", help="also push the resulting cases to Airtable")
    ing.add_argument("--save-dir", default="inbox_attachments", help="where to save inbox attachments")
    ing.add_argument("--all", action="store_true", help="inbox: process all emails, not just unseen")
    ing.add_argument("--no-mark", action="store_true", help="inbox: don't mark emails as seen")
    ing.add_argument("--limit", type=int, help="inbox: cap number of emails processed")
    ing.add_argument("--db", default=DEFAULT_DB)
    ing.add_argument("--extract-model", default="claude-haiku-4-5")
    ing.set_defaults(func=cmd_ingest)

    so = sub.add_parser("send-outreach", help="send outreach emails to Approved prospects via Resend")
    so.add_argument("--limit", type=int, default=10, help="max emails to send per run (default 10)")
    so.add_argument("--dry-run", action="store_true", help="preview without sending")
    so.add_argument("--sender", default="tsiflik@bc.edu", help="from address")
    so.add_argument("--reply-to", default="tsiflik@bc.edu", help="reply-to address")
    so.set_defaults(func=cmd_send_outreach)

    ps = sub.add_parser("prospect-status", help="show outreach pipeline: prospect counts by status")
    ps.set_defaults(func=cmd_prospect_status)

    pe = sub.add_parser("prospect-export", help="export prospects to CSV for review in Excel")
    pe.add_argument("--out", default=os.path.expanduser("~/workspace/exports/dnd-prospects.csv"),
                    help="output CSV path")
    pe.set_defaults(func=cmd_prospect_export)

    pa = sub.add_parser("prospect-approve", help="read back a reviewed CSV and approve/reject prospects")
    pa.add_argument("csv", help="path to your reviewed CSV with Proceed? column filled in")
    pa.set_defaults(func=cmd_prospect_approve)

    pc = sub.add_parser("prospect-cleanup", help="fix placeholder brackets + fake phones in prospect drafts")
    pc.add_argument("--dry-run", action="store_true", help="show what would change without patching Airtable")
    pc.set_defaults(func=cmd_prospect_cleanup)

    evs = sub.add_parser("evidence-scaffold", help="create editable evidence files (closures/tariffs) for --enrich")
    evs.add_argument("--dir", default="evidence_data")
    evs.set_defaults(func=cmd_evidence_scaffold)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
