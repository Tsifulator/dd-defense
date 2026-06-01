"""Tests for the case/savings tracker (in-memory SQLite; no network)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense import store


def sample_report(invoice="INV-1", billed=3600.0, oblig=3600.0, disp=900.0):
    return {
        "invoice_number": invoice,
        "issuing_party": "OceanLink Carrier Co.",
        "billed_party": "FreshHarvest Imports LLC",
        "currency": "USD",
        "total_amount_due": billed,
        "amount_obligation_eliminated": oblig,
        "amount_disputable": disp,
        "needs_evidence_count": 0,
        "findings": [],
        "note": "test",
    }


class TestStore(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_create_and_get(self):
        cid = store.create_case(self.conn, sample_report(), letter="Dear carrier...")
        c = store.get_case(self.conn, cid)
        self.assertEqual(c["invoice_number"], "INV-1")
        self.assertEqual(c["status"], "drafted")
        self.assertEqual(c["amount_billed"], 3600.0)
        self.assertEqual(c["letter_text"], "Dear carrier...")

    def test_flagged_is_max_of_oblig_and_disputable(self):
        # obligation eliminated (full invoice) dominates the line-level disputable
        self.assertEqual(store.flagged_amount(sample_report(oblig=3600, disp=900)), 3600.0)
        # no facial defect -> flagged is the disputable lines
        self.assertEqual(store.flagged_amount(sample_report(oblig=0, disp=900)), 900.0)

    def test_lifecycle_timestamps(self):
        cid = store.create_case(self.conn, sample_report())
        store.set_status(self.conn, cid, "sent")
        c = store.get_case(self.conn, cid)
        self.assertEqual(c["status"], "sent")
        self.assertTrue(c["sent_at"])
        self.assertIsNone(c["resolved_at"])

    def test_set_recovered_resolves_and_records(self):
        cid = store.create_case(self.conn, sample_report())
        store.set_recovered(self.conn, cid, 2400.0, note="carrier waived")
        c = store.get_case(self.conn, cid)
        self.assertEqual(c["amount_recovered"], 2400.0)
        self.assertEqual(c["status"], "resolved")
        self.assertTrue(c["resolved_at"])

    def test_zero_recovery_marks_rejected(self):
        cid = store.create_case(self.conn, sample_report())
        store.set_recovered(self.conn, cid, 0)
        self.assertEqual(store.get_case(self.conn, cid)["status"], "rejected")

    def test_invalid_status_rejected(self):
        cid = store.create_case(self.conn, sample_report())
        with self.assertRaises(ValueError):
            store.set_status(self.conn, cid, "banana")

    def test_events_audit_trail(self):
        cid = store.create_case(self.conn, sample_report())
        store.set_status(self.conn, cid, "sent")
        store.set_recovered(self.conn, cid, 1000.0)
        events = [e["event"] for e in store.get_events(self.conn, cid)]
        self.assertEqual(events[0], "created")
        self.assertIn("status_changed", events)
        self.assertIn("recovered_set", events)

    def test_notes_append(self):
        cid = store.create_case(self.conn, sample_report())
        store.add_note(self.conn, cid, "called the carrier")
        store.add_note(self.conn, cid, "they asked for the B/L")
        notes = store.get_case(self.conn, cid)["notes"]
        self.assertIn("called the carrier", notes)
        self.assertIn("they asked for the B/L", notes)

    def test_portfolio_stats(self):
        c1 = store.create_case(self.conn, sample_report("INV-1", billed=3600, oblig=3600, disp=900))
        c2 = store.create_case(self.conn, sample_report("INV-2", billed=1000, oblig=0, disp=400))
        store.create_case(self.conn, sample_report("INV-3", billed=500, oblig=0, disp=0))
        store.set_recovered(self.conn, c1, 3000.0)   # resolved
        store.set_status(self.conn, c2, "sent")      # still open
        s = store.portfolio_stats(self.conn, fee_rate=0.2)
        self.assertEqual(s["total_cases"], 3)
        self.assertEqual(s["total_billed"], 5100.0)
        self.assertEqual(s["total_flagged"], 3600 + 400 + 0)
        self.assertEqual(s["total_recovered"], 3000.0)
        self.assertEqual(s["estimated_fee"], 600.0)  # 20% of 3000
        self.assertEqual(s["by_status"]["resolved"], 1)
        self.assertEqual(s["by_status"]["sent"], 1)
        self.assertEqual(s["by_status"]["drafted"], 1)
        # recovery rate = recovered / flagged among CLOSED cases = 3000 / 3600
        self.assertAlmostEqual(s["recovery_rate"], 3000.0 / 3600.0, places=4)

    def test_case_ref_format(self):
        self.assertEqual(store.case_ref(7), "C-0007")


if __name__ == "__main__":
    unittest.main(verbosity=2)
