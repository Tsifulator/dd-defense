"""D&D invoice defense — parse a carrier demurrage/detention invoice, audit it
against a configurable FMC ruleset, and draft a dispute letter for the importer.

Analyzes and drafts only; it does not file disputes and is not legal advice.
"""
from .audit import run_audit
from .rules import RULESET, build_ruleset
from .schema import (
    AuditReport, CheckResult, Evidence, Field, Finding, LineItem,
    ParsedInvoice, Rule,
)

__version__ = "0.1.0"
__all__ = [
    "AuditReport", "CheckResult", "Evidence", "Field", "Finding", "LineItem",
    "ParsedInvoice", "Rule", "run_audit", "RULESET", "build_ruleset",
]
