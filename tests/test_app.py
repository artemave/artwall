import json
import os
import random
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from artwall import app
from artwall.config import Config
from tests.server import serve

IMAGE_BYTES = b"\xff\xd8\xff fake jpeg"


class Recorder:
    """A real callable standing in for subprocess.run — records argv, runs nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, check=False):
        self.calls.append((argv, check))


def wikidata_router(qids, missing=(), anonymous=()):
    """Serve a tiny Wikidata/Commons: WDQS catalogue CSV, Action-API entities, images.

    `missing` QIDs come back with no image (as if deleted since the catalogue
    cached) so the caller re-picks; `anonymous` QIDs have no creator.
    """
    missing, anonymous = set(missing), set(anonymous)

    def entity(num):
        if num in missing:
            return {"claims": {}, "labels": {}}  # no P18 -> parse_entity returns None
        claims = {"P18": [{"mainsnak": {"datavalue": {"value": f"Q{num}.jpg"}}}]}
        if num not in anonymous:
            when = {"time": "+1700-00-00T00:00:00Z"}
            claims["P170"] = [{"mainsnak": {"datavalue": {"value": {"id": "Q999"}}}}]
            claims["P571"] = [{"mainsnak": {"datavalue": {"value": when}}}]
        return {"claims": claims, "labels": {"en": {"value": f"Painting {num}"}}}

    def router(path):
        parsed = urllib.parse.urlparse(path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/sparql":  # catalogue (the only WDQS use)
            body = "qid\n" + "\n".join(str(q) for q in qids)
            return 200, "text/csv", body.encode()
        if parsed.path == "/api":  # wbgetentities for a painting or its creator
            eid = params["ids"][0]
            if eid == "Q999":  # the shared creator
                body = {"entities": {"Q999": {"labels": {"en": {"value": "Tester"}}}}}
            else:
                body = {"entities": {eid: entity(int(eid[1:]))}}
            return 200, "application/json", json.dumps(body).encode()
        if parsed.path.startswith("/img/"):
            return 200, "image/jpeg", IMAGE_BYTES
        return 404, "text/plain", b"not found"

    router.base = ""
    return router


def config_for(server, cache_dir):
    return Config(
        cache_dir=cache_dir,
        sparql_url=server.base_url + "/sparql",
        api_url=server.base_url + "/api",
        commons_url=server.base_url + "/img/",
    )


def outputs(*names):
    """A real get_outputs provider returning fixed displays (each 1920x1080)."""
    return lambda: [app.Output(name, 1920, 1080) for name in names]


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_happy_path_sets_wallpaper(self):
        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("DP-1"),
            )

        self.assertEqual(len(shown), 1)
        qid = shown[0]
        self.assertIn(qid, [101, 102])

        image = self.cache_dir / "current-DP-1.jpg"
        self.assertEqual(image.read_bytes(), IMAGE_BYTES)

        compose_call, wallpaper_call = runner.calls
        compose_argv = compose_call[0]
        self.assertEqual(compose_argv[0], "magick")
        self.assertIn("1920x1080!", compose_argv)  # gradient canvas at the display's size
        caption_arg = compose_argv[compose_argv.index("-annotate") + 2]
        self.assertIn(f"Painting {qid}", caption_arg)
        self.assertEqual(compose_call[1], True)  # check=True: a failed compose fails the run
        self.assertEqual(
            wallpaper_call,
            (["swaymsg", "output", "DP-1", "bg", str(image), "fill"], True),
        )

    def test_each_display_gets_a_different_painting(self):
        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("DP-1", "HDMI-A-1"),
            )

        self.assertEqual(sorted(shown), [101, 102])  # two distinct paintings

        wallpaper_calls = [argv for argv, _check in runner.calls if argv[0] == "swaymsg"]
        self.assertEqual([argv[2] for argv in wallpaper_calls], ["DP-1", "HDMI-A-1"])
        for name in ("DP-1", "HDMI-A-1"):
            self.assertTrue((self.cache_dir / f"current-{name}.jpg").exists())

    def test_catalogue_is_cached_after_first_fetch(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"))
            catalogue_hits = sum(1 for p in s.requests if p.startswith("/sparql"))
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"))
            catalogue_hits_after = sum(1 for p in s.requests if p.startswith("/sparql"))

        self.assertEqual(catalogue_hits, 1)
        self.assertEqual(catalogue_hits_after, 1)  # served from cache, no second catalogue

    def test_retries_past_a_vanished_painting(self):
        # 101 comes back empty (deleted/no image); only 102 is usable.
        router = wikidata_router([101, 102], missing={101})
        with serve(router) as s:
            router.base = s.base_url
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
            )

        self.assertEqual(shown, [102])  # the vanished 101 was skipped

    def test_anonymous_painting_gets_unknown_artist(self):
        router = wikidata_router([101], anonymous={101})
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("DP-1"),
            )

        compose_argv = runner.calls[0][0]
        caption_arg = compose_argv[compose_argv.index("-annotate") + 2]
        self.assertIn("Unknown artist", caption_arg)  # no creator -> caption default

    def test_raises_when_no_painting_is_usable(self):
        router = wikidata_router([101, 102], missing={101, 102})
        with serve(router) as s:
            router.base = s.base_url
            with self.assertRaises(RuntimeError):
                app.run(
                    config=config_for(s, self.cache_dir),
                    rng=random.Random(0),
                    runner=Recorder(),
                    get_outputs=outputs("DP-1"),
                )


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_opens_preview_without_touching_the_wallpaper(self):
        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            path = app.preview(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
            )

        self.assertEqual(path, self.cache_dir / "preview.jpg")
        self.assertEqual(path.read_bytes(), IMAGE_BYTES)

        compose_call, open_call = runner.calls
        self.assertEqual(compose_call[0][0], "magick")
        self.assertEqual(open_call, (["xdg-open", str(path)], True))

        # The wallpaper is untouched: no swaymsg, no per-output image written.
        self.assertFalse(any(call[0][0] == "swaymsg" for call in runner.calls))
        self.assertFalse(any(self.cache_dir.glob("current-*.jpg")))


class SearchEntities(unittest.TestCase):
    def test_resolves_term_to_qid_rows(self):
        def router(path):
            body = {
                "search": [
                    {"id": "Q40415", "label": "Impressionism", "description": "art movement"},
                ]
            }
            return 200, "application/json", json.dumps(body).encode()

        with serve(router) as s:
            cfg = Config(api_url=s.base_url + "/api")
            rows = app.search_entities("impressionism", cfg)

        self.assertEqual(rows, [("Q40415", "Impressionism", "art movement")])


class ParseOutputs(unittest.TestCase):
    def test_returns_active_outputs_with_size(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "current_mode": {"width": 2560, "height": 1440}},
                {"name": "HDMI-A-1", "active": True, "current_mode": {"width": 1920, "height": 1080}},  # noqa: E501
            ]
        )
        self.assertEqual(
            app.parse_outputs(raw),
            [app.Output("DP-1", 2560, 1440), app.Output("HDMI-A-1", 1920, 1080)],
        )

    def test_skips_inactive_outputs(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "current_mode": {"width": 1920, "height": 1080}},
                {"name": "DP-2", "active": False, "current_mode": {"width": 1920, "height": 1080}},
            ]
        )
        self.assertEqual(app.parse_outputs(raw), [app.Output("DP-1", 1920, 1080)])


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_skips_when_changed_recently(self):
        (self.cache_dir / "last_change").touch()  # a change just happened

        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("DP-1"),
                throttle=True,
            )

        self.assertEqual(shown, [])  # nothing chosen
        self.assertEqual(runner.calls, [])  # and nothing set

    def test_changes_when_interval_elapsed(self):
        stamp = self.cache_dir / "last_change"
        stamp.touch()
        an_hour_ago = time.time() - 3600
        os.utime(stamp, (an_hour_ago, an_hour_ago))

        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                throttle=True,
            )

        self.assertEqual(len(shown), 1)
        self.assertTrue((self.cache_dir / "current-DP-1.jpg").exists())
        self.assertLess(time.time() - stamp.stat().st_mtime, 60)  # stamp refreshed


class PaintingIdsTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_raises_when_catalogue_is_empty(self):
        router = wikidata_router([])  # catalogue CSV has only the header
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(RuntimeError):
                app.painting_ids(cfg)


if __name__ == "__main__":
    unittest.main()
