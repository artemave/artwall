import json
import os
import random
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from artwall import app, wikidata
from artwall.config import Config
from tests.server import serve

IMAGE_BYTES = b"\xff\xd8\xff fake jpeg"


class Recorder:
    """A real callable standing in for subprocess.run — records argv, runs nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, check=False):
        self.calls.append((argv, check))


def wikidata_router(qids, missing=(), anonymous=(), no_article=(), artist_article=None):
    """Serve a tiny Wikidata/Commons: WDQS catalogue CSV, Action-API entities, images.

    `missing` QIDs come back with no image (as if deleted since the catalogue
    cached) so the caller re-picks; `anonymous` QIDs have no creator; `no_article`
    QIDs have no Wikipedia sitelink; `artist_article` (a URL) gives the shared
    creator a Wikipedia article, so the link can fall back from painting to artist.
    """
    missing, anonymous, no_article = set(missing), set(anonymous), set(no_article)

    def entity(num):
        if num in missing:
            return {"claims": {}, "labels": {}}  # no P18 -> parse_entity returns None
        claims = {"P18": [{"mainsnak": {"datavalue": {"value": f"Q{num}.jpg"}}}]}
        if num not in anonymous:
            when = {"time": "+1700-00-00T00:00:00Z"}
            claims["P170"] = [{"mainsnak": {"datavalue": {"value": {"id": "Q999"}}}}]
            claims["P571"] = [{"mainsnak": {"datavalue": {"value": when}}}]
        ent = {"claims": claims, "labels": {"en": {"value": f"Painting {num}"}}}
        if num not in no_article:
            ent["sitelinks"] = {"enwiki": {"url": f"https://en.wikipedia.org/wiki/Painting_{num}"}}
        return ent

    def router(path):
        parsed = urllib.parse.urlparse(path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/sparql":  # catalogue (the only WDQS use)
            body = "qid\n" + "\n".join(str(q) for q in qids)
            return 200, "text/csv", body.encode()
        if parsed.path == "/api":  # wbgetentities for a painting or its creator
            eid = params["ids"][0]
            if eid == "Q999":  # the shared creator
                creator = {"labels": {"en": {"value": "Tester"}}}
                if artist_article:
                    creator["sitelinks"] = {"enwiki": {"url": artist_article}}
                body = {"entities": {"Q999": creator}}
            else:
                body = {"entities": {eid: entity(int(eid[1:]))}}
            return 200, "application/json", json.dumps(body).encode()
        if parsed.path.startswith("/img/"):
            return 200, "image/jpeg", IMAGE_BYTES
        return 404, "text/plain", b"not found"

    router.base = ""
    return router


def config_for(server, cache_dir, caption_mode="text"):
    # default "text" so the burn-the-caption assertions below stay exercised;
    # link-mode tests pass caption_mode="link" explicitly. catalogue_dir points at
    # an empty temp path so tests fetch from the loopback server, not the shipped
    # bundle (the bundle-seed path is exercised by its own test).
    return Config(
        cache_dir=cache_dir,
        catalogue_dir=cache_dir / "no-bundle",
        sparql_url=server.base_url + "/sparql",
        api_url=server.base_url + "/api",
        commons_url=server.base_url + "/img/",
        caption_mode=caption_mode,
    )


def outputs(*names, scale=1.0):
    """A real get_outputs provider returning fixed displays (each 1920x1080)."""
    return lambda: [app.Output(name, 1920, 1080, scale) for name in names]


def fake_font():
    """A real get_font provider — a fixed (file, point size), no desktop needed."""
    return ("/fonts/Test.ttf", 11)


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
                get_font=fake_font,
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
        self.assertEqual(compose_argv[compose_argv.index("-font") + 1], "/fonts/Test.ttf")
        self.assertEqual(compose_argv[compose_argv.index("-pointsize") + 1], "15")  # 11pt @ scale 1
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
                get_font=fake_font,
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
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"), fake_font)
            catalogue_hits = sum(1 for p in s.requests if p.startswith("/sparql"))
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"), fake_font)
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
                get_font=fake_font,
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
                get_font=fake_font,
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
                    get_font=fake_font,
                )

    def test_caption_scales_with_a_hidpi_output(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("eDP-1", scale=2.0),
                get_font=fake_font,
            )

        compose_argv = runner.calls[0][0]
        # system 11pt, doubled on a 2x display -> magick pointsize 29.
        self.assertEqual(compose_argv[compose_argv.index("-pointsize") + 1], "29")

    def test_font_size_config_overrides_the_system_size(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            cfg.font_size = 20  # explicit override beats the system size
            runner = Recorder()
            app.run(cfg, random.Random(0), runner, outputs("DP-1"), fake_font)

        compose_argv = runner.calls[0][0]
        # 20pt at 1x -> magick pointsize 27, regardless of the system's 11pt.
        self.assertEqual(compose_argv[compose_argv.index("-pointsize") + 1], "27")

    def test_link_mode_skips_burn_and_writes_caption_file(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            shown = app.run(
                config=config_for(s, self.cache_dir, caption_mode="link"),
                rng=random.Random(0),
                runner=runner,
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        qid = shown[0]
        compose_argv = runner.calls[0][0]
        self.assertNotIn("-annotate", compose_argv)  # nothing burned into the wallpaper
        self.assertIn("-composite", compose_argv)  # painting still composed
        data = json.loads((self.cache_dir / "caption-DP-1.json").read_text())
        self.assertIn(f"Painting {qid}", data["text"])
        self.assertEqual(data["url"], f"https://en.wikipedia.org/wiki/Painting_{qid}")

    def test_link_mode_uses_artist_article_when_painting_has_none(self):
        # painting has no article, but its artist does -> link to the artist
        router = wikidata_router(
            [101], no_article={101}, artist_article="https://en.wikipedia.org/wiki/Jan_Asselijn"
        )
        with serve(router) as s:
            router.base = s.base_url
            app.run(
                config=config_for(s, self.cache_dir, caption_mode="link"),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        data = json.loads((self.cache_dir / "caption-DP-1.json").read_text())
        self.assertEqual(data["url"], "https://en.wikipedia.org/wiki/Jan_Asselijn")

    def test_link_mode_falls_back_to_wikidata_page_when_neither_has_an_article(self):
        # neither the painting nor its (here, absent) artist has an article
        router = wikidata_router([101], no_article={101}, anonymous={101})
        with serve(router) as s:
            router.base = s.base_url
            app.run(
                config=config_for(s, self.cache_dir, caption_mode="link"),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        data = json.loads((self.cache_dir / "caption-DP-1.json").read_text())
        self.assertEqual(data["url"], "https://www.wikidata.org/wiki/Q101")

    def test_unknown_caption_mode_fails_loudly(self):
        cfg = Config(cache_dir=self.cache_dir, caption_mode="bogus")
        with self.assertRaises(ValueError):
            app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"), fake_font)


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
                get_font=fake_font,
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
    def test_returns_active_outputs_with_size_and_scale(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "scale": 1.0, "current_mode": {"width": 2560, "height": 1440}},  # noqa: E501
                {"name": "eDP-1", "active": True, "scale": 2.0, "current_mode": {"width": 3840, "height": 2160}},  # noqa: E501
            ]
        )
        self.assertEqual(
            app.parse_outputs(raw),
            [app.Output("DP-1", 2560, 1440, 1.0), app.Output("eDP-1", 3840, 2160, 2.0)],
        )

    def test_skips_inactive_outputs(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "scale": 1.0, "current_mode": {"width": 1920, "height": 1080}},  # noqa: E501
                {"name": "DP-2", "active": False, "scale": 1.0, "current_mode": {"width": 1920, "height": 1080}},  # noqa: E501
            ]
        )
        self.assertEqual(app.parse_outputs(raw), [app.Output("DP-1", 1920, 1080, 1.0)])


class FontTests(unittest.TestCase):
    def test_parse_font_name_splits_family_and_size(self):
        self.assertEqual(app.parse_font_name("Adwaita Sans 11"), ("Adwaita Sans", 11))

    def test_scaled_pointsize_is_scale_aware(self):
        # 11pt at 96 dpi = ~14.67px on a 1x display, doubled on a 2x display.
        self.assertEqual(app.scaled_pointsize(11, 1.0), 15)
        self.assertEqual(app.scaled_pointsize(11, 2.0), 29)


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
                get_font=fake_font,
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
                get_font=fake_font,
                throttle=True,
            )

        self.assertEqual(len(shown), 1)
        self.assertTrue((self.cache_dir / "current-DP-1.jpg").exists())
        self.assertLess(time.time() - stamp.stat().st_mtime, 60)  # stamp refreshed

    def test_min_interval_override_shortens_the_throttle(self):
        stamp = self.cache_dir / "last_change"
        stamp.touch()
        ten_seconds_ago = time.time() - 10
        os.utime(stamp, (ten_seconds_ago, ten_seconds_ago))

        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            shown = app.run(
                config=config_for(s, self.cache_dir),  # default 30-min interval would skip
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
                throttle=True,
                min_interval=5,  # but the change was 10s ago > 5s, so it runs
            )

        self.assertEqual(len(shown), 1)


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

    def test_first_run_seeds_from_the_bundled_catalogue(self):
        # A packaged catalogue for this filter-set means no network on first run.
        bundle_dir = Path(tempfile.mkdtemp())
        cfg = Config(cache_dir=self.cache_dir, catalogue_dir=bundle_dir, sparql_url="http://0.0.0.0:1/x")
        query = wikidata.catalogue_query(cfg.filters, cfg.date_begin, cfg.date_end)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / cfg.ids_filename(query)).write_text(json.dumps([201, 202, 203]))

        ids = app.painting_ids(cfg)  # sparql_url is unreachable, so this must not fetch

        self.assertEqual(ids, [201, 202, 203])
        # adopted into the cache, so the TTL governs subsequent runs
        self.assertEqual(json.loads(cfg.ids_file(query).read_text()), [201, 202, 203])


if __name__ == "__main__":
    unittest.main()
