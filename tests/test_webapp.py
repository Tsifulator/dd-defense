"""Web-layer tests for the FastAPI app — no API key, no network.

These exercise routing, validation, and the (LLM-free) /demo endpoint. The real
/audit extraction path needs an API key + a file, so it is covered by the manual
end-to-end run, not here.

Run:  python -m unittest tests.test_webapp   (from the dd-defense/ directory)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient
    from dd_defense.webapp import create_app
    _HAVE_FASTAPI = True
except Exception:  # fastapi not installed -> skip this module gracefully
    _HAVE_FASTAPI = False


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi/testclient not installed")
class TestWebApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(create_app())

    def test_home_has_upload_form(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('action="/audit"', r.text)
        self.assertIn("not legal advice", r.text)

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_demo_renders_full_report_without_api(self):
        r = self.client.get("/demo")
        self.assertEqual(r.status_code, 200)
        self.assertIn("DD-2024-88123", r.text)            # bundled sample invoice
        self.assertIn("Draft dispute letter", r.text)
        self.assertIn("Audit another invoice", r.text)

    def test_audit_rejects_wrong_extension(self):
        r = self.client.post("/audit", files={"invoice": ("notes.txt", b"hello", "text/plain")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Unsupported file type", r.text)

    def test_audit_rejects_bad_evidence_json(self):
        # valid extension, but evidence is not parseable -> 400 before any extraction
        r = self.client.post(
            "/audit",
            files={"invoice": ("inv.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={"evidence": "{not json"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Evidence JSON is invalid", r.text)

    def test_audit_requires_a_file(self):
        r = self.client.post("/audit")
        self.assertEqual(r.status_code, 422)  # FastAPI validation: missing file field


if __name__ == "__main__":
    unittest.main(verbosity=2)
