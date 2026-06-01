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
        self.assertIn("Audit another", r.text)            # result-page nav link
        self.assertIn("View all cases", r.text)

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

    def test_cases_dashboard_renders(self):
        r = self.client.get("/cases")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Total recovered", r.text)
        self.assertIn("Recovery rate", r.text)

    def test_missing_case_is_404(self):
        r = self.client.get("/cases/999999")
        self.assertEqual(r.status_code, 404)
        self.assertIn("No such case", r.text)


@unittest.skipUnless(_HAVE_FASTAPI, "fastapi/testclient not installed")
class TestWebAppAuth(unittest.TestCase):
    """Auth gate behaviour. Each test builds an app with auth toggled via env."""

    def _client(self, password=None):
        import importlib
        os.environ["DD_SECRET_KEY"] = "test-secret"
        if password:
            os.environ["DD_APP_PASSWORD"] = password
        else:
            os.environ.pop("DD_APP_PASSWORD", None)
        import dd_defense.auth as auth
        importlib.reload(auth)
        import dd_defense.webapp as webapp
        importlib.reload(webapp)
        from fastapi.testclient import TestClient
        return TestClient(webapp.create_app())

    def tearDown(self):
        os.environ.pop("DD_APP_PASSWORD", None)
        os.environ.pop("DD_SECRET_KEY", None)
        import importlib
        import dd_defense.auth as auth
        import dd_defense.webapp as webapp
        importlib.reload(auth)
        importlib.reload(webapp)

    def test_open_when_no_password(self):
        c = self._client(password=None)
        self.assertEqual(c.get("/cases").status_code, 200)

    def test_gated_redirects_browser_to_login(self):
        c = self._client(password="secret")
        r = c.get("/cases", follow_redirects=False, headers={"accept": "text/html"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers.get("location"), "/login")

    def test_gated_api_caller_gets_401(self):
        c = self._client(password="secret")
        r = c.get("/cases", follow_redirects=False, headers={"accept": "application/json"})
        self.assertEqual(r.status_code, 401)

    def test_healthz_open_even_when_gated(self):
        c = self._client(password="secret")
        self.assertEqual(c.get("/healthz").status_code, 200)

    def test_login_then_access(self):
        c = self._client(password="secret")
        self.assertEqual(c.post("/login", data={"password": "wrong"},
                                follow_redirects=False).status_code, 303)
        c.post("/login", data={"password": "secret"})
        self.assertEqual(c.get("/cases").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
