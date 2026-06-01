import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from artwall import met
from tests.server import serve


class GetJson(unittest.TestCase):
    def test_parses_json(self):
        def router(path):
            return 200, "application/json", json.dumps({"hello": "world"}).encode()

        with serve(router) as s:
            self.assertEqual(met.get_json(s.base_url + "/x"), {"hello": "world"})

    def test_encodes_query_params(self):
        def router(path):
            return 200, "application/json", json.dumps({"path": path}).encode()

        with serve(router) as s:
            result = met.get_json(s.base_url + "/search", {"q": "painting", "hasImages": "true"})
            self.assertIn("q=painting", result["path"])
            self.assertIn("hasImages=true", result["path"])

    def test_raises_on_http_error(self):
        def router(path):
            return 500, "text/plain", b"boom"

        with serve(router) as s:
            with self.assertRaises(urllib.error.HTTPError):
                met.get_json(s.base_url + "/x")


class Download(unittest.TestCase):
    def test_writes_bytes_to_path(self):
        payload = b"\x89PNG fake image bytes"

        def router(path):
            return 200, "image/jpeg", payload

        with serve(router) as s:
            dest = Path(tempfile.mkdtemp()) / "current.jpg"
            met.download(s.base_url + "/img.jpg", dest)
            self.assertEqual(dest.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
