"""Robustness tests: filetype sniffing + rate limiting (no network, no API key)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dd_defense.extract import ExtractionError, extract_from_file, sniff_filetype


class TestSniff(unittest.TestCase):
    def test_pdf(self):
        self.assertEqual(sniff_filetype(b"%PDF-1.7\n..."), "pdf")

    def test_png(self):
        self.assertEqual(sniff_filetype(b"\x89PNG\r\n\x1a\n\x00\x00"), "png")

    def test_jpeg(self):
        self.assertEqual(sniff_filetype(b"\xff\xd8\xff\xe0JFIF"), "jpeg")

    def test_pdf_with_leading_whitespace(self):
        self.assertEqual(sniff_filetype(b"   \n%PDF-1.4"), "pdf")

    def test_unknown(self):
        self.assertIsNone(sniff_filetype(b"this is just text"))
        self.assertIsNone(sniff_filetype(b""))


class TestExtractValidation(unittest.TestCase):
    def test_unknown_content_raises_extractionerror(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(b"not a real pdf, just text")
            path = fh.name
        try:
            with self.assertRaises(ExtractionError):
                # passes a key so it gets past the key check to the content check
                extract_from_file(path, api_key="sk-ant-dummy")
        finally:
            os.unlink(path)

    def test_missing_key_raises_extractionerror(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(b"%PDF-1.4 minimal")
            path = fh.name
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(ExtractionError):
                extract_from_file(path, api_key=None)
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old
            os.unlink(path)


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        from dd_defense.webapp import _RateLimiter
        self.RL = _RateLimiter

    def test_allows_up_to_max_then_blocks(self):
        rl = self.RL(max_hits=3, window_s=100)
        now = 1000.0
        self.assertTrue(rl.allow("ip", now)[0])
        self.assertTrue(rl.allow("ip", now)[0])
        self.assertTrue(rl.allow("ip", now)[0])
        self.assertFalse(rl.allow("ip", now)[0])  # 4th blocked

    def test_window_slides(self):
        rl = self.RL(max_hits=2, window_s=100)
        self.assertTrue(rl.allow("ip", 0)[0])
        self.assertTrue(rl.allow("ip", 0)[0])
        self.assertFalse(rl.allow("ip", 50)[0])     # still within window
        self.assertTrue(rl.allow("ip", 101)[0])     # old hits expired

    def test_per_key_isolation(self):
        rl = self.RL(max_hits=1, window_s=100)
        self.assertTrue(rl.allow("a", 0)[0])
        self.assertTrue(rl.allow("b", 0)[0])        # different key, own bucket
        self.assertFalse(rl.allow("a", 0)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
