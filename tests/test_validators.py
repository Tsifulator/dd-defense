"""Tests for ISO 6346 container-number validation (no LLM, no network).

These lock in the real-world bug we caught: the extractor misread HLBU9250073 as
HLBU6952073, and the check digit catches it deterministically."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense.audit import run_audit
from dd_defense.schema import ParsedInvoice
from dd_defense.validators import (
    container_check_digit, is_valid_container, validate_containers)


class TestCheckDigit(unittest.TestCase):
    def test_known_valid_numbers(self):
        # real container numbers from FMC docket 26-04 (Hapag-Lloyd)
        self.assertTrue(is_valid_container("HLBU9250073"))
        self.assertTrue(is_valid_container("HLBU9693533"))

    def test_catches_the_real_misread(self):
        # the actual error the extractor made on the scanned invoice
        self.assertTrue(is_valid_container("HLBU9250073"))   # truth
        self.assertFalse(is_valid_container("HLBU6952073"))  # what it extracted

    def test_check_digit_value(self):
        self.assertEqual(container_check_digit("HLBU9250073"), 3)
        self.assertEqual(container_check_digit("MSCU1234566"), 6)

    def test_format_rejects(self):
        for bad in ("", "ABC123", "MSCU12345", "1234567890A", "msc!!!"):
            self.assertFalse(is_valid_container(bad))

    def test_lowercase_normalised(self):
        self.assertTrue(is_valid_container("hlbu9250073"))

    def test_validate_list_reports_problems(self):
        problems = validate_containers(["HLBU9250073", "HLBU6952073", "NOTACONTAINER"])
        # only the misread + the malformed one are problems
        vals = {p["value"] for p in problems}
        self.assertIn("HLBU6952073", vals)
        self.assertIn("NOTACONTAINER", vals)
        self.assertNotIn("HLBU9250073", vals)
        # the misread is flagged with a clear reason (we do NOT fabricate a "correct" number)
        mis = [p for p in problems if p["value"] == "HLBU6952073"][0]
        self.assertIn("check digit", mis["reason"])
        self.assertNotIn("suggestion", mis)  # honest: we can't know the true number

    def test_all_valid_returns_empty(self):
        self.assertEqual(validate_containers(["HLBU9250073", "MSCU1234566"]), [])


class TestContainerRuleIntegration(unittest.TestCase):
    def _invoice(self, containers):
        return ParsedInvoice.from_dict({
            "invoice_number": "T-1", "issuing_party": "X", "billed_party": "Y",
            "container_numbers": {"value": containers, "present_on_invoice": True, "confidence": 0.99},
            "line_items": [{"container_number": c} for c in containers],
        })

    def test_rule_flags_misread_as_review(self):
        inv = self._invoice(["HLBU6952073"])  # misread
        f = {x.rule_id: x for x in run_audit(inv).findings}
        self.assertEqual(f["CONTAINER_CHECK_DIGIT"].status, "review")
        self.assertIn("HLBU6952073", f["CONTAINER_CHECK_DIGIT"].affected_containers)
        self.assertIn("check digit", f["CONTAINER_CHECK_DIGIT"].dispute_ground_text)

    def test_rule_passes_valid(self):
        inv = self._invoice(["HLBU9250073", "HLBU9693533"])
        f = {x.rule_id: x for x in run_audit(inv).findings}
        self.assertEqual(f["CONTAINER_CHECK_DIGIT"].status, "pass")

    def test_misread_is_review_not_dispute(self):
        # critical: a misread must NOT become a dispute ground (would embarrass the client)
        inv = self._invoice(["HLBU6952073"])
        r = run_audit(inv)
        f = {x.rule_id: x for x in r.findings}
        self.assertNotEqual(f["CONTAINER_CHECK_DIGIT"].status, "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
