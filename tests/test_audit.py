"""Deterministic tests for the audit engine — no LLM, no network.

Run: python -m unittest discover -s tests   (from the dd-defense/ directory)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense.audit import run_audit
from dd_defense.calendars import parse_date, us_federal_holidays
from dd_defense.schema import Evidence, ParsedInvoice

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def load_invoice():
    with open(os.path.join(SAMPLES, "sample_parsed_invoice.json")) as fh:
        return ParsedInvoice.from_dict(json.load(fh))


def load_evidence():
    with open(os.path.join(SAMPLES, "sample_evidence.json")) as fh:
        return Evidence.from_dict(json.load(fh))


def by_id(report):
    return {f.rule_id: f for f in report.findings}


class TestCalendars(unittest.TestCase):
    def test_labor_day_2024(self):
        self.assertIn(parse_date("2024-09-02"), us_federal_holidays(2024))

    def test_parse_us_format(self):
        self.assertEqual(parse_date("09/02/2024").isoformat(), "2024-09-02")


class TestFacialChecks(unittest.TestCase):
    def setUp(self):
        self.f = by_id(run_audit(load_invoice(), None))

    def test_missing_required_elements_fail(self):
        for rid in ("REQ_BASIS_FOR_LIABILITY", "REQ_MITIGATION_PROCESS", "REQ_STMT_NO_FAULT"):
            self.assertEqual(self.f[rid].status, "fail", rid)

    def test_present_element_passes(self):
        self.assertEqual(self.f["REQ_BL_NUMBERS"].status, "pass")

    def test_low_confidence_is_review_not_fail(self):
        # port_of_discharge is present but confidence 0.4 -> review, NOT a false "missing" ground
        self.assertEqual(self.f["REQ_PORT_OF_DISCHARGE"].status, "review")

    def test_late_issuance_fail(self):
        self.assertEqual(self.f["TIMING_30_DAY"].status, "fail")
        self.assertEqual(self.f["TIMING_30_DAY"].severity, "obligation_eliminated")

    def test_line_math_error(self):
        self.assertEqual(self.f["MATH_LINE"].status, "fail")
        self.assertAlmostEqual(self.f["MATH_LINE"].amount_implicated, 150.0)

    def test_total_mismatch(self):
        self.assertEqual(self.f["MATH_TOTAL"].status, "fail")
        self.assertAlmostEqual(self.f["MATH_TOTAL"].amount_implicated, 150.0)

    def test_charge_during_free_time(self):
        self.assertEqual(self.f["CHARGE_DURING_FREE_TIME"].status, "fail")


class TestSubstantiveNeedsEvidence(unittest.TestCase):
    def test_without_evidence_pending(self):
        f = by_id(run_audit(load_invoice(), None))
        for rid in ("CLOSURE", "NO_APPOINTMENT", "INCENTIVE_NO_FAULT", "RATE_VS_TARIFF"):
            self.assertEqual(f[rid].status, "needs_evidence", rid)
            self.assertTrue(f[rid].evidence_needed, rid)


class TestSubstantiveWithEvidence(unittest.TestCase):
    def setUp(self):
        self.f = by_id(run_audit(load_invoice(), load_evidence()))

    def test_closure_fail(self):
        self.assertEqual(self.f["CLOSURE"].status, "fail")
        self.assertAlmostEqual(self.f["CLOSURE"].amount_implicated, 4 * 150.0)  # 2 days x 2 containers

    def test_no_appointment_fail(self):
        self.assertEqual(self.f["NO_APPOINTMENT"].status, "fail")

    def test_availability_gap_and_hold(self):
        self.assertEqual(self.f["INCENTIVE_NO_FAULT"].status, "fail")
        # MSCU1234566: 08-31..09-04 = 5 days; MSCU7654329 hold 09-07..09-09 = 3 days -> 8 x 150
        self.assertAlmostEqual(self.f["INCENTIVE_NO_FAULT"].amount_implicated, 8 * 150.0)

    def test_rate_overcharge(self):
        self.assertEqual(self.f["RATE_VS_TARIFF"].status, "fail")
        self.assertAlmostEqual(self.f["RATE_VS_TARIFF"].amount_implicated, 30 * (10 + 14))

    def test_holiday_weekend_becomes_fail_when_tolled(self):
        self.assertEqual(self.f["HOLIDAY_WEEKEND"].status, "fail")


class TestSummary(unittest.TestCase):
    def test_obligation_eliminated_is_full_total(self):
        r = run_audit(load_invoice(), load_evidence())
        self.assertAlmostEqual(r.amount_obligation_eliminated, 3600.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
