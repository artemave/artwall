import os
import tempfile
import time
import unittest
from pathlib import Path

from artwall import cache


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_save_load_roundtrip(self):
        path = self.dir / "data.json"
        cache.save_json(path, {"a": [1, 2, 3]})
        self.assertEqual(cache.load_json(path, None), {"a": [1, 2, 3]})

    def test_load_returns_default_when_missing(self):
        self.assertEqual(cache.load_json(self.dir / "nope.json", []), [])

    def test_fresh_true_for_recent_file(self):
        path = self.dir / "f"
        path.write_text("x")
        self.assertTrue(cache.fresh(path, ttl=60))

    def test_fresh_false_when_expired(self):
        path = self.dir / "f"
        path.write_text("x")
        old = time.time() - 120
        os.utime(path, (old, old))
        self.assertFalse(cache.fresh(path, ttl=60))

    def test_fresh_false_when_missing(self):
        self.assertFalse(cache.fresh(self.dir / "nope", ttl=60))


if __name__ == "__main__":
    unittest.main()
