"""Render an AuditReport to a human-readable Markdown report (JSON via to_dict)."""
from __future__ import annotations

from .util import fmt_money

_STATUS_LABEL = {
    "fail": "DISPUTABLE",
    "review": "REVIEW",
    "needs_evidence": "NEEDS EVIDENCE",
    "pass": "OK",
    "not_applicable": "N/A",
}

_SEVERITY_NOTE = {
    "obligation_eliminated": "obligation to pay may be eliminated",
    "disputable": "disputable charge",
    "review": "manual review",
}


def _finding_block(f, currency):
    head = f"- **[{_STATUS_LABEL.get(f.status, f.status.upper())}] {f.title}**  \n"
    meta = f"  _{f.citation} · {_SEVERITY_NOTE.get(f.severity, f.severity)}_"
    if f.affected_containers:
        meta += f" · containers: {', '.join(str(c) for c in f.affected_containers)}"
    if f.amount_implicated:
        meta += f" · ~{fmt_money(f.amount_implicated, currency)} implicated"
    body = f"\n\n  {f.dispute_ground_text}" if f.dispute_ground_text else ""
    return head + meta + body


def render_markdown(report, inv=None):
    c = report.currency or "USD"
    out = []
    out.append("# D&D Invoice Audit Report")
    out.append("")
    out.append(f"- **Invoice:** {report.invoice_number or 'n/a'}")
    out.append(f"- **Carrier / issuer:** {report.issuing_party or 'n/a'}")
    out.append(f"- **Billed party (importer):** {report.billed_party or 'n/a'}")
    out.append(f"- **Total billed:** {fmt_money(report.total_amount_due, c)}")
    out.append("")

    fails = [f for f in report.findings if f.status == "fail"]
    reviews = [f for f in report.findings if f.status == "review"]
    needs = [f for f in report.findings if f.status == "needs_evidence"]
    passes = [f for f in report.findings if f.status in ("pass", "not_applicable")]

    out.append("## Bottom line")
    out.append("")
    if report.amount_obligation_eliminated:
        out.append(f"- **{fmt_money(report.amount_obligation_eliminated, c)}** — the full invoice: at "
                   f"least one *required-element / timing* defect was found, which under the FMC rule can "
                   f"eliminate the obligation to pay the entire charge.")
    out.append(f"- **{fmt_money(report.amount_disputable, c)}** — additional amount implicated by "
               f"arithmetic and substantive grounds (indicative; lines may overlap).")
    out.append(f"- **{len(fails)}** disputable finding(s), **{len(reviews)}** to review, "
               f"**{report.needs_evidence_count}** pending evidence.")
    out.append("")

    def section(title, items):
        out.append(f"## {title}")
        out.append("")
        if not items:
            out.append("_None._")
            out.append("")
            return
        for f in items:
            out.append(_finding_block(f, c))
            out.append("")

    section("Facial defects — strongest grounds (provable from the invoice)",
            [f for f in fails if f.layer == "facial"])
    section("Substantive grounds (incentive principle)",
            [f for f in fails if f.layer == "substantive"])
    section("To review (low confidence or possible duplicates)", reviews)

    out.append("## Evidence to gather (to confirm pending grounds)")
    out.append("")
    if needs:
        seen = set()
        for f in needs:
            for ev in f.evidence_needed:
                if ev not in seen:
                    seen.add(ev)
                    out.append(f"- [ ] {ev}  \n  _supports: {f.title}_")
        out.append("")
    else:
        out.append("_None outstanding._")
        out.append("")

    out.append("## Checks passed")
    out.append("")
    out.append(", ".join(f.title for f in passes) if passes else "_None._")
    out.append("")

    out.append("---")
    out.append("")
    out.append(f"_{report.note}_")
    out.append("")
    return "\n".join(out)
