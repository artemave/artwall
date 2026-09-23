import fcntl
import json
import os
import random
import re
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


# A Commons file page id, derived from the painting's QID so the fake Commons and
# the fake Wikidata agree without a lookup table: File:Q101.jpg <-> M9101 <-> Q101.
COMMONS_PAGE_OFFSET = 9000
FILE_TITLE = re.compile(r"^File:Q(\d+)\.jpg$")


def select_labels(store, params):
    """Mimic `wbgetentities`' label filtering, fallback chain included.

    Only the requested language comes back, and a `mul` label — Wikidata's
    language-agnostic one, all many artists have — reaches the caller *only* if
    `languagefallback` was asked for, keyed under the requested language.
    """
    language = params["languages"][0]
    if language in store:
        return {language: store[language]}
    if params.get("languagefallback") and "mul" in store:
        return {language: {**store["mul"], "language": "mul", "for-language": language}}
    return {}


DEFAULT_IMAGE_SIZE = (4000, 3000)  # comfortably bigger than any test display
LOW_RES_IMAGE_SIZE = (800, 600)  # smaller than a test display on both sides


def wikidata_router(
    qids,
    missing=(),
    anonymous=(),
    no_article=(),
    artist_article=None,
    unnamed_artist=False,
    unknown_files=(),
    unlinked_files=(),
    not_paintings=(),
    depicts_only=(),
    described_files=(),
    described_type="painting",
    low_res=(),
):
    """Serve a tiny Wikidata/Commons: WDQS catalogue CSV, Action-API entities, images.

    `missing` QIDs come back with no image (as if deleted since the catalogue
    cached) so the caller re-picks; `anonymous` QIDs have no creator; `no_article`
    QIDs have no Wikipedia sitelink; `artist_article` (a URL) gives the shared
    creator a Wikipedia article, so the link can fall back from painting to artist.
    `unnamed_artist` files the creator's name under `mul` and nowhere else, as
    Wikidata does for a name that isn't translated.

    The Commons side backs `resolve_link()`: `unknown_files` aren't on Commons at
    all (as in-copyright art isn't), `unlinked_files` are there but name no artwork,
    `depicts_only` carry the loose P180 instead of P6243 (as many real scans do),
    and `not_paintings` resolve to an entity that isn't a painting.

    `described_files` are `unlinked_files` that still describe their artwork in an
    `{{Artwork}}` wikitext template — the Commons-only fallback. `described_type`
    sets that template's `object type`, so a non-painting can be rejected there too.

    Every image reports `DEFAULT_IMAGE_SIZE` from `imageinfo` — comfortably bigger
    than any test display — except `low_res` QIDs, which report `LOW_RES_IMAGE_SIZE`
    (smaller on both sides), so `choose()` skips and re-picks past them.
    """
    missing, anonymous, no_article = set(missing), set(anonymous), set(no_article)
    unknown_files, unlinked_files = set(unknown_files), set(unlinked_files)
    not_paintings, depicts_only = set(not_paintings), set(depicts_only)
    described_files, low_res = set(described_files), set(low_res)

    def entity(num):
        instance = "Q5" if num in not_paintings else wikidata.PAINTING_QID
        claims = {"P31": [{"mainsnak": {"datavalue": {"value": {"id": instance}}}}]}
        if num in missing:
            # still an entity, just imageless -> parse_entity returns None
            return {"claims": claims, "labels": {}}
        claims["P18"] = [{"mainsnak": {"datavalue": {"value": f"Q{num}.jpg"}}}]
        if num not in anonymous:
            when = {"time": "+1700-00-00T00:00:00Z", "precision": 9}
            claims["P170"] = [{"mainsnak": {"datavalue": {"value": {"id": "Q999"}}}}]
            claims["P571"] = [{"mainsnak": {"datavalue": {"value": when}}}]
        ent = {"claims": claims, "labels": {"en": {"value": f"Painting {num}"}}}
        if num not in no_article:
            ent["sitelinks"] = {"enwiki": {"url": f"https://en.wikipedia.org/wiki/Painting_{num}"}}
        return ent

    def commons(params):
        """Commons' Action API: file title -> page id, then page id -> P6243 / size."""
        if params["action"][0] == "query":
            match = FILE_TITLE.match(params["titles"][0])
            num = int(match[1]) if match else None
            if num is None or num in unknown_files:
                return {"query": {"pages": {"-1": {"missing": ""}}}}
            pageid = COMMONS_PAGE_OFFSET + num
            page = {"pageid": pageid}
            if params.get("prop") == ["imageinfo"]:
                width, height = LOW_RES_IMAGE_SIZE if num in low_res else DEFAULT_IMAGE_SIZE
                page["imageinfo"] = [{"width": width, "height": height}]
            return {"query": {"pages": {str(pageid): page}}}
        if params["action"][0] == "parse":  # the wikitext fallback
            match = FILE_TITLE.match(params["page"][0])
            num = int(match[1])
            wikitext = (
                "== {{int:filedesc}} ==\n{{Artwork\n |wikidata = \n"
                f" |artist = {{{{Creator:Painter {num}}}}}\n"
                f" |title = {{{{title|en=Described {num}|de=Beschrieben {num}}}}}\n"
                f" |date = 1911\n |object type = {described_type}\n}}}}"
                if num in described_files
                else "== {{int:filedesc}} ==\n{{Information|author=Somebody}}"
            )
            return {"parse": {"wikitext": {"*": wikitext}}}
        media_id = params["ids"][0]
        num = int(media_id[1:]) - COMMONS_PAGE_OFFSET
        prop = "P180" if num in depicts_only else "P6243"
        statements = (
            {}
            if num in unlinked_files
            else {prop: [{"mainsnak": {"datavalue": {"value": {"numeric-id": num}}}}]}
        )
        return {"entities": {media_id: {"statements": statements}}}

    def router(path):
        parsed = urllib.parse.urlparse(path)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/sparql":  # catalogue (the only WDQS use)
            body = "qid\n" + "\n".join(str(q) for q in qids)
            return 200, "text/csv", body.encode()
        if parsed.path == "/commons-api":
            return 200, "application/json", json.dumps(commons(params)).encode()
        if parsed.path == "/api":  # wbgetentities for a painting or its creator
            eid = params["ids"][0]
            if eid == "Q999":  # the shared creator
                language = "mul" if unnamed_artist else "en"
                creator = {"labels": {language: {"value": "Tester"}}}
                if artist_article:
                    creator["sitelinks"] = {"enwiki": {"url": artist_article}}
                body = {"entities": {"Q999": creator}}
            else:
                body = {"entities": {eid: entity(int(eid[1:]))}}
            for ent in body["entities"].values():
                ent["labels"] = select_labels(ent["labels"], params)
            return 200, "application/json", json.dumps(body).encode()
        if parsed.path.startswith("/img/"):
            return 200, "image/jpeg", IMAGE_BYTES
        return 404, "text/plain", b"not found"

    router.base = ""
    return router


def config_for(server, cache_dir, caption_mode="text"):
    # default "text" so the burn-the-caption assertions below stay exercised;
    # interactive-mode tests pass caption_mode="interactive" explicitly. catalogue_dir
    # points at an empty temp path so tests fetch from the loopback server, not the
    # shipped bundle (the bundle-seed path is exercised by its own test).
    return Config(
        cache_dir=cache_dir,
        catalogue_dir=cache_dir / "no-bundle",
        sparql_url=server.base_url + "/sparql",
        api_url=server.base_url + "/api",
        commons_url=server.base_url + "/img/",
        commons_api_url=server.base_url + "/commons-api",
        # not the loopback server: nothing fetches it, it only has to end up in the
        # record as the article link for a painting with no Wikidata item
        commons_file_url="https://commons.example/wiki/",
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

    def test_retries_past_a_painting_too_small_for_the_display(self):
        # 102's scan is smaller than the display on both sides (it would be
        # upscaled, and blurry); only 101 is usable. rng.Random(0) draws 102
        # first (twice), so this only passes if the skip actually retries.
        router = wikidata_router([101, 102], low_res={102})
        with serve(router) as s:
            router.base = s.base_url
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        self.assertEqual(shown, [101])  # the too-small 102 was skipped

    def test_retries_past_a_painting_whose_file_vanished_from_commons(self):
        # 102's Wikidata claim still names a file, but Commons itself no longer
        # has it (deleted/renamed there since); only 101 is usable. rng.Random(0)
        # draws 102 first (twice), so this only passes if the skip actually retries.
        router = wikidata_router([101, 102], unknown_files={102})
        with serve(router) as s:
            router.base = s.base_url
            shown = app.run(
                config=config_for(s, self.cache_dir),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        self.assertEqual(shown, [101])

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

    def test_an_artist_named_only_in_mul_is_still_credited(self):
        """A creator whose name is filed under `mul` and no real language is named,
        not anonymous — asking for `en` alone got "Unknown artist" on a painting
        whose own Wikidata page reads "John Paul Selinger" (Q22002875)."""
        router = wikidata_router([101], unnamed_artist=True)
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
        self.assertIn("Tester", caption_arg)
        self.assertNotIn("Unknown artist", caption_arg)

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

    def test_interactive_mode_skips_burn_and_writes_caption_file(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            runner = Recorder()
            shown = app.run(
                config=config_for(s, self.cache_dir, caption_mode="interactive"),
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
        self.assertEqual(data["key"], f"Q{qid}")
        self.assertEqual(data["title"], f"Painting {qid}")
        self.assertEqual(data["artist"], "Tester")
        self.assertEqual(data["date"], "1700")
        self.assertEqual(data["image"], f"Q{qid}.jpg")  # what the star archiver fetches
        self.assertEqual(data["url"], f"https://en.wikipedia.org/wiki/Painting_{qid}")

    def test_interactive_mode_uses_artist_article_when_painting_has_none(self):
        # painting has no article, but its artist does -> link to the artist
        router = wikidata_router(
            [101], no_article={101}, artist_article="https://en.wikipedia.org/wiki/Jan_Asselijn"
        )
        with serve(router) as s:
            router.base = s.base_url
            app.run(
                config=config_for(s, self.cache_dir, caption_mode="interactive"),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        data = json.loads((self.cache_dir / "caption-DP-1.json").read_text())
        self.assertEqual(data["url"], "https://en.wikipedia.org/wiki/Jan_Asselijn")

    def test_interactive_mode_falls_back_to_wikidata_page_when_neither_has_an_article(self):
        # neither the painting nor its (here, absent) artist has an article
        router = wikidata_router([101], no_article={101}, anonymous={101})
        with serve(router) as s:
            router.base = s.base_url
            app.run(
                config=config_for(s, self.cache_dir, caption_mode="interactive"),
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )

        data = json.loads((self.cache_dir / "caption-DP-1.json").read_text())
        self.assertEqual(data["url"], "https://www.wikidata.org/wiki/Q101")

    def test_only_re_rolls_a_single_named_output(self):
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
                only="HDMI-A-1",
            )

        self.assertEqual(len(shown), 1)  # only the named display was set
        wallpaper_calls = [argv for argv, _check in runner.calls if argv[0] == "swaymsg"]
        self.assertEqual([argv[2] for argv in wallpaper_calls], ["HDMI-A-1"])
        self.assertTrue((self.cache_dir / "current-HDMI-A-1.jpg").exists())
        self.assertFalse((self.cache_dir / "current-DP-1.jpg").exists())

    def test_only_with_an_unknown_output_fails_loudly(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            with self.assertRaises(RuntimeError):
                app.run(
                    config=config_for(s, self.cache_dir),
                    rng=random.Random(0),
                    runner=Recorder(),
                    get_outputs=outputs("DP-1"),
                    get_font=fake_font,
                    only="NOPE-1",
                )

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


class LockTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def test_drops_the_run_while_another_holds_the_lock(self):
        # Hold the same lock the way a concurrent run would (a real flock, no mock):
        # a separate open fd is denied by flock just as another process would be, so
        # run() must see it held and rotate nothing.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            with cfg.lock.open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)  # stand in for the in-progress run
                runner = Recorder()
                shown = app.run(
                    config=cfg,
                    rng=random.Random(0),
                    runner=runner,
                    get_outputs=outputs("DP-1"),
                    get_font=fake_font,
                )

        self.assertEqual(shown, [])  # the lock was held, so the trigger was dropped
        self.assertEqual(runner.calls, [])  # nothing composed or set
        self.assertFalse((self.cache_dir / "current-DP-1.jpg").exists())

    def test_releases_the_lock_so_the_next_run_proceeds(self):
        # Back-to-back runs (the common case) must each acquire and release cleanly.
        router = wikidata_router([101, 102])
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir)
            first = app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"), fake_font)
            second = app.run(cfg, random.Random(1), Recorder(), outputs("DP-1"), fake_font)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)  # the second run wasn't blocked by a stale lock


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

    def test_falls_back_to_stale_cache_when_wdqs_is_down(self):
        # WDQS is outage-prone; a stale catalogue must not crash the wallpaper.
        cfg = Config(cache_dir=self.cache_dir, sparql_url="http://0.0.0.0:1/x")
        query = wikidata.catalogue_query(cfg.filters, cfg.date_begin, cfg.date_end)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cfg.ids_file(query)
        cache_file.write_text(json.dumps([301, 302]))
        stale = time.time() - cfg.ids_ttl - 60  # older than the TTL
        os.utime(cache_file, (stale, stale))

        ids = app.painting_ids(cfg)  # WDQS unreachable -> reuse the stale cache

        self.assertEqual(ids, [301, 302])
        # the stale mtime is left untouched, so the next run retries WDQS
        self.assertLess(cache_file.stat().st_mtime, time.time() - cfg.ids_ttl)

    def test_falls_back_to_bundle_when_wdqs_is_down_and_cache_stale(self):
        # A stale cache exists (so the first-run bundle-seed path is skipped) but is
        # empty/unreadable; the packaged bundle is the last resort when WDQS is down.
        bundle_dir = Path(tempfile.mkdtemp())
        cfg = Config(cache_dir=self.cache_dir, catalogue_dir=bundle_dir, sparql_url="http://0.0.0.0:1/x")
        query = wikidata.catalogue_query(cfg.filters, cfg.date_begin, cfg.date_end)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cfg.ids_file(query)
        cache_file.write_text(json.dumps([]))  # a stale, empty cache
        stale = time.time() - cfg.ids_ttl - 60
        os.utime(cache_file, (stale, stale))
        (bundle_dir / cfg.ids_filename(query)).write_text(json.dumps([401, 402]))

        ids = app.painting_ids(cfg)

        self.assertEqual(ids, [401, 402])

    def test_reraises_when_wdqs_down_and_nothing_cached(self):
        # No cache and no bundle: there is nothing to fall back to, so it must fail.
        cfg = Config(cache_dir=self.cache_dir, catalogue_dir=self.cache_dir / "no-bundle",
                     sparql_url="http://0.0.0.0:1/x")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(OSError):
            app.painting_ids(cfg)


def link_to(num):
    """A media-viewer link exactly as Wikipedia hands it out, for File:Q<n>.jpg."""
    return f"https://en.wikipedia.org/wiki/Artist#/media/File:Q{num}.jpg"


class ResolveLink(unittest.TestCase):
    """A pasted image link -> the same record `run()` writes for the overlay."""

    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())

    def resolve(self, link, **router_kwargs):
        router = wikidata_router([101], **router_kwargs)
        with serve(router) as s:
            router.base = s.base_url
            return app.resolve_link(config_for(s, self.cache_dir), link)

    def test_builds_the_full_record_from_a_link(self):
        record = self.resolve(link_to(101))
        self.assertEqual(
            record,
            {
                "key": "Q101",
                "artist": "Tester",
                "title": "Painting 101",
                "date": "1700",
                "image": "Q101.jpg",
                "url": "https://en.wikipedia.org/wiki/Painting_101",
            },
        )

    def test_the_record_matches_what_run_writes_for_the_same_painting(self):
        # the whole point: a pasted painting is indistinguishable from a shown one,
        # so the gallery, the trash and the archive stay one code path
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = config_for(s, self.cache_dir, caption_mode="interactive")
            app.run(
                config=cfg,
                rng=random.Random(0),
                runner=Recorder(),
                get_outputs=outputs("DP-1"),
                get_font=fake_font,
            )
            shown = json.loads(cfg.caption_file("DP-1").read_text())
            pasted = app.resolve_link(cfg, link_to(101))

        self.assertEqual(pasted, shown)

    def test_a_file_page_link_resolves_the_same_way(self):
        record = self.resolve("https://en.wikipedia.org/wiki/File:Q101.jpg")
        self.assertEqual(record["key"], "Q101")

    def test_a_link_naming_no_image_is_rejected(self):
        with self.assertRaises(app.LinkError) as cm:
            self.resolve("https://en.wikipedia.org/wiki/Muqi")
        self.assertIn("doesn't point at an image", str(cm.exception))

    def test_a_file_that_is_not_on_commons_is_rejected(self):
        # in-copyright art is hosted on the language wiki, not Commons
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(link_to(101), unknown_files=[101])
        self.assertIn("isn't on Wikimedia Commons", str(cm.exception))

    def test_a_file_describing_nothing_at_all_is_rejected(self):
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(link_to(101), unlinked_files=[101])
        self.assertIn("doesn't describe one either", str(cm.exception))

    def test_a_file_described_only_in_wikitext_still_resolves(self):
        # the real case this was built for: the Commons page carries a full
        # {{Artwork}} template, its structured data carries nothing, and the
        # painting has no Wikidata item to link to at all
        record = self.resolve(link_to(101), unlinked_files=[101], described_files=[101])
        self.assertEqual(
            record,
            {
                "key": "M9101",  # the Commons page is the identity — there is no QID
                "artist": "Painter 101",
                "title": "Described 101",
                "date": "1911",
                "image": "Q101.jpg",
                "url": "https://commons.example/wiki/File:Q101.jpg",
            },
        )

    def test_a_wikitext_record_is_shaped_like_a_wikidata_one(self):
        # the equivalence that keeps the gallery, trash and archive one code path
        described = self.resolve(link_to(101), unlinked_files=[101], described_files=[101])
        self.assertEqual(set(described), set(self.resolve(link_to(101))))

    def test_wikitext_describing_a_non_painting_is_rejected(self):
        # `object type` stands in for the P31 guard: without it a pasted
        # photograph would be archived as art
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(
                link_to(101),
                unlinked_files=[101],
                described_files=[101],
                described_type="photograph",
            )
        self.assertIn("doesn't describe one either", str(cm.exception))

    def test_structured_data_wins_over_wikitext(self):
        # a file with both is resolved through Wikidata, not the weaker fallback
        record = self.resolve(link_to(101), described_files=[101])
        self.assertEqual(record["key"], "Q101")
        self.assertEqual(record["title"], "Painting 101")

    def test_a_file_carrying_only_depicts_still_resolves(self):
        # the Muqi handscroll this was built for has P180 and no P6243
        self.assertEqual(self.resolve(link_to(101), depicts_only=[101])["key"], "Q101")

    def test_a_depicts_pointing_at_a_non_painting_is_still_rejected(self):
        # P180 is loose — a photo depicts whatever is in frame — so the painting
        # guard is what makes leaning on it safe
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(link_to(101), depicts_only=[101], not_paintings=[101])
        self.assertIn("isn't a painting", str(cm.exception))

    def test_a_file_depicting_something_that_is_not_a_painting_is_rejected(self):
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(link_to(101), not_paintings=[101])
        self.assertIn("isn't a painting", str(cm.exception))

    def test_an_artwork_with_no_image_is_rejected(self):
        with self.assertRaises(app.LinkError) as cm:
            self.resolve(link_to(101), missing=[101])
        self.assertIn("no image", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
