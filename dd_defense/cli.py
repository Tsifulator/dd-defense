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
    environment if not already set. Lets you keep the API key in a gitignored
    .env file instead of exporting it every terminal session."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
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

    # 4. console summary
    c = report.currency or "USD"
    print(f"\nAudited invoice {report.invoice_number or '(unknown)'} — {report.issuing_party or ''}")
    if report.amount_obligation_eliminated:
        print(f"  obligation may be eliminated: {c} {report.amount_obligation_eliminated:,.2f} (full invoice)")
    print(f"  additional disputable:        {c} {report.amount_disputable:,.2f}")
    fails = sum(1 for f in report.findings if f.status == "fail")
    print(f"  {fails} disputable finding(s), {report.needs_evidence_count} pending evidence")
    print(f"  -> {args.out}/report.md, letter.md, report.json\n")
    return 0


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv=None):
    _load_dotenv()  # pick up ANTHROPIC_API_KEY from a local .env if present
    p = argparse.ArgumentParser(prog="dd_defense", description="Audit a D&D invoice against the FMC ruleset.")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("audit", help="audit one invoice and draft a dispute letter")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--invoice", help="path to invoice PDF or image (uses the LLM extractor)")
    src.add_argument("--parsed", help="path to an already-parsed invoice JSON (no LLM)")
    a.add_argument("--evidence", help="path to optional evidence JSON (Layer 2 substantive checks)")
    a.add_argument("--out", default="out", help="output directory (default: out)")
    a.add_argument("--extract-model", default="claude-haiku-4-5", help="model for extraction")
    a.add_argument("--polish", action="store_true", help="LLM-polish the letter tone (needs API key)")
    a.set_defaults(func=cmd_audit)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
