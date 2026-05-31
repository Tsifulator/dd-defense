"""The configurable FMC D&D ruleset (STARTER — refine against the regulation text).

Two editable surfaces, and most changes touch only the first:

  1. REQUIRED_ELEMENTS (data) — the FMC "required billing element" checklist.
     Add/remove/reword entries; each becomes a presence check automatically.
     This is where you encode your authoritative version of the 13 elements.
  2. _EXPLICIT_RULES (data + small predicates) — timing, math, and substantive
     (incentive-principle) checks that need real logic.

Two layers:
  * facial      — provable from the invoice alone (missing elements, >30-day late
                  issuance, arithmetic/consistency errors).
  * substantive — the FMC incentive principle (46 CFR § 545.5); needs evidence the
                  invoice does not carry. Absent that evidence a check returns
                  `needs_evidence` and lists exactly what to gather.

LEGAL CITATIONS (section headings confirmed via eCFR + Cornell LII, May 2026):
  * § 541.6 — Contents of invoice: the required minimum data elements. Omitting a
    required element eliminates the billed party's obligation to pay the charge.
  * § 541.7 — Issuance: the invoice must be issued within 30 calendar days of the
    date the charge was last incurred; if not, the billed party need not pay.
  * § 545.5 — The incentive-principle Interpretive Rule (substantive grounds).

⚠ CURRENCY (verify before relying): In 2025 a U.S. Court of Appeals SET ASIDE
§ 541.4 — the provision restricting WHICH party may be billed — and per FMC notices
it was removed from the CFR (eff. Dec 29 2025). The rest of Part 541, including
§ 541.6 and § 541.7, is understood to remain in effect, but confirm the current
scope (the court touched the "properly issued invoice" framework) with counsel.

STILL TO VERIFY against the primary text: the exact paragraph letter within § 541.6
for each element, the canonical "13-element" grouping, and whether the two
"certification statement" elements survived into the final rule. Starter wording —
confirm with counsel. Not legal advice.
"""
from __future__ import annotations

from datetime import timedelta

from .calendars import is_holiday, is_weekend, nonworking_days_in_range, overlap_days
from .schema import CheckResult, Rule
from .util import date_range, parse_date, to_float

# ---------------------------------------------------------------------------
# Layer 1a — required invoice content (presence checks, generated from data)
# ---------------------------------------------------------------------------

# Required-content elements are in § 541.6 ("Contents of invoice"), which the rule
# groups into: identifying info, timing info, rate info, and contact/dispute info.
# The exact paragraph letter within § 541.6 and the canonical "13-element" grouping
# still need confirmation against the regulation text. The two stmt_* elements are
# flagged VERIFY — they may not have survived into the final rule.
REQUIRED_ELEMENTS = [
    # -- identifying information --
    {"field": "bl_numbers", "label": "Bill of lading number(s)", "citation": "46 CFR § 541.6"},
    {"field": "container_numbers", "label": "Container number(s)", "citation": "46 CFR § 541.6"},
    {"field": "port_of_discharge", "label": "Port(s) of discharge", "citation": "46 CFR § 541.6"},
    {"field": "basis_for_liability", "label": "Basis the billed party is the proper party liable", "citation": "46 CFR § 541.6"},
    # -- timing information --
    {"field": "invoice_date", "label": "Invoice date", "citation": "46 CFR § 541.6"},
    {"field": "due_date", "label": "Invoice due date", "citation": "46 CFR § 541.6"},
    {"field": "free_time_allowed_days", "label": "Allowed free time (in days)", "citation": "46 CFR § 541.6"},
    {"field": "free_time_start", "label": "Free time start date", "citation": "46 CFR § 541.6"},
    {"field": "free_time_end", "label": "Free time end date", "citation": "46 CFR § 541.6"},
    # -- rate information --
    {"field": "rate_rule_reference", "label": "Applicable D&D rule the rate is based on", "citation": "46 CFR § 541.6"},
    {"field": "per_diem_rates", "label": "Applicable per-diem rate(s)", "citation": "46 CFR § 541.6"},
    {"field": "total_amount_due", "label": "Total amount due", "citation": "46 CFR § 541.6"},
    # -- contact / dispute information --
    {"field": "dispute_contact", "label": "Contact for billing disputes / fee mitigation", "citation": "46 CFR § 541.6"},
    {"field": "mitigation_process", "label": "How (and timeframe) to request mitigation/refund/waiver", "citation": "46 CFR § 541.6"},
    # -- certification statements (VERIFY these survived into the final rule) --
    {"field": "stmt_fmc_consistent", "label": "Statement charges are consistent with FMC regulations", "citation": "46 CFR § 541.6 — VERIFY retained"},
    {"field": "stmt_no_fault", "label": "Statement billing party did not cause the charges", "citation": "46 CFR § 541.6 — VERIFY retained"},
]

_PRESENCE_TEMPLATE = (
    "The invoice omits the FMC-required billing element “{label}”. Under the FMC "
    "Final Rule on Demurrage and Detention Billing Requirements (46 CFR Part 541), a "
    "demurrage or detention invoice that fails to include a required billing element does "
    "not comply with the rule, and the billed party's obligation to pay this charge may be "
    "eliminated. ({citation})"
)


def _make_presence_rule(elem):
    field_name, label, citation = elem["field"], elem["label"], elem["citation"]

    def check(inv, ev, ctx, _f=field_name, _l=label, _c=citation):
        f = getattr(inv, _f, None)
        if f is None or not f.present_on_invoice or not f.has_value():
            return CheckResult("fail", detail={"label": _l, "citation": _c})
        if f.confidence < 0.5:
            return CheckResult("review", detail={"label": _l, "citation": _c, "confidence": f.confidence})
        return CheckResult("pass")

    return Rule(
        id="REQ_" + field_name.upper(),
        title="Required element — " + label,
        citation=citation,
        layer="facial",
        category="required_element",
        severity="obligation_eliminated",
        dispute_ground=_PRESENCE_TEMPLATE,
        check=check,
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _window(li):
    return parse_date(li.start_date), parse_date(li.end_date)


def _charge_last_incurred(inv):
    """Prefer a stated 'charge last incurred' date; else the latest line-item end
    date; else the free-time end date."""
    f = getattr(inv, "charge_last_incurred", None)
    if f and f.has_value():
        d = parse_date(f.value)
        if d:
            return d
    ends = [parse_date(li.end_date) for li in inv.line_items]
    ends = [d for d in ends if d]
    if ends:
        return max(ends)
    return parse_date(inv.free_time_end.value)


# ---------------------------------------------------------------------------
# Layer 1b — timing
# ---------------------------------------------------------------------------


def _check_timing(inv, ev, ctx):
    inv_date = parse_date(inv.invoice_date.value)
    last = _charge_last_incurred(inv)
    if not inv_date or not last:
        return CheckResult("review", detail={"reason": "invoice date or last-charge date not parseable"})
    days = (inv_date - last).days
    if days > 30:
        return CheckResult("fail", detail={
            "days": days, "last_incurred": last.isoformat(), "invoice_date": inv_date.isoformat()})
    return CheckResult("pass", detail={"days": days})


TIMING_30_DAY = Rule(
    id="TIMING_30_DAY",
    title="Invoice issued within 30 days of last charge",
    citation="46 CFR § 541.7 (issuance within 30 days)",
    layer="facial",
    category="timing",
    severity="obligation_eliminated",
    dispute_ground=(
        "This invoice was issued on {invoice_date}, which is {days} days after the charge was "
        "last incurred on {last_incurred}. The FMC rule (46 CFR § 541.7) requires "
        "demurrage and detention invoices to be issued within 30 calendar days of the date the "
        "charge was last incurred. Because this invoice was issued after that deadline, the "
        "billed party's obligation to pay is eliminated."),
    check=_check_timing,
)


# ---------------------------------------------------------------------------
# Layer 1c — arithmetic / consistency
# ---------------------------------------------------------------------------


def _check_math_line(inv, ev, ctx):
    bad, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        days, rate, total = to_float(li.days_charged), to_float(li.rate_applied), to_float(li.line_total)
        if days is None or rate is None or total is None:
            continue
        expected = round(days * rate, 2)
        if abs(expected - total) > 0.01:
            over = round(total - expected, 2)
            bad.append(f"{li.container_number}: {days} × {rate} = {expected} but billed {total}")
            if over > 0:
                implicated += over
            if li.container_number:
                conts.append(li.container_number)
    if bad:
        return CheckResult("fail", affected_containers=conts,
                           detail={"lines": "; ".join(bad), "count": len(bad)},
                           amount_implicated=round(implicated, 2))
    return CheckResult("pass")


MATH_LINE = Rule(
    id="MATH_LINE",
    title="Line items compute (days × rate = total)",
    citation="Arithmetic / invoice accuracy",
    layer="facial",
    category="math",
    severity="disputable",
    dispute_ground=("{count} line item(s) contain arithmetic errors in which days × per-diem rate "
                    "does not equal the amount billed ({lines}). The billed amount is overstated and "
                    "should be corrected."),
    check=_check_math_line,
)


def _check_math_total(inv, ev, ctx):
    total = to_float(inv.total_amount_due.value)
    lines = [to_float(li.line_total) for li in inv.line_items if to_float(li.line_total) is not None]
    if total is None or not lines:
        return CheckResult("not_applicable")
    s = round(sum(lines), 2)
    if abs(s - total) > 0.01:
        return CheckResult("fail", detail={"sum_lines": s, "total": total, "diff": round(abs(s - total), 2)},
                           amount_implicated=round(abs(s - total), 2))
    return CheckResult("pass")


MATH_TOTAL = Rule(
    id="MATH_TOTAL",
    title="Line items reconcile to the stated total",
    citation="Arithmetic / invoice accuracy",
    layer="facial",
    category="consistency",
    severity="disputable",
    dispute_ground=("The invoice line items sum to {sum_lines}, but the stated total amount due is "
                    "{total}. The total does not reconcile with the line items and should be corrected."),
    check=_check_math_total,
)


def _check_charge_during_free_time(inv, ev, ctx):
    fe = parse_date(inv.free_time_end.value)
    if not fe:
        return CheckResult("not_applicable")
    bad, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        s, e = _window(li)
        if s and s <= fe:
            overlap_end = min(e, fe) if e else fe
            d = (overlap_end - s).days + 1
            bad.append(f"{li.container_number} from {li.start_date} ({d} day(s) within free time)")
            if li.container_number:
                conts.append(li.container_number)
            rate = to_float(li.rate_applied)
            if rate:
                implicated += d * rate
    if bad:
        return CheckResult("fail", affected_containers=conts,
                           detail={"lines": "; ".join(bad), "free_end": fe.isoformat()},
                           amount_implicated=round(implicated, 2))
    return CheckResult("pass")


CHARGE_DURING_FREE_TIME = Rule(
    id="CHARGE_DURING_FREE_TIME",
    title="No charges accrue during free time",
    citation="Free-time terms",
    layer="facial",
    category="consistency",
    severity="disputable",
    dispute_ground=("One or more charges began on or before the end of the allowed free time "
                    "({free_end}): {lines}. Charges may only accrue after free time expires; amounts "
                    "billed for days within the free-time window should be removed."),
    check=_check_charge_during_free_time,
)


def _check_duplicates(inv, ev, ctx):
    seen, dups = {}, []
    for li in inv.line_items:
        key = (li.container_number, li.start_date, li.end_date, li.charge_type)
        seen[key] = seen.get(key, 0) + 1
    for k, c in seen.items():
        if c > 1:
            dups.append(f"{k[0]} {k[3] or ''} {k[1]}–{k[2]} (×{c})")
    if dups:
        return CheckResult("review", detail={"lines": "; ".join(dups)})
    return CheckResult("pass")


DUPLICATE_LINES = Rule(
    id="DUPLICATE_LINES",
    title="No duplicate charge lines",
    citation="Invoice accuracy",
    layer="facial",
    category="consistency",
    severity="review",
    dispute_ground=("The invoice appears to contain duplicate charge line(s) for the same container "
                    "and date range ({lines}). Duplicated charges should be removed."),
    review_ground=("The invoice appears to contain duplicate charge line(s) for the same container "
                   "and date range ({lines}). Verify these are not billed more than once."),
    check=_check_duplicates,
)


# ---------------------------------------------------------------------------
# Layer 2 — substantive (incentive principle); needs evidence
# ---------------------------------------------------------------------------


def _check_holiday_weekend(inv, ev, ctx):
    flagged, conts, implicated, total_days = [], [], 0.0, 0
    for li in inv.line_items:
        s, e = _window(li)
        if not s or not e:
            continue
        nw = nonworking_days_in_range(s, e)
        if nw:
            total_days += len(nw)
            if li.container_number:
                conts.append(li.container_number)
            rate = to_float(li.rate_applied)
            if rate:
                implicated += len(nw) * rate
            flagged.append(f"{li.container_number}: {len(nw)} day(s)")
    if not flagged:
        return CheckResult("pass")
    tolls = bool(ev and ev.free_time_tolls_holidays)
    return CheckResult(
        "fail" if tolls else "review",
        affected_containers=conts,
        detail={"lines": "; ".join(flagged), "total_days": total_days},
        amount_implicated=round(implicated, 2) if tolls else 0.0,
    )


HOLIDAY_WEEKEND = Rule(
    id="HOLIDAY_WEEKEND",
    title="Charges exclude weekends/holidays where required",
    citation="Free-time terms / incentive principle",
    layer="substantive",
    category="incentive",
    severity="disputable",
    evidence_required=["the tariff/contract free-time terms stating whether weekends/holidays toll free time"],
    dispute_ground=("The charge window includes non-working days (weekends / US federal holidays) that "
                    "do not accrue charges under the applicable free-time terms: {lines} "
                    "({total_days} day(s)). These amounts should be removed."),
    review_ground=("The charge window includes non-working days (weekends / US federal holidays): "
                   "{lines} ({total_days} day(s)). If the applicable tariff excludes these days from "
                   "chargeable time, the corresponding amounts are disputable."),
    check=_check_holiday_weekend,
)


def _check_closure(inv, ev, ctx):
    if not ev or not ev.closures:
        return CheckResult("needs_evidence")
    hits, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        s, e = _window(li)
        if not s or not e:
            continue
        for c in ev.closures:
            cs, ce = parse_date(c.start), parse_date(c.end)
            if not cs or not ce:
                continue
            d = overlap_days(s, e, cs, ce)
            if d > 0:
                hits.append(f"{li.container_number}: {d} day(s) during {c.location or 'terminal'} "
                            f"closure {c.start}–{c.end}" + (f" ({c.reason})" if c.reason else ""))
                if li.container_number:
                    conts.append(li.container_number)
                rate = to_float(li.rate_applied)
                if rate:
                    implicated += d * rate
    if not hits:
        return CheckResult("pass")
    return CheckResult("fail", affected_containers=conts,
                       detail={"lines": "; ".join(hits)}, amount_implicated=round(implicated, 2))


CLOSURE = Rule(
    id="CLOSURE",
    title="No charges during terminal/port closures",
    citation="46 CFR § 545.5 (incentive principle)",
    layer="substantive",
    category="incentive",
    severity="disputable",
    evidence_required=["terminal or port closure dates (gate closures, weather, strikes) overlapping the charge window"],
    dispute_ground=("Charges accrued during documented terminal/port closures when the container could "
                    "not be retrieved or returned ({lines}). Under the FMC incentive principle "
                    "(46 CFR § 545.5), such charges do not serve their intended purpose and should be removed."),
    needs_evidence_ground=("Under the FMC incentive principle (46 CFR § 545.5), demurrage and detention "
                           "should not accrue for days when the container could not be retrieved or "
                           "returned because the terminal or port was closed for reasons outside the "
                           "importer's control. Provide closure records to substantiate this ground."),
    check=_check_closure,
)


def _check_no_appointment(inv, ev, ctx):
    if not ev or not ev.no_appointment_dates:
        return CheckResult("needs_evidence")
    nad = {d for d in (parse_date(x) for x in ev.no_appointment_dates) if d}
    hits, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        s, e = _window(li)
        if not s or not e:
            continue
        days = [d for d in date_range(s, e) if d in nad]
        if days:
            hits.append(f"{li.container_number}: {len(days)} day(s)")
            if li.container_number:
                conts.append(li.container_number)
            rate = to_float(li.rate_applied)
            if rate:
                implicated += len(days) * rate
    if not hits:
        return CheckResult("pass")
    return CheckResult("fail", affected_containers=conts,
                       detail={"lines": "; ".join(hits)}, amount_implicated=round(implicated, 2))


NO_APPOINTMENT = Rule(
    id="NO_APPOINTMENT",
    title="No charges on days without an available appointment",
    citation="46 CFR § 545.5 (incentive principle)",
    layer="substantive",
    category="incentive",
    severity="disputable",
    evidence_required=["dates on which no return/pickup appointment was available at the terminal"],
    dispute_ground=("Charges accrued on days when no terminal appointment was available to move the "
                    "container ({lines}). Under the FMC incentive principle (46 CFR § 545.5), these "
                    "charges should be removed."),
    needs_evidence_ground=("Under the FMC incentive principle, charges should not accrue on days when the "
                           "importer or its trucker could not obtain a terminal appointment. Provide "
                           "appointment records (e.g., terminal screenshots/notices) to substantiate this ground."),
    check=_check_no_appointment,
)


def _check_availability(inv, ev, ctx):
    if not ev or (not ev.containers and not ev.government_holds):
        return CheckResult("needs_evidence")
    cont_ev = {c.container_number: c for c in ev.containers}
    hits, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        s, e = _window(li)
        if not s or not e:
            continue
        ce = cont_ev.get(li.container_number)
        if ce and ce.available_for_pickup:
            avail = parse_date(ce.available_for_pickup)
            if avail and s < avail:
                gap_end = min(e, avail - timedelta(days=1))
                d = (gap_end - s).days + 1
                if d > 0:
                    hits.append(f"{li.container_number}: {d} day(s) before available {ce.available_for_pickup}")
                    conts.append(li.container_number)
                    rate = to_float(li.rate_applied)
                    if rate:
                        implicated += d * rate
        for h in ev.government_holds:
            if h.container_number == li.container_number:
                hs, he = parse_date(h.start), parse_date(h.end)
                if hs and he:
                    d = overlap_days(s, e, hs, he)
                    if d > 0:
                        hits.append(f"{li.container_number}: {d} day(s) under hold {h.start}–{h.end}"
                                    + (f" ({h.reason})" if h.reason else ""))
                        conts.append(li.container_number)
                        rate = to_float(li.rate_applied)
                        if rate:
                            implicated += d * rate
    if not hits:
        return CheckResult("pass")
    return CheckResult("fail", affected_containers=sorted(set(conts)),
                       detail={"lines": "; ".join(hits)}, amount_implicated=round(implicated, 2))


INCENTIVE_NO_FAULT = Rule(
    id="INCENTIVE_NO_FAULT",
    title="No charges accrue before the importer could act",
    citation="46 CFR § 545.5 (incentive principle)",
    layer="substantive",
    category="incentive",
    severity="disputable",
    evidence_required=["container availability and return timestamps, plus any customs/government hold notices"],
    dispute_ground=("Charges accrued while the container could not be retrieved or returned through no "
                    "fault of the importer ({lines}). The importer could not act during this period, so "
                    "under the FMC incentive principle (46 CFR § 545.5) these charges should be removed."),
    needs_evidence_ground=("The FMC incentive principle (46 CFR § 545.5) requires that demurrage and "
                           "detention incentivize cargo movement. Charges that accrue when the importer "
                           "could not act — container not yet available, under customs hold, or no chassis "
                           "available — may be improper. Provide container availability and return "
                           "timestamps to substantiate this ground."),
    check=_check_availability,
)


def _check_rate_vs_tariff(inv, ev, ctx):
    if not ev or not ev.tariff_rates:
        return CheckResult("needs_evidence")
    ref = inv.rate_rule_reference.value
    if ref and ref in ev.tariff_rates:
        tariff = to_float(ev.tariff_rates[ref])
    elif "default" in ev.tariff_rates:
        tariff = to_float(ev.tariff_rates["default"])
    elif len(ev.tariff_rates) == 1:
        tariff = to_float(next(iter(ev.tariff_rates.values())))
    else:
        return CheckResult("needs_evidence")
    if tariff is None:
        return CheckResult("needs_evidence")
    hits, conts, implicated = [], [], 0.0
    for li in inv.line_items:
        rate, days = to_float(li.rate_applied), to_float(li.days_charged)
        if rate and days and rate > tariff:
            over = round((rate - tariff) * days, 2)
            implicated += over
            conts.append(li.container_number)
            hits.append(f"{li.container_number}: billed {rate}/day vs tariff {tariff}/day (overcharge {over})")
    if not hits:
        return CheckResult("pass")
    return CheckResult("fail", affected_containers=conts,
                       detail={"lines": "; ".join(hits)}, amount_implicated=round(implicated, 2))


RATE_VS_TARIFF = Rule(
    id="RATE_VS_TARIFF",
    title="Billed rate does not exceed the governing tariff",
    citation="Tariff / service contract",
    layer="substantive",
    category="consistency",
    severity="disputable",
    evidence_required=["the published tariff or service-contract per-diem rate for this charge (key by rate basis, or 'default')"],
    dispute_ground=("The per-diem rate billed exceeds the applicable tariff/contract rate ({lines}). "
                    "The overcharge should be corrected."),
    needs_evidence_ground=("The billed per-diem rate should match the rate in the applicable tariff or "
                           "service contract. Provide the governing rate to verify the billed amount."),
    check=_check_rate_vs_tariff,
)


# ---------------------------------------------------------------------------
# Assembled ruleset
# ---------------------------------------------------------------------------

_EXPLICIT_RULES = [
    TIMING_30_DAY, MATH_LINE, MATH_TOTAL, CHARGE_DURING_FREE_TIME, DUPLICATE_LINES,
    HOLIDAY_WEEKEND, CLOSURE, NO_APPOINTMENT, INCENTIVE_NO_FAULT, RATE_VS_TARIFF,
]


def build_ruleset():
    """Build the full ruleset: generated presence rules + explicit rules."""
    return [_make_presence_rule(e) for e in REQUIRED_ELEMENTS] + _EXPLICIT_RULES


RULESET = build_ruleset()
