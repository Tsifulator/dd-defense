"""Tests for the session-auth helpers (stdlib only; no network)."""
import importlib
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAuth(unittest.TestCase):
    def setUp(self):
        os.environ["DD_SECRET_KEY"] = "test-secret-fixed"
        os.environ["DD_APP_PASSWORD"] = "hunter2"
        import dd_defense.auth as auth
        importlib.reload(auth)
        self.auth = auth

    def tearDown(self):
        os.environ.pop("DD_APP_PASSWORD", None)
        os.environ.pop("DD_SECRET_KEY", None)

    def test_enabled_when_password_set(self):
        self.assertTrue(self.auth.auth_enabled())

    def test_password_check_constant_time_ok(self):
        self.assertTrue(self.auth.check_password("hunter2"))
        self.assertFalse(self.auth.check_password("wrong"))
        self.assertFalse(self.auth.check_password(""))

    def test_token_roundtrip(self):
        t = self.auth.make_token()
        payload = self.auth.verify_token(t)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "operator")

    def test_tampered_token_rejected(self):
        t = self.auth.make_token()
        b64, sig = t.rsplit(".", 1)
        forged = b64 + "." + ("0" * len(sig))
        self.assertIsNone(self.auth.verify_token(forged))

    def test_expired_token_rejected(self):
        t = self.auth.make_token(ttl=-1)
        self.assertIsNone(self.auth.verify_token(t))

    def test_garbage_token_rejected(self):
        for bad in (None, "", "nodot", "a.b.c.d", "!!!.@@@"):
            self.assertIsNone(self.auth.verify_token(bad))

    def test_disabled_when_no_password(self):
        os.environ.pop("DD_APP_PASSWORD", None)
        importlib.reload(self.auth)
        self.assertFalse(self.auth.auth_enabled())
        self.assertFalse(self.auth.check_password("anything"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
