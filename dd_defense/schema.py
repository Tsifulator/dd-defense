"""Data schema for a parsed D&D invoice, the ruleset, evidence, and audit output.

Stdlib-only (dataclasses). The one non-obvious decision: every extracted element
is a `Field` carrying {value, present_on_invoice, confidence, source_text}, which
separates two things that must never be confused:

  * present_on_invoice == False -> the element is MISSING (a dispute ground).
  * present_on_invoice == True but low confidence -> the extractor is UNSURE
    (a human-verify flag, NOT a dispute ground).

Conflating them would manufacture false dispute grounds — the fastest way to lose
credibility with a carrier. So the audit reads `present_on_invoice` for compliance
and `confidence` only for advisory "please verify" notes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Parsed invoice
# ---------------------------------------------------------------------------


@dataclass
class Field:
    value: Any = None
    present_on_invoice: bool = False
    confidence: float = 0.0
    source_text: str = ""

    def has_value(self):
        return self.value not in (None, "", [], {})


def _mk_field(raw):
    """Build a Field from JSON: a full {value,...} dict, a bare value (treated as
    present + confident), or None (treated as absent)."""
    if isinstance(raw, dict) and "value" in raw:
        return Field(
            value=raw.get("value"),
            present_on_invoice=raw.get("present_on_invoice", True),
            confidence=float(raw.get("confidence", 1.0)),
            source_text=raw.get("source_text", ""),
        )
    if raw is None:
        return Field(value=None, present_on_invoice=False, confidence=0.0)
    return Field(value=raw, present_on_invoice=True, confidence=1.0)


@dataclass
class LineItem:
    """One charge row (raw values as printed; the audit parses them)."""
    container_number: Optional[str] = None
    charge_type: Optional[str] = None       # demurrage | detention
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days_charged: Optional[float] = None
    rate_applied: Optional[float] = None
    line_total: Optional[float] = None
    source_text: str = ""

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d.get(k) for k in (
            "container_number", "charge_type", "start_date", "end_date",
            "days_charged", "rate_applied", "line_total", "source_text",
        ) if d.get(k) is not None})


# The single-value fields of a parsed invoice (all are `Field`s).
PARSED_FIELD_NAMES = (
    "invoice_number", "invoice_date", "due_date", "issuing_party",
    "billed_party", "currency", "bl_numbers", "container_numbers",
    "port_of_discharge", "basis_for_liability", "charge_type",
    "free_time_allowed_days", "free_time_start", "free_time_end",
    "charge_last_incurred", "rate_rule_reference", "per_diem_rates",
    "dispute_contact", "mitigation_process", "stmt_fmc_consistent",
    "stmt_no_fault", "total_amount_due",
)


@dataclass
class ParsedInvoice:
    invoice_number: Field = field(default_factory=Field)
    invoice_date: Field = field(default_factory=Field)
    due_date: Field = field(default_factory=Field)
    issuing_party: Field = field(default_factory=Field)          # carrier / NVOCC
    billed_party: Field = field(default_factory=Field)           # the importer
    currency: Field = field(default_factory=Field)
    bl_numbers: Field = field(default_factory=Field)
    container_numbers: Field = field(default_factory=Field)
    port_of_discharge: Field = field(default_factory=Field)
    basis_for_liability: Field = field(default_factory=Field)
    charge_type: Field = field(default_factory=Field)            # demurrage | detention | mixed
    free_time_allowed_days: Field = field(default_factory=Field)
    free_time_start: Field = field(default_factory=Field)
    free_time_end: Field = field(default_factory=Field)
    charge_last_incurred: Field = field(default_factory=Field)   # if stated; else derived from lines
    rate_rule_reference: Field = field(default_factory=Field)
    per_diem_rates: Field = field(default_factory=Field)
    dispute_contact: Field = field(default_factory=Field)
    mitigation_process: Field = field(default_factory=Field)
    stmt_fmc_consistent: Field = field(default_factory=Field)
    stmt_no_fault: Field = field(default_factory=Field)
    total_amount_due: Field = field(default_factory=Field)
    line_items: list = field(default_factory=list)
    raw_text: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, d):
        obj = cls()
        for name in PARSED_FIELD_NAMES:
            setattr(obj, name, _mk_field(d.get(name)))
        obj.line_items = [LineItem.from_dict(li) for li in d.get("line_items", [])]
        obj.raw_text = d.get("raw_text", "")
        obj.notes = d.get("notes", "")
        return obj

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Evidence sidecar (Layer 2 substantive checks)
# ---------------------------------------------------------------------------


@dataclass
class ContainerEvidence:
    container_number: str
    last_free_day: Optional[str] = None
    available_for_pickup: Optional[str] = None   # when the container became retrievable
    returned_at: Optional[str] = None
    picked_up_at: Optional[str] = None


@dataclass
class Closure:
    location: str = ""
    start: str = ""
    end: str = ""
    reason: str = ""


@dataclass
class GovernmentHold:
    container_number: str = ""
    start: str = ""
    end: str = ""
    reason: str = ""


@dataclass
class Evidence:
    """Optional facts the invoice does not carry, supplied by the importer so the
    substantive (incentive-principle) checks can reach a conclusion instead of
    only emitting `needs_evidence`."""
    containers: list = field(default_factory=list)
    closures: list = field(default_factory=list)
    no_appointment_dates: list = field(default_factory=list)
    government_holds: list = field(default_factory=list)
    tariff_rates: dict = field(default_factory=dict)          # rule_reference -> rate
    free_time_tolls_holidays: bool = False                   # tariff excludes weekends/holidays?
    notes: str = ""

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(
            containers=[ContainerEvidence(**c) for c in d.get("containers", [])],
            closures=[Closure(**c) for c in d.get("closures", [])],
            no_appointment_dates=list(d.get("no_appointment_dates", [])),
            government_holds=[GovernmentHold(**c) for c in d.get("government_holds", [])],
            tariff_rates=dict(d.get("tariff_rates", {})),
            free_time_tolls_holidays=bool(d.get("free_time_tolls_holidays", False)),
            notes=d.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Rules, check results, findings, report
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A configurable audit rule. Content (id, citation, severity, text, evidence
    list) is data you can edit; `check` holds the small predicate."""
    id: str
    title: str
    citation: str
    layer: str          # facial | substantive
    category: str       # required_element | timing | math | consistency | incentive
    severity: str       # obligation_eliminated | disputable | review
    dispute_ground: str                          # template used when the check fails
    evidence_required: list = field(default_factory=list)
    review_ground: Optional[str] = None          # template for "review" status
    needs_evidence_ground: Optional[str] = None  # template for "needs_evidence" status
    check: Optional[Callable] = None             # (invoice, evidence, ctx) -> CheckResult
    applies_when: Optional[Callable] = None      # (invoice, evidence) -> bool


@dataclass
class CheckResult:
    status: str = "pass"   # pass | fail | needs_evidence | review | not_applicable
    affected_containers: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    amount_implicated: float = 0.0


@dataclass
class Finding:
    rule_id: str
    title: str
    citation: str
    layer: str
    category: str
    severity: str
    status: str
    affected_containers: list
    dispute_ground_text: str
    evidence_needed: list
    amount_implicated: float

    def to_dict(self):
        return asdict(self)


@dataclass
class AuditReport:
    invoice_number: Optional[str]
    issuing_party: Optional[str]
    billed_party: Optional[str]
    currency: Optional[str]
    total_amount_due: Optional[float]
    findings: list
    amount_obligation_eliminated: float
    amount_disputable: float
    needs_evidence_count: int
    note: str = ""

    def to_dict(self):
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        return d
