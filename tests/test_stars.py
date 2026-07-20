import contextlib
import dataclasses
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from artwall import app, stars
from artwall.config import Config
from tests.server import serve
from tests.test_app import (
    IMAGE_BYTES,
    Recorder,
    config_for,
    fake_font,
    link_to,
    outputs,
    wikidata_router,
)


def star_of(qid, **overrides):
    """A star record shaped exactly as `selection.record()` builds it."""
    return {
        "qid": qid,
        "artist": "Rembrandt",
        "title": f"Painting {qid}",
        "date": "1642",
        "image": f"Q{qid}.jpg",
        "url": f"https://en.wikipedia.org/wiki/Painting_{qid}",
        **overrides,
    }


class Toggle(unittest.TestCase):
    def test_adds_a_new_painting(self):
        result, starred = stars.toggle([], star_of(101))
        self.assertTrue(starred)
        self.assertEqual([s["qid"] for s in result], [101])

    def test_appends_so_the_list_stays_oldest_first(self):
        result, _ = stars.toggle([star_of(101)], star_of(102))
        self.assertEqual([s["qid"] for s in result], [101, 102])

    def test_removes_a_painting_that_is_already_starred(self):
        result, starred = stars.toggle([star_of(101), star_of(102)], star_of(101))
        self.assertFalse(starred)
        self.assertEqual([s["qid"] for s in result], [102])

    def test_matches_on_qid_not_identity(self):
        # the record the overlay hands back is re-read from disk, so it's a distinct
        # dict — only the QID can decide whether it's already starred.
        result, starred = stars.toggle([star_of(101)], star_of(101, title="Renamed"))
        self.assertFalse(starred)
        self.assertEqual(result, [])

    def test_is_starred(self):
        self.assertTrue(stars.is_starred([star_of(101)], 101))
        self.assertFalse(stars.is_starred([star_of(101)], 102))


class RenderPage(unittest.TestCase):
    def test_empty_gallery_explains_itself(self):
        page = stars.render_page([])
        self.assertIn("Nothing starred yet", page)
        self.assertNotIn("<figure>", page)

    def test_counts_the_paintings_and_pluralises(self):
        self.assertIn("★ 1 starred painting<", stars.render_page([star_of(101)]))
        self.assertIn("★ 2 starred paintings<", stars.render_page([star_of(101), star_of(102)]))

    def test_images_are_relative_so_the_directory_is_portable(self):
        page = stars.render_page([star_of(101)])
        self.assertIn('<img src="images/Q101.jpg"', page)
        self.assertNotIn("commons.wikimedia.org", page)  # nothing loads from the network

    def test_the_image_links_to_the_full_size_file(self):
        page = stars.render_page([star_of(101)])
        self.assertIn('<a class="art" href="images/Q101.jpg">', page)

    def test_the_title_links_to_the_article_in_a_new_tab(self):
        page = stars.render_page([star_of(101)])
        self.assertIn(
            '<a class="title" href="https://en.wikipedia.org/wiki/Painting_101" '
            'target="_blank" rel="noopener noreferrer">Painting 101</a>',
            page,
        )

    def test_the_grid_is_capped_to_the_columns_its_paintings_can_fill(self):
        # multicol balances to equal heights, so an unbounded container squeezes a
        # couple of paintings into one column and leaves the rest of the row empty
        one = stars.render_page([star_of(101)])
        three = stars.render_page([star_of(101), star_of(102), star_of(103)])
        self.assertIn('style="max-width: 260px"', one)  # COLUMN
        self.assertIn('style="max-width: 844px"', three)  # 3*260 + 2*32

    def test_unstar_buttons_only_when_interactive(self):
        self.assertNotIn("/unstar/", stars.render_page([star_of(101)]))
        served = stars.render_page([star_of(101)], interactive=True)
        self.assertIn('<form class="corner" method="post" action="/unstar/101">', served)

    def test_the_unstar_button_is_labelled_for_screen_readers(self):
        served = stars.render_page([star_of(101, title="The Night Watch")], interactive=True)
        self.assertIn('aria-label="Unstar The Night Watch"', served)

    def test_an_untitled_paintings_button_still_gets_a_label(self):
        served = stars.render_page([star_of(101, title="")], interactive=True)
        self.assertIn('aria-label="Unstar Untitled"', served)

    def test_the_trash_link_appears_only_when_the_trash_has_something(self):
        stars_ = [star_of(101)]
        self.assertNotIn('href="/trash"', stars.render_page(stars_, interactive=True))
        linked = stars.render_page(stars_, interactive=True, trash_count=2)
        self.assertIn('<a href="/trash">Trash (2 paintings)</a>', linked)
        one = stars.render_page(stars_, interactive=True, trash_count=1)
        self.assertIn("Trash (1 painting)</a>", one)

    def test_the_archived_page_never_links_to_the_trash(self):
        # nothing would serve /trash once the command exits
        page = stars.render_page([star_of(101)], interactive=False, trash_count=3)
        self.assertNotIn("/trash", page)

    def test_the_undo_banner_names_the_painting_and_posts_back(self):
        flash = stars.Flash("Removed", "Painting 101", 101)
        page = stars.render_page([star_of(102)], interactive=True, flash=flash)
        self.assertIn("Removed <i>Painting 101</i>.", page)
        self.assertIn('<form method="post" action="/restore/101">', page)

    def test_a_flash_without_an_undo_has_no_button(self):
        flash = stars.Flash("Deleted 2 paintings for good")
        page = stars.render_page([], interactive=True, flash=flash)
        self.assertIn("Deleted 2 paintings for good.", page)
        self.assertNotIn("<form method=\"post\" action=\"/restore/", page)

    def test_no_banner_without_a_flash(self):
        self.assertNotIn('class="flash"', stars.render_page([star_of(101)], interactive=True))

    def test_no_form_is_nested_inside_a_paragraph(self):
        # A <p> can't contain a <form>: the browser closes the paragraph early and
        # hoists the form out as a sibling, dropping it out of the banner's flex row.
        # Substring assertions can't see this — only a real HTML parser can.
        flash = stars.Flash("Removed", "Painting 102", 102)
        pages = [
            stars.render_page([star_of(101)], interactive=True, flash=flash),
            stars.render_page([], interactive=True, flash=flash),
            stars.render_trash([{"index": 0, "star": star_of(101)}], flash),
            stars.render_trash([], flash),
        ]
        for page in pages:
            for paragraph in re.findall(r"<p\b.*?</p>", page, re.S):
                self.assertNotIn("<form", paragraph)

    def test_trashing_the_last_star_leaves_an_empty_gallery_with_a_banner(self):
        # the empty-state copy and the undo offer have to coexist
        page = stars.render_page([], interactive=True, flash=stars.Flash("Removed", "X", 101))
        self.assertIn("Nothing starred yet", page)
        self.assertIn('action="/restore/101"', page)


class RenderTrash(unittest.TestCase):
    def trashed(self, *qids):
        return [{"index": i, "star": star_of(q)} for i, q in enumerate(qids)]

    def test_empty_trash_explains_itself(self):
        page = stars.render_trash([])
        self.assertIn("The trash is empty", page)
        self.assertNotIn("/trash/empty", page)  # nothing to delete

    def test_lists_trashed_paintings_with_restore_buttons(self):
        page = stars.render_trash(self.trashed(101, 102))
        self.assertIn("2 paintings in the trash", page)
        self.assertIn('action="/restore/101"', page)
        self.assertIn('aria-label="Restore Painting 102"', page)

    def test_trash_images_are_served_absolutely_not_relatively(self):
        # the trash page only ever exists on the server, so it can use a real route
        page = stars.render_trash(self.trashed(101))
        self.assertIn('<img src="/trash/images/Q101.jpg"', page)
        self.assertIn('<a class="art" href="/trash/images/Q101.jpg">', page)

    def test_a_trashed_paintings_title_still_links_to_its_article(self):
        page = stars.render_trash(self.trashed(101))
        self.assertIn('rel="noopener noreferrer">Painting 101</a>', page)

    def test_offers_to_empty_the_trash_and_says_how_many(self):
        page = stars.render_trash(self.trashed(101, 102, 103))
        self.assertIn('action="/trash/empty"', page)
        self.assertIn("Delete 3 paintings forever", page)
        self.assertIn("Delete 1 painting forever", stars.render_trash(self.trashed(101)))

    def test_links_back_to_the_gallery(self):
        self.assertIn('<a href="/">← Gallery</a>', stars.render_trash([]))

    def test_newest_star_is_shown_first(self):
        page = stars.render_page([star_of(101), star_of(102)])
        self.assertLess(page.index("Q102.jpg"), page.index("Q101.jpg"))

    def test_escapes_html_in_the_painting_metadata(self):
        # real titles contain ampersands and quotes, and a title is emitted twice:
        # as text and as an alt attribute.
        page = stars.render_page([star_of(101, title='Punch & Judy "<script>"', artist="A&B")])
        self.assertIn("Punch &amp; Judy &quot;&lt;script&gt;&quot;", page)
        self.assertIn("A&amp;B", page)
        self.assertNotIn("<script>", page)  # nothing from Wikidata reaches the DOM as markup

    def test_falls_back_for_an_anonymous_untitled_painting(self):
        page = stars.render_page([star_of(101, artist="", title="")])
        self.assertIn("Unknown artist", page)
        self.assertIn("Untitled", page)


class StarTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"  # not yet created

    def config(self, server):
        cfg = config_for(server, self.cache_dir, caption_mode="interactive")
        cfg.data_dir = self.data_dir
        return cfg

    def set_a_wallpaper(self, cfg):
        """Really run() so the caption file `star()` reads is the one run() writes."""
        return app.run(cfg, random.Random(0), Recorder(), outputs("DP-1"), fake_font)[0]

    def test_stars_the_current_painting_and_archives_its_image(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = self.config(s)
            qid = self.set_a_wallpaper(cfg)
            starred = stars.star(cfg, output="DP-1")

        self.assertTrue(starred)
        self.assertEqual([s["qid"] for s in stars.load(cfg)], [qid])
        # the painting itself is archived beside the list, ready to be backed up
        self.assertEqual(cfg.star_image(qid).read_bytes(), IMAGE_BYTES)
        self.assertEqual(cfg.star_image(qid), self.data_dir / "images" / f"Q{qid}.jpg")

    def test_archives_at_the_configured_width(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = self.config(s)
            cfg.stars_image_width = 1234
            self.set_a_wallpaper(cfg)
            stars.star(cfg, output="DP-1")
            fetches = [p for p in s.requests if p.startswith("/img/") and "1234" in p]

        self.assertEqual(len(fetches), 1)  # a full-size archive copy, not the wallpaper's

    def test_starring_twice_unstars_and_deletes_the_image(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = self.config(s)
            qid = self.set_a_wallpaper(cfg)
            stars.star(cfg, output="DP-1")
            starred = stars.star(cfg, output="DP-1")

        self.assertFalse(starred)
        self.assertEqual(stars.load(cfg), [])
        self.assertFalse(cfg.star_image(qid).exists())

    def test_stars_survive_wiping_the_cache(self):
        # the whole reason stars live under data_dir: `rm -rf ~/.cache/artwall` is
        # documented as a safe reset.
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = self.config(s)
            qid = self.set_a_wallpaper(cfg)
            stars.star(cfg, output="DP-1")

        for path in self.cache_dir.iterdir():
            path.unlink()

        self.assertEqual([s["qid"] for s in stars.load(cfg)], [qid])
        self.assertTrue(cfg.star_image(qid).exists())

    def test_unknown_output_fails_loudly(self):
        cfg = Config(cache_dir=self.cache_dir, data_dir=self.data_dir)
        with self.assertRaises(RuntimeError):
            stars.star(cfg, output="NOPE-1")  # no caption file was ever written


class WritePage(unittest.TestCase):
    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        self.cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=self.data_dir)

    def test_writes_the_gallery_beside_the_images(self):
        self.cfg.stars_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.stars_file.write_text(json.dumps([star_of(101)]))

        page = stars.write_page(self.cfg)

        self.assertEqual(page, self.data_dir / "stars.html")
        self.assertIn('<img src="images/Q101.jpg"', page.read_text())

    def test_the_archived_copy_has_no_unstar_buttons(self):
        # nothing would answer the POST once the server is gone
        self.cfg.stars_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.stars_file.write_text(json.dumps([star_of(101)]))

        self.assertNotIn("/unstar/", stars.write_page(self.cfg).read_text())

    def test_an_empty_gallery_still_writes(self):
        self.assertIn("Nothing starred yet", stars.write_page(self.cfg).read_text())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An opener that reports a 3xx instead of chasing it."""

    def redirect_request(self, *_args):
        return None


class ServeGallery(unittest.TestCase):
    """Drives the real loopback server with real HTTP requests — no mocks."""

    def setUp(self):
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        # cache_dir too, never the default: serve_gallery() writes stars.pid there
        # and signals whatever it names — the developer's own gallery, otherwise.
        self.cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=self.data_dir)
        self.cfg.star_image(101).parent.mkdir(parents=True, exist_ok=True)
        self.cfg.star_image(101).write_bytes(IMAGE_BYTES)
        self.cfg.star_image(102).write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(101), star_of(102)])

        self.runner = Recorder()
        self.server = stars.serve_gallery(self.cfg, self.runner)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.url.rstrip("/")

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read()

    def post(self, path):
        """POST, returning (status, Location) *without* following the redirect.

        A browser follows the 303 and lands on `/`, which renders — and therefore
        consumes — the flash. Following it here would spend the undo offer before
        the assertion that's about it, so the opener is built without a redirect
        handler and the 303 surfaces as an HTTPError we read the headers off.
        """
        request = urllib.request.Request(self.base + path, data=b"", method="POST")
        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location")
        return response.status, response.headers.get("Location")

    def test_opens_the_browser_at_the_served_url(self):
        argv, check = self.runner.calls[0]
        self.assertEqual(argv, ["xdg-open", self.base + "/"])
        self.assertTrue(check)

    def test_serves_the_gallery_with_unstar_buttons(self):
        status, body = self.get("/")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('action="/unstar/101"', page)
        self.assertIn('action="/unstar/102"', page)

    def test_serves_the_archived_paintings(self):
        status, body = self.get("/images/Q101.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, IMAGE_BYTES)

    def test_unstarring_trashes_the_painting_and_redirects_to_a_clean_url(self):
        status, location = self.post("/unstar/101")

        self.assertEqual(status, 303)  # so a reload can't repeat the post
        self.assertEqual(location, "/")  # no ?removed= left in the address bar
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [102])
        self.assertFalse(self.cfg.star_image(101).exists())
        self.assertTrue(self.cfg.star_image(102).exists())  # the other one is untouched
        # nothing is deleted: the image is parked and the record remembers its place
        self.assertEqual(self.cfg.trash_image(101).read_bytes(), IMAGE_BYTES)
        self.assertEqual(stars.load_trash(self.cfg), [{"index": 0, "star": star_of(101)}])

    def test_the_undo_banner_is_a_flash_shown_once(self):
        self.post("/unstar/101")
        _status, first = self.get("/")
        _status, second = self.get("/")

        self.assertIn('action="/restore/101"', first.decode())  # offered on the next render
        self.assertNotIn('class="flash"', second.decode())  # never again — reload is clean

    def test_fetching_a_thumbnail_does_not_swallow_the_flash(self):
        # the browser fetches images right after the page; only a page consumes the flash
        self.post("/unstar/101")
        self.get("/images/Q102.jpg")
        _status, body = self.get("/")
        self.assertIn('action="/restore/101"', body.decode())

    def test_restore_still_works_from_a_page_whose_flash_is_spent(self):
        # you loaded the banner, then reloaded elsewhere; the button must still work
        self.post("/unstar/101")
        self.get("/")  # consumes the flash
        self.get("/")  # banner gone from the UI
        self.post("/restore/101")  # ...but the stale page's button still posts
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101, 102])

    def test_restore_returns_the_painting_its_image_and_its_position(self):
        self.post("/unstar/101")  # 101 was first in the list
        status, location = self.post("/restore/101")

        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101, 102])  # order kept
        self.assertEqual(self.cfg.star_image(101).read_bytes(), IMAGE_BYTES)
        self.assertFalse(self.cfg.trash_image(101).exists())  # moved back out of the trash
        self.assertEqual(stars.load_trash(self.cfg), [])

    def test_restoring_replaces_the_pending_undo_with_its_own_flash(self):
        self.post("/unstar/101")
        self.post("/restore/101")  # before the banner was ever rendered
        _status, body = self.get("/")
        page = body.decode()
        self.assertIn("Restored <i>Painting 101</i>.", page)
        self.assertNotIn('action="/restore/101"', page.split("</div>")[0])  # no stale undo

    def test_a_painting_can_only_be_restored_once(self):
        self.post("/unstar/101")
        self.post("/restore/101")
        status, _location = self.post("/restore/101")  # no longer in the trash
        self.assertEqual(status, 404)

    def test_unstarring_a_painting_that_is_not_starred_is_not_found(self):
        status, _location = self.post("/unstar/999")
        self.assertEqual(status, 404)

    def test_restore_after_a_second_unstar_still_returns_the_right_one(self):
        self.post("/unstar/101")
        self.post("/unstar/102")
        self.post("/restore/101")

        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101])
        self.assertTrue(self.cfg.star_image(101).exists())
        self.assertFalse(self.cfg.star_image(102).exists())  # still trashed
        self.assertEqual([t["star"]["qid"] for t in stars.load_trash(self.cfg)], [102])

    def test_unstarring_updates_the_archived_page_too(self):
        self.post("/unstar/101")
        self.assertNotIn("Q101.jpg", self.cfg.stars_page.read_text())
        self.post("/restore/101")
        self.assertIn("Q101.jpg", self.cfg.stars_page.read_text())

    def test_serves_the_trash_page_with_restore_buttons(self):
        self.post("/unstar/101")
        _status, body = self.get("/trash")
        page = body.decode()
        self.assertIn("1 painting in the trash", page)
        self.assertIn('action="/restore/101"', page)
        self.assertIn('<img src="/trash/images/Q101.jpg"', page)

    def test_serves_trashed_images(self):
        self.post("/unstar/101")
        status, body = self.get("/trash/images/Q101.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, IMAGE_BYTES)

    def test_the_gallery_links_to_the_trash_once_it_has_something(self):
        _status, before = self.get("/")
        self.assertNotIn('href="/trash"', before.decode())
        self.post("/unstar/101")
        self.get("/")  # burn the flash so it doesn't confuse the assertion
        _status, after = self.get("/")
        self.assertIn('<a href="/trash">Trash (1 painting)</a>', after.decode())

    def test_emptying_the_trash_deletes_everything_in_it(self):
        self.post("/unstar/101")
        self.post("/unstar/102")
        status, location = self.post("/trash/empty")

        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertFalse(self.cfg.trash_dir.exists())
        self.assertEqual(stars.load_trash(self.cfg), [])
        self.assertEqual(stars.load(self.cfg), [])  # they were unstarred, and now gone
        status, _location = self.post("/restore/101")
        self.assertEqual(status, 404)  # nothing left to restore

    def test_emptying_the_trash_says_how_many_it_deleted(self):
        self.post("/unstar/101")
        self.post("/unstar/102")
        self.post("/trash/empty")
        _status, body = self.get("/")
        self.assertIn("Deleted 2 paintings for good.", body.decode())

    def test_emptying_an_empty_trash_is_harmless(self):
        status, _location = self.post("/trash/empty")
        self.assertEqual(status, 303)
        _status, body = self.get("/")
        self.assertIn("Deleted 0 paintings for good.", body.decode())

    def test_the_trash_survives_the_gallery_closing(self):
        # the whole point of a durable trash: restore it days later, in a new process
        self.post("/unstar/101")
        self.server.shutdown()
        self.server.server_close()

        reopened = stars.serve_gallery(self.cfg, Recorder())
        self.addCleanup(reopened.server_close)

        self.assertEqual([t["star"]["qid"] for t in stars.load_trash(self.cfg)], [101])
        self.assertEqual(self.cfg.trash_image(101).read_bytes(), IMAGE_BYTES)
        stars.restore(self.cfg, 101)  # still restorable in the new session
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101, 102])

    def test_does_not_serve_the_rest_of_the_filesystem(self):
        for path in ("/stars.json", "/images/../stars.json", "/etc/passwd", "/.trash/trash.json"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_an_unknown_post_is_not_found(self):
        status, _location = self.post("/nope")
        self.assertEqual(status, 404)


class StarFromLink(unittest.TestCase):
    """`star_link()` against a real loopback Wikidata/Commons — no mocks."""

    def setUp(self):
        self.cache_dir = Path(tempfile.mkdtemp())
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def config(self, **router_kwargs):
        router = wikidata_router([101], **router_kwargs)
        server = self.stack.enter_context(serve(router))
        router.base = server.base_url
        return dataclasses.replace(
            config_for(server, self.cache_dir), data_dir=self.data_dir
        )

    def test_stars_a_pasted_painting_and_archives_the_image(self):
        cfg = self.config()
        record, outcome = stars.star_link(cfg, link_to(101))

        self.assertEqual(outcome, "starred")
        self.assertEqual(record["title"], "Painting 101")
        self.assertEqual([s["qid"] for s in stars.load(cfg)], [101])
        self.assertEqual(cfg.star_image(101).read_bytes(), IMAGE_BYTES)

    def test_the_pasted_record_is_shaped_like_every_other_star(self):
        cfg = self.config()
        record, _ = stars.star_link(cfg, link_to(101))
        self.assertEqual(set(record), set(star_of(101)))

    def test_pasting_a_painting_that_is_already_hung_changes_nothing(self):
        # not a toggle: you paste a link to *add*, so a repeat must not remove it
        cfg = self.config()
        stars.star_link(cfg, link_to(101))
        _record, outcome = stars.star_link(cfg, link_to(101))

        self.assertEqual(outcome, "already")
        self.assertEqual([s["qid"] for s in stars.load(cfg)], [101])
        self.assertTrue(cfg.star_image(101).exists())

    def test_pasting_a_trashed_painting_restores_it(self):
        # adding it afresh would leave the trash holding the same QID, and
        # restoring that later would hang a second copy of the painting
        cfg = self.config()
        stars.star_link(cfg, link_to(101))
        stars.unstar(cfg, 101)

        _record, outcome = stars.star_link(cfg, link_to(101))

        self.assertEqual(outcome, "restored")
        self.assertEqual([s["qid"] for s in stars.load(cfg)], [101])
        self.assertEqual(stars.load_trash(cfg), [])
        self.assertEqual(cfg.star_image(101).read_bytes(), IMAGE_BYTES)
        self.assertFalse(cfg.trash_image(101).exists())

    def test_a_bad_link_raises_rather_than_starring_anything(self):
        cfg = self.config()
        with self.assertRaises(app.LinkError):
            stars.star_link(cfg, "https://en.wikipedia.org/wiki/Muqi")
        self.assertEqual(stars.load(cfg), [])


class PasteIntoGallery(unittest.TestCase):
    """The paste box, driven over real HTTP against the real gallery server."""

    def setUp(self):
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.cache_dir = Path(tempfile.mkdtemp())
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

        router = wikidata_router([101, 102])
        wiki = self.stack.enter_context(serve(router))
        router.base = wiki.base_url
        self.cfg = dataclasses.replace(
            config_for(wiki, self.cache_dir), data_dir=self.data_dir
        )

        self.server = stars.serve_gallery(self.cfg, Recorder())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.url.rstrip("/")

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.read().decode()

    def paste(self, link):
        """Submit the paste form exactly as a browser does, without following the 303."""
        body = urllib.parse.urlencode({"link": link}).encode()
        request = urllib.request.Request(
            self.base + "/star",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location")
        return response.status, response.headers.get("Location")

    def test_the_paste_box_is_on_the_served_page(self):
        page = self.get("/")
        self.assertIn('<form class="add" method="post" action="/star">', page)
        self.assertIn('name="link"', page)

    def test_the_archived_page_has_no_paste_box(self):
        # nothing would answer the post once the command exits
        self.assertNotIn('action="/star"', self.cfg.stars_page.read_text())

    def test_pasting_a_link_hangs_the_painting(self):
        status, location = self.paste(link_to(101))

        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101])
        self.assertEqual(self.cfg.star_image(101).read_bytes(), IMAGE_BYTES)

    def test_the_new_painting_appears_in_the_gallery(self):
        self.paste(link_to(101))
        page = self.get("/")
        self.assertIn('<img src="images/Q101.jpg"', page)
        self.assertIn("Starred <i>Painting 101</i>.", page)

    def test_pasting_updates_the_archived_page_too(self):
        self.paste(link_to(101))
        self.assertIn("Q101.jpg", self.cfg.stars_page.read_text())

    def test_a_repeat_paste_says_so_instead_of_unstarring(self):
        self.paste(link_to(101))
        self.get("/")  # burn the first flash
        self.paste(link_to(101))

        self.assertIn("Already in the gallery: <i>Painting 101</i>.", self.get("/"))
        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [101])

    def test_pasting_a_trashed_painting_reports_the_restore(self):
        self.paste(link_to(101))
        stars.unstar(self.cfg, 101)
        self.paste(link_to(101))

        self.assertIn("Restored from the trash: <i>Painting 101</i>.", self.get("/"))

    def test_a_link_to_no_image_comes_back_as_a_message_not_an_error(self):
        # typed input: a bad link is expected, and must not replace the gallery
        status, _location = self.paste("https://en.wikipedia.org/wiki/Muqi")
        self.assertEqual(status, 303)
        page = self.get("/")
        self.assertIn("Couldn&#x27;t add that link", page)
        self.assertIn("doesn&#x27;t point at an image", page)
        self.assertEqual(stars.load(self.cfg), [])

    def test_an_empty_paste_is_reported_the_same_way(self):
        self.paste("")
        self.assertIn("Couldn&#x27;t add that link", self.get("/"))

    def test_the_paste_box_shows_on_an_empty_gallery_too(self):
        # it's the one place you'd look when there's nothing starred yet
        page = self.get("/")
        self.assertIn("Nothing starred yet", page)
        self.assertIn('action="/star"', page)


class StopPrevious(unittest.TestCase):
    """Driven against real spawned processes — no mocks, no patched os.kill."""

    def setUp(self):
        self.cfg = Config(cache_dir=Path(tempfile.mkdtemp()))
        self.cfg.cache_dir.mkdir(parents=True, exist_ok=True)

    def spawn(self, argv_tail, trap=False):
        """A real, live process whose /proc cmdline we control.

        The extra argv after -c lands in sys.argv, so the cmdline can be made to
        look like a gallery server (or deliberately not like one) while the
        process is genuinely running.

        A `trap`ping child ignores SIGTERM — but only once it has run, so it
        writes a ready file first and we wait for it. Signalling before then
        would hit the default handler and kill the very process meant to survive.
        """
        ready = Path(tempfile.mkdtemp()) / "ready"
        code = (
            "import signal, sys, time, pathlib; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path(sys.argv[-1]).write_text('ok'); "
            "time.sleep(60)"
            if trap
            else "import sys, time, pathlib; "
            "pathlib.Path(sys.argv[-1]).write_text('ok'); "
            "time.sleep(60)"
        )
        process = subprocess.Popen([sys.executable, "-c", code, *argv_tail, str(ready)])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)

        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"spawned process exited early ({process.returncode})")
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "spawned process never started")
        return process

    def write_pid(self, pid):
        self.cfg.stars_pid.write_text(str(pid))

    def test_no_pid_file_means_nothing_to_stop(self):
        self.assertIsNone(stars.stop_previous(self.cfg))

    def test_stops_a_running_gallery(self):
        process = self.spawn(["-m", "artwall", "--stars"])
        self.write_pid(process.pid)

        self.assertEqual(stars.stop_previous(self.cfg), process.pid)
        self.assertIsNotNone(process.wait(timeout=5))  # really gone

    def test_a_stale_pid_file_is_harmless(self):
        process = self.spawn(["-m", "artwall", "--stars"])
        pid = process.pid
        process.kill()
        process.wait()
        self.write_pid(pid)

        self.assertIsNone(stars.stop_previous(self.cfg))

    def test_a_recycled_pid_is_not_killed(self):
        # the PID is live, but it belongs to something else entirely now
        process = self.spawn(["something", "else"])
        self.write_pid(process.pid)

        self.assertIsNone(stars.stop_previous(self.cfg))
        self.assertIsNone(process.poll())  # untouched

    def test_our_own_pid_is_never_signalled(self):
        self.write_pid(os.getpid())
        self.assertIsNone(stars.stop_previous(self.cfg))

    def test_a_gallery_that_will_not_exit_is_reported(self):
        process = self.spawn(["-m", "artwall", "--stars"], trap=True)
        self.write_pid(process.pid)

        with self.assertRaises(RuntimeError) as cm:
            stars.stop_previous(self.cfg, timeout=0.2)
        self.assertIn(str(process.pid), str(cm.exception))
        self.assertIsNone(process.poll())  # still there, still ignoring us


class IsGallery(unittest.TestCase):
    def test_recognises_a_gallery_server(self):
        self.assertTrue(stars.is_gallery("/usr/bin/python3\0-m\0artwall\0--stars\0"))

    def test_rejects_another_artwall_process(self):
        # the wallpaper oneshot and the overlay must survive a --stars
        self.assertFalse(stars.is_gallery("/usr/bin/python3\0-m\0artwall\0--throttle\0"))
        self.assertFalse(stars.is_gallery("/usr/bin/python3\0-m\0artwall.overlay\0"))

    def test_rejects_an_unrelated_process(self):
        self.assertFalse(stars.is_gallery("/usr/bin/firefox\0--stars\0"))

    def test_a_dead_process_has_no_cmdline(self):
        self.assertEqual(stars._cmdline(-1), "")


class GalleryPidFile(unittest.TestCase):
    def test_serving_records_the_pid_and_replaces_a_previous_gallery(self):
        cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()),
            data_dir=Path(tempfile.mkdtemp()) / "artwall",
        )
        first = stars.serve_gallery(cfg, Recorder())
        self.addCleanup(first.server_close)
        self.assertEqual(cfg.stars_pid.read_text(), str(os.getpid()))

        # a second serve in this same process must not signal itself
        second = stars.serve_gallery(cfg, Recorder())
        self.addCleanup(second.server_close)
        self.assertEqual(cfg.stars_pid.read_text(), str(os.getpid()))


class TrashFile(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )

    def test_an_absent_trash_reads_as_empty(self):
        self.assertEqual(stars.load_trash(self.cfg), [])
        self.assertFalse(stars.in_trash(self.cfg, 101))

    def test_emptying_an_absent_trash_is_a_no_op(self):
        self.assertEqual(stars.empty_trash(self.cfg), 0)
        self.assertFalse(self.cfg.trash_dir.exists())

    def test_restoring_into_a_gallery_that_shrank_clamps_the_index(self):
        # trashed from position 5, but only one painting is left: it goes at the end
        image = self.cfg.trash_image(101)
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(102)])
        stars.save_trash(self.cfg, [{"index": 5, "star": star_of(101)}])

        stars.restore(self.cfg, 101)

        self.assertEqual([s["qid"] for s in stars.load(self.cfg)], [102, 101])


class SessionTests(unittest.TestCase):
    def test_the_flash_is_read_once(self):
        session = stars.Session()
        session.flash = stars.Flash("Removed", "Painting 101", 101)
        self.assertEqual(session.take_flash().undo_qid, 101)
        self.assertIsNone(session.take_flash())

    def test_a_fresh_session_has_no_flash(self):
        self.assertIsNone(stars.Session().take_flash())


if __name__ == "__main__":
    unittest.main()
