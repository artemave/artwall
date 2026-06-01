import json
import random
import tempfile
import unittest
from pathlib import Path

from artwall import app, cache
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


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_happy_path_sets_wallpaper_and_records_history(self):
        router = met_router([101, 102], {101: True, 102: True})
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            object_id = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
            )

        self.assertIn(object_id, [101, 102])

        image = self.cache_dir / "current.jpg"
        self.assertEqual(image.read_bytes(), IMAGE_BYTES)

        annotate_call, wallpaper_call = runner.calls
        annotate_argv = annotate_call[0]
        self.assertEqual(annotate_argv[0], "magick")
        caption_arg = annotate_argv[annotate_argv.index("-annotate") + 2]
        self.assertIn(f"Painting {object_id}", caption_arg)
        self.assertEqual(annotate_call[1], True)  # check=True: a failed caption fails the run
        self.assertEqual(
            wallpaper_call,
            (["swaymsg", "output", "*", "bg", str(image), "fill"], True),
        )

        history = cache.load_json(self.cache_dir / "history.json", [])
        self.assertEqual(history, [object_id])

    def test_second_run_avoids_repeating_recent_artwork(self):
        router = met_router([101, 102], {101: True, 102: True})
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            first = app.run(config=cfg, rng=random.Random(0), runner=Recorder())
            second = app.run(config=cfg, rng=random.Random(0), runner=Recorder())

        self.assertNotEqual(first, second)
        history = cache.load_json(self.cache_dir / "history.json", [])
        self.assertEqual(sorted(history), [101, 102])

    def test_ids_are_cached_after_first_fetch(self):
        router = met_router([101], {101: True})
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            app.run(config=cfg, rng=random.Random(0), runner=Recorder())
            search_hits = sum(1 for p in s.requests if p.startswith("/search"))
            app.run(config=cfg, rng=random.Random(0), runner=Recorder())
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
                )


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
