"""The audit engine: run the ruleset over (invoice, evidence) -> AuditReport.

Deterministic and LLM-free. Every rule produces a Finding (including passes, so
the report can show what was checked). Summary dollar figures:
  * amount_obligation_eliminated — the whole invoice total IF any
    `obligation_eliminated` rule fails (a missing required element or a late
    invoice puts the entire charge in question under the rule).
  * amount_disputable — sum of per-line amounts implicated by other failed rules
    (math, closures, etc.). Indicative; lines can overlap across rules.
"""
from __future__ import annotations

from .rules import RULESET
from .schema import AuditReport, CheckResult, Finding
from .util import to_float


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _render(template, detail, inv):
    ctx = dict(detail)
    ctx.setdefault("invoice_date", inv.invoice_date.value or "")
    return template.format_map(_SafeDict(ctx))


def _finding_text(rule, res, inv):
    if res.status == "fail":
        return _render(rule.dispute_ground, res.detail, inv)
    if res.status == "review":
        if rule.category == "required_element":
            return (f"Low extraction confidence for required element "
                    f"‘{res.detail.get('label', '')}’ "
                    f"(confidence {res.detail.get('confidence', '?')}). Manually verify whether it is "
                    f"present before relying on or waiving this ground.")
        return _render(rule.review_ground or rule.dispute_ground, res.detail, inv)
    if res.status == "needs_evidence":
        if rule.needs_evidence_ground:
            return rule.needs_evidence_ground
        if rule.evidence_required:
            return "Potential ground pending evidence. To confirm, provide: " + "; ".join(rule.evidence_required) + "."
        return "Potential ground pending evidence."
    return ""


def run_audit(inv, evidence=None, ruleset=None):
    ruleset = ruleset if ruleset is not None else RULESET
    findings = []
    for rule in ruleset:
        if rule.applies_when and not rule.applies_when(inv, evidence):
            continue
        try:
            res = rule.check(inv, evidence, {}) if rule.check else CheckResult("not_applicable")
        except Exception as ex:  # a buggy rule must not crash the whole audit
            res = CheckResult("review", detail={"error": f"check raised: {ex}"})
        findings.append(Finding(
            rule_id=rule.id,
            title=rule.title,
            citation=rule.citation,
            layer=rule.layer,
            category=rule.category,
            severity=rule.severity,
            status=res.status,
            affected_containers=res.affected_containers,
            dispute_ground_text=_finding_text(rule, res, inv),
            evidence_needed=list(rule.evidence_required) if res.status == "needs_evidence" else [],
            amount_implicated=res.amount_implicated,
        ))

    total = to_float(inv.total_amount_due.value)
    obligation_eliminated = any(
        f.severity == "obligation_eliminated" and f.status == "fail" for f in findings)
    amount_oblig = total if (obligation_eliminated and total is not None) else 0.0
    amount_disputable = round(sum(
        f.amount_implicated for f in findings
        if f.status == "fail" and f.severity != "obligation_eliminated"), 2)
    needs_evidence_count = sum(1 for f in findings if f.status == "needs_evidence")

    return AuditReport(
        invoice_number=inv.invoice_number.value,
        issuing_party=inv.issuing_party.value,
        billed_party=inv.billed_party.value,
        currency=inv.currency.value or "USD",
        total_amount_due=total,
        findings=findings,
        amount_obligation_eliminated=round(amount_oblig, 2),
        amount_disputable=amount_disputable,
        needs_evidence_count=needs_evidence_count,
        note=("Automated analysis for the importer's review. Not legal advice; the importer files "
              "any dispute. Verify each ground and citation before relying on it."),
    )
