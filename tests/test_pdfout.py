"""Tests for PDF export (skipped if reportlab is not installed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import reportlab  # noqa: F401
    from dd_defense import pdfout
    _HAVE = True
except Exception:
    _HAVE = False

_REPORT = {
    "invoice_number": "PBLU-1", "issuing_party": "Pacific Blue Lines",
    "billed_party": "Sunrise Produce", "currency": "USD",
    "total_amount_due": 3730.0, "amount_obligation_eliminated": 3730.0,
    "amount_disputable": 430.0,
    "findings": [
        {"status": "fail", "title": "Invoice issued late", "citation": "46 CFR 541.7", "amount_implicated": 0},
        {"status": "fail", "title": "Line math error", "citation": "accuracy", "amount_implicated": 100.0},
        {"status": "pass", "title": "BL present", "citation": "541.6", "amount_implicated": 0},
    ],
}
_LETTER = "[YOUR LETTERHEAD]\n[DATE]\n\nRe: Dispute of PBLU-1\n\nTo whom it may concern,\n\nWe dispute.\n\n---\nDRAFT for your review."


@unittest.skipUnless(_HAVE, "reportlab not installed")
class TestPdfOut(unittest.TestCase):
    def test_letter_pdf_is_valid_pdf(self):
        data = pdfout.letter_pdf_bytes(_REPORT, _LETTER)
        self.assertTrue(data[:5] == b"%PDF-")
        self.assertGreater(len(data), 800)

    def test_report_pdf_is_valid_pdf(self):
        data = pdfout.report_pdf_bytes(_REPORT, _LETTER)
        self.assertTrue(data[:5] == b"%PDF-")
        self.assertGreater(len(data), 1000)

    def test_handles_empty_letter(self):
        data = pdfout.letter_pdf_bytes(_REPORT, "")
        self.assertTrue(data[:5] == b"%PDF-")

    def test_handles_special_chars(self):
        # ampersand/angle brackets must not break the PDF build
        data = pdfout.letter_pdf_bytes(_REPORT, "A & B < C > D\nLine two")
        self.assertTrue(data[:5] == b"%PDF-")


if __name__ == "__main__":
    unittest.main(verbosity=2)
