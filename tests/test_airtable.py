"""Tests for the Airtable layer — pure logic only, NO network / NO API key.

Covers payload shaping, outreach drafting/scoring/field-mapping, and case->Airtable
mapping. The HTTP calls (create/list/update) are intentionally not exercised here;
they need a live base and are verified manually via `airtable-ping`."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense import airtable, outreach


class TestAirtableShaping(unittest.TestCase):
    def test_clean_fields_drops_empty(self):
        self.assertEqual(
            airtable.clean_fields({"a": "x", "b": "", "c": None, "d": [], "e": ["v", ""]}),
            {"a": "x", "e": ["v"]})

    def test_clean_fields_trims_strings(self):
        self.assertEqual(airtable.clean_fields({"a": "  hi  "}), {"a": "hi"})

    def test_records_payload(self):
        self.assertEqual(
            airtable._records_payload([{"a": 1}, {"b": ""}]),
            {"records": [{"fields": {"a": 1}}, {"fields": {}}], "typecast": True})

    def test_chunk_by_ten(self):
        self.assertEqual([len(c) for c in airtable._chunk(list(range(25)))], [10, 10, 5])

    def test_missing_key_raises(self):
        old = os.environ.pop("AIRTABLE_API_KEY", None)
        try:
            with self.assertRaises(airtable.AirtableError):
                airtable._api_key()
        finally:
            if old is not None:
                os.environ["AIRTABLE_API_KEY"] = old


class TestOutreachScoring(unittest.TestCase):
    def test_forwarder_outranks_importer(self):
        f = outreach.fit_score({"type": "Forwarder", "containers_per_mo": 120})
        i = outreach.fit_score({"type": "Importer", "containers_per_mo": 120})
        self.assertGreater(f, i)

    def test_known_values(self):
        self.assertEqual(outreach.fit_score({"type": "Forwarder", "containers_per_mo": 120}), 70)
        self.assertEqual(outreach.fit_score({"type": "Importer", "containers_per_mo": 60}), 30)

    def test_capped_at_100(self):
        self.assertLessEqual(outreach.fit_score({"type": "Forwarder", "containers_per_mo": 99999}), 100)

    def test_handles_missing_volume(self):
        self.assertEqual(outreach.fit_score({"type": "Broker"}), 20)


class TestOutreachDraft(unittest.TestCase):
    def test_uses_first_name(self):
        d = outreach.draft_email({"company": "Harbor FF", "type": "Forwarder", "contact_name": "Maria Lopez"})
        self.assertIn("Hi Maria,", d["body"])

    def test_fallback_greeting(self):
        d = outreach.draft_email({"company": "X", "type": "Forwarder"})
        self.assertIn("Hi there,", d["body"])

    def test_forwarder_vs_importer_differ(self):
        f = outreach.draft_email({"company": "A", "type": "Forwarder"})["body"]
        i = outreach.draft_email({"company": "A", "type": "Importer"})["body"]
        self.assertNotEqual(f, i)
        self.assertIn("your clients", f)              # forwarder angle
        self.assertIn("importers pay them anyway", i)  # importer angle

    def test_references_site(self):
        d = outreach.draft_email({"company": "X", "type": "Forwarder"})
        self.assertIn("dnddefense.com", d["body"])


class TestProspectFields(unittest.TestCase):
    def test_maps_to_airtable_fields(self):
        p = {"company": "Harbor FF", "type": "forwarder", "email": "a@b.com",
             "containers_per_mo": 120}
        d = outreach.draft_email(p)
        f = outreach.prospect_to_fields(p, d)
        self.assertEqual(f["Company"], "Harbor FF")
        self.assertEqual(f["Type"], "Forwarder")
        self.assertEqual(f["Status"], "Needs Approval")
        self.assertEqual(f["Fit Score"], 70)
        self.assertEqual(f["Draft Subject"], d["subject"])
        self.assertIn("Hi", f["Draft Email"])

    def test_unknown_type_maps_to_other(self):
        f = outreach.prospect_to_fields({"company": "X", "type": "wholesaler"},
                                        {"subject": "s", "body": "b"})
        self.assertEqual(f["Type"], "Other")


class TestCaseSync(unittest.TestCase):
    def test_case_to_fields(self):
        from dd_defense import airtable_sync
        case = {"id": 7, "invoice_number": "INV-1", "carrier": "OceanLink",
                "client": "Acme", "amount_billed": 3600, "amount_flagged": 3600,
                "amount_recovered": 2900, "status": "resolved"}
        f = airtable_sync.case_to_fields(case)
        self.assertEqual(f["Case Ref"], "C-0007")
        self.assertEqual(f["Amount Recovered"], 2900)
        self.assertEqual(f["Status"], "resolved")


class TestSetupSchema(unittest.TestCase):
    def test_schema_has_three_tables_with_primaries(self):
        from dd_defense import airtable_setup as s
        self.assertEqual(set(s.SCHEMA), {"Prospects", "Leads", "Cases"})
        for t in s.SCHEMA:
            self.assertIn(t, s._PRIMARY)

    def test_prospects_status_includes_needs_approval(self):
        from dd_defense import airtable_setup as s
        status = [f for f in s.SCHEMA["Prospects"] if f["name"] == "Status"][0]
        choices = [c["name"] for c in status["options"]["choices"]]
        self.assertIn("Needs Approval", choices)


if __name__ == "__main__":
    unittest.main(verbosity=2)
