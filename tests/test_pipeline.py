"""Tests for the autonomous invoice pipeline: inbox parsing, fingerprint
idempotency, evidence enrichment, sender->client. No network, no LLM."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense import evidence_sources, inbox, pipeline


class TestInboxParsing(unittest.TestCase):
    def _email_with(self, attachments):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        m = MIMEMultipart()
        m["From"] = "Acme Forwarding <ops@acme-forwarding.com>"
        m["Subject"] = "D&D invoice"
        m.attach(MIMEText("please review"))
        for name, subtype, data in attachments:
            m.attach(MIMEApplication(data, _subtype=subtype, name=name))
        return m.as_bytes()

    def test_extracts_pdf_attachment(self):
        raw = self._email_with([("inv.pdf", "pdf", b"%PDF-1.4 x")])
        parsed = inbox.attachments_from_bytes(raw)
        self.assertEqual(len(parsed["attachments"]), 1)
        self.assertEqual(parsed["attachments"][0]["filename"], "inv.pdf")
        self.assertIn("acme-forwarding.com", parsed["meta"]["from"])

    def test_ignores_non_invoice_attachments(self):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        m = MIMEMultipart()
        m["From"] = "x@y.com"
        m.attach(MIMEText("just a body, no attachments"))
        parsed = inbox.attachments_from_bytes(m.as_bytes())
        self.assertEqual(parsed["attachments"], [])

    def test_multiple_invoice_attachments(self):
        raw = self._email_with([("a.pdf", "pdf", b"%PDF a"), ("b.pdf", "pdf", b"%PDF b")])
        parsed = inbox.attachments_from_bytes(raw)
        self.assertEqual(len(parsed["attachments"]), 2)


class TestFingerprint(unittest.TestCase):
    def test_same_bytes_same_fingerprint(self):
        with tempfile.NamedTemporaryFile(delete=False) as a:
            a.write(b"hello world"); pa = a.name
        with tempfile.NamedTemporaryFile(delete=False) as b:
            b.write(b"hello world"); pb = b.name
        with tempfile.NamedTemporaryFile(delete=False) as c:
            c.write(b"different"); pc = c.name
        try:
            self.assertEqual(pipeline.file_fingerprint(pa), pipeline.file_fingerprint(pb))
            self.assertNotEqual(pipeline.file_fingerprint(pa), pipeline.file_fingerprint(pc))
        finally:
            for p in (pa, pb, pc):
                os.unlink(p)

    def test_ledger_roundtrip(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "cases.db")
        pipeline._save_ledger(db, {"abc", "def"})
        self.assertEqual(pipeline._load_ledger(db), {"abc", "def"})


class TestSenderToClient(unittest.TestCase):
    def test_domain_becomes_client(self):
        self.assertEqual(pipeline._client_from_sender("ops@acme-forwarding.com"), "Acme-forwarding")
        self.assertEqual(pipeline._client_from_sender("Bob <bob@harbor.co>"), "Harbor")

    def test_none_on_garbage(self):
        self.assertIsNone(pipeline._client_from_sender("no-at-sign"))
        self.assertIsNone(pipeline._client_from_sender(None))


class TestEvidenceEnrichment(unittest.TestCase):
    def test_holidays_become_closures(self):
        ev = evidence_sources.build_evidence(years=(2025,))
        self.assertEqual(len([c for c in ev.closures if "holiday" in c.reason.lower()]), 11)
        self.assertTrue(ev.free_time_tolls_holidays)

    def test_reads_local_files(self):
        d = tempfile.mkdtemp()
        import json
        with open(os.path.join(d, "tariffs.json"), "w") as fh:
            json.dump({"default": 99}, fh)
        with open(os.path.join(d, "closures.json"), "w") as fh:
            json.dump([{"location": "POLA", "start": "2025-03-01", "end": "2025-03-02", "reason": "strike"}], fh)
        ev = evidence_sources.build_evidence(years=(2025,), data_dir=d)
        self.assertEqual(ev.tariff_rates.get("default"), 99)
        self.assertTrue(any(c.reason == "strike" for c in ev.closures))

    def test_scaffold_creates_files(self):
        d = tempfile.mkdtemp()
        written = evidence_sources.scaffold(data_dir=d)
        self.assertTrue(any("tariffs.json" in p for p in written))
        # re-scaffold doesn't clobber
        self.assertEqual(evidence_sources.scaffold(data_dir=d), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
