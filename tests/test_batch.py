"""Tests for batch triage + summarize (no LLM, no network).

We test the triage/summary logic directly (the part the future agent reuses),
without invoking the real extractor — process_invoice's extraction is exercised
elsewhere; here we feed parsed invoices straight into the decision logic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense import batch
from dd_defense.audit import run_audit
from dd_defense.schema import ParsedInvoice


def make_inv(containers=("HLBU9250073",), conf=0.99, total=3600.0, missing=()):
    d = {
        "invoice_number": {"value": "INV-1", "present_on_invoice": True, "confidence": conf},
        "invoice_date": {"value": "2024-01-01", "present_on_invoice": True, "confidence": conf},
        "issuing_party": {"value": "Carrier Co", "present_on_invoice": True, "confidence": conf},
        "billed_party": {"value": "Importer Co", "present_on_invoice": True, "confidence": conf},
        "container_numbers": {"value": list(containers), "present_on_invoice": True, "confidence": conf},
        "total_amount_due": {"value": total, "present_on_invoice": True, "confidence": conf},
        "free_time_end": {"value": "2024-01-05", "present_on_invoice": True, "confidence": conf},
        "line_items": [{"container_number": containers[0], "start_date": "2024-01-10",
                        "end_date": "2024-01-15", "days_charged": 5, "rate_applied": 100, "line_total": 500}],
    }
    for m in missing:
        d.pop(m, None)
    return ParsedInvoice.from_dict(d)


class TestTriage(unittest.TestCase):
    def test_clean_high_confidence_auto_clears(self):
        inv = make_inv(conf=0.99, total=3600)
        decision, reasons = batch._triage(inv, run_audit(inv))
        self.assertEqual(decision, "auto_clear", reasons)

    def test_bad_container_forces_review(self):
        inv = make_inv(containers=("HLBU6952073",))  # the real misread
        decision, reasons = batch._triage(inv, run_audit(inv))
        self.assertEqual(decision, "needs_review")
        self.assertTrue(any("check digit" in r for r in reasons))

    def test_low_confidence_forces_review(self):
        inv = make_inv(conf=0.4)
        decision, reasons = batch._triage(inv, run_audit(inv))
        self.assertEqual(decision, "needs_review")
        self.assertTrue(any("confidence" in r for r in reasons))

    def test_high_value_forces_review(self):
        inv = make_inv(total=80_000.0)
        decision, reasons = batch._triage(inv, run_audit(inv))
        self.assertEqual(decision, "needs_review")
        self.assertTrue(any("high invoice value" in r for r in reasons))


class TestFindAndSummarize(unittest.TestCase):
    def test_find_invoices_filters_extensions(self):
        import tempfile
        d = tempfile.mkdtemp()
        for n in ("a.pdf", "b.PNG", "c.jpg", "notes.txt", ".hidden.pdf"):
            open(os.path.join(d, n), "w").close()
        found = [os.path.basename(p) for p in batch.find_invoices(d)]
        self.assertEqual(set(found), {"a.pdf", "b.PNG", "c.jpg"})

    def test_summarize_counts(self):
        results = [
            {"ok": True, "decision": "auto_clear", "amount_flagged": 1000, "amount_billed": 1200, "currency": "USD"},
            {"ok": True, "decision": "needs_review", "amount_flagged": 500, "amount_billed": 600, "currency": "USD"},
            {"ok": False, "decision": "needs_review", "amount_flagged": 0, "amount_billed": 0, "currency": "USD"},
        ]
        s = batch.summarize(results)
        self.assertEqual(s["total_files"], 3)
        self.assertEqual(s["processed"], 2)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["auto_clear"], 1)
        self.assertEqual(s["needs_review"], 1)
        self.assertEqual(s["total_flagged"], 1500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
