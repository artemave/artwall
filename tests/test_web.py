import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from artwall import web
from tests.server import serve


class GetJson(unittest.TestCase):
    def test_parses_json(self):
        def router(path):
            return 200, "application/json", json.dumps({"hello": "world"}).encode()

        with serve(router) as s:
            self.assertEqual(web.get_json(s.base_url + "/x"), {"hello": "world"})

    def test_encodes_query_params(self):
        def router(path):
            return 200, "application/json", json.dumps({"path": path}).encode()

        with serve(router) as s:
            result = web.get_json(s.base_url + "/sparql", {"query": "SELECT", "format": "json"})
            self.assertIn("query=SELECT", result["path"])
            self.assertIn("format=json", result["path"])

    def test_raises_on_http_error(self):
        def router(path):
            return 500, "text/plain", b"boom"

        with serve(router) as s:
            with self.assertRaises(urllib.error.HTTPError):
                web.get_json(s.base_url + "/x")


class GetText(unittest.TestCase):
    def test_returns_decoded_body(self):
        def router(path):
            return 200, "text/csv", b"qid\n101\n102\n"

        with serve(router) as s:
            self.assertEqual(web.get_text(s.base_url + "/sparql"), "qid\n101\n102\n")


class Download(unittest.TestCase):
    def test_writes_bytes_to_path(self):
        payload = b"\x89PNG fake image bytes"

        def router(path):
            return 200, "image/jpeg", payload

        with serve(router) as s:
            dest = Path(tempfile.mkdtemp()) / "current.jpg"
            web.download(s.base_url + "/img.jpg", dest)
            self.assertEqual(dest.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
