import json
import random
import tempfile
import unittest
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


def met_router(ids, images):
    """Serve a tiny Met API: a search result, per-object metadata, and images.

    `images` maps object_id -> bool (whether it has a primaryImage).
    """

    def router(path):
        if path.startswith("/search"):
            return 200, "application/json", json.dumps({"objectIDs": ids}).encode()
        if path.startswith("/objects/"):
            object_id = int(path.rsplit("/", 1)[1])
            meta = {
                "title": f"Painting {object_id}",
                "artistDisplayName": "Tester",
                "objectDate": "1900",
            }
            if images.get(object_id):
                meta["primaryImage"] = router.base + f"/img/{object_id}.jpg"
            return 200, "application/json", json.dumps(meta).encode()
        if path.startswith("/img/"):
            return 200, "image/jpeg", IMAGE_BYTES
        return 404, "text/plain", b"not found"

    router.base = ""
    return router


def config_for(server, cache_dir):
    return Config(
        cache_dir=cache_dir,
        search_url=server.base_url + "/search",
        object_url=server.base_url + "/objects/{}",
    )


def outputs(*names):
    """A real get_outputs provider returning fixed display names."""
    return lambda: list(names)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_happy_path_sets_wallpaper(self):
        router = met_router([101, 102], {101: True, 102: True})
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
        object_id = shown[0]
        self.assertIn(object_id, [101, 102])

        image = self.cache_dir / "current-DP-1.jpg"
        self.assertEqual(image.read_bytes(), IMAGE_BYTES)

        annotate_call, wallpaper_call = runner.calls
        annotate_argv = annotate_call[0]
        self.assertEqual(annotate_argv[0], "magick")
        caption_arg = annotate_argv[annotate_argv.index("-annotate") + 2]
        self.assertIn(f"Painting {object_id}", caption_arg)
        self.assertEqual(annotate_call[1], True)  # check=True: a failed caption fails the run
        self.assertEqual(
            wallpaper_call,
            (["swaymsg", "output", "DP-1", "bg", str(image), "fill"], True),
        )

    def test_each_display_gets_a_different_painting(self):
        router = met_router([101, 102], {101: True, 102: True})
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

    def test_ids_are_cached_after_first_fetch(self):
        router = met_router([101], {101: True})
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"))
            search_hits = sum(1 for p in s.requests if p.startswith("/search"))
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"))
            search_hits_after = sum(1 for p in s.requests if p.startswith("/search"))

        self.assertEqual(search_hits, 1)
        self.assertEqual(search_hits_after, 1)  # served from cache, no second search

    def test_raises_when_no_artwork_has_an_image(self):
        router = met_router([101, 102], {101: False, 102: False})
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
        router = met_router([101, 102], {101: True, 102: True})
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

        annotate_call, open_call = runner.calls
        self.assertEqual(annotate_call[0][0], "magick")
        self.assertEqual(open_call, (["xdg-open", str(path)], True))

        # The wallpaper is untouched: no swaymsg, no per-output image written.
        self.assertFalse(any(call[0][0] == "swaymsg" for call in runner.calls))
        self.assertFalse(any(self.cache_dir.glob("current-*.jpg")))


class ParseOutputs(unittest.TestCase):
    def test_returns_active_output_names(self):
        raw = json.dumps(
            [{"name": "DP-1", "active": True}, {"name": "HDMI-A-1", "active": True}]
        )
        self.assertEqual(app.parse_outputs(raw), ["DP-1", "HDMI-A-1"])

    def test_skips_inactive_outputs(self):
        raw = json.dumps(
            [{"name": "DP-1", "active": True}, {"name": "DP-2", "active": False}]
        )
        self.assertEqual(app.parse_outputs(raw), ["DP-1"])


class PaintingIdsTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_raises_when_search_returns_no_ids(self):
        def router(path):
            return 200, "application/json", json.dumps({"objectIDs": []}).encode()

        with serve(router) as s:
            cfg = Config(cache_dir=self.cache_dir, search_url=s.base_url + "/search")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(RuntimeError):
                app.painting_ids(cfg)


if __name__ == "__main__":
    unittest.main()
