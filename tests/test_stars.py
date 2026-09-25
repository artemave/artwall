import contextlib
import dataclasses
import json
import os
import random
import re
import shutil
import subprocess
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
    fake_desktop,
    link_to,
    wikidata_router,
)


def star_of(qid, **overrides):
    """A star record shaped exactly as `selection.record()` builds it.

    Takes the bare number and namespaces it, since most stars come from Wikidata;
    pass `key=` to build a Commons-derived one (`M<pageid>`).
    """
    return {
        "key": f"Q{qid}",
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
        self.assertEqual([s["key"] for s in result], ["Q101"])

    def test_appends_so_the_list_stays_oldest_first(self):
        result, _ = stars.toggle([star_of(101)], star_of(102))
        self.assertEqual([s["key"] for s in result], ["Q101", "Q102"])

    def test_removes_a_painting_that_is_already_starred(self):
        result, starred = stars.toggle([star_of(101), star_of(102)], star_of(101))
        self.assertFalse(starred)
        self.assertEqual([s["key"] for s in result], ["Q102"])

    def test_matches_on_qid_not_identity(self):
        # the record the overlay hands back is re-read from disk, so it's a distinct
        # dict — only the QID can decide whether it's already starred.
        result, starred = stars.toggle([star_of(101)], star_of(101, title="Renamed"))
        self.assertFalse(starred)
        self.assertEqual(result, [])

    def test_is_starred(self):
        self.assertTrue(stars.is_starred([star_of(101)], "Q101"))
        self.assertFalse(stars.is_starred([star_of(101)], "Q102"))


class RenderPage(unittest.TestCase):
    def test_empty_gallery_explains_itself(self):
        page = stars.render_page([])
        self.assertIn("Nothing starred yet", page)
        self.assertNotIn("<figure>", page)

    def test_counts_the_paintings_and_pluralises(self):
        self.assertIn("★ 1 starred painting<", stars.render_page([star_of(101)]))
        self.assertIn("★ 2 starred paintings<", stars.render_page([star_of(101), star_of(102)]))

    def test_an_owner_is_named_beside_the_star(self):
        page = stars.render_page([star_of(101)], owner="Alex")
        heading = page.split("<h1>")[1].split("</h1>")[0]
        self.assertEqual(heading, "★ Alex starred 1 painting")

    def test_an_owner_with_no_stars_drops_the_count_entirely(self):
        page = stars.render_page([], owner="Alex")
        self.assertIn("★ Alex starred paintings<", page)

    def test_no_owner_configured_keeps_the_generic_heading(self):
        self.assertIn("★ 1 starred painting<", stars.render_page([star_of(101)], owner=""))

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
        self.assertIn('<form class="corner" method="post" action="/unstar/Q101">', served)

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

    def test_the_sync_button_appears_only_when_interactive_and_a_git_repo(self):
        stars_ = [star_of(101)]
        self.assertNotIn("/sync", stars.render_page(stars_))  # not served
        self.assertNotIn("/sync", stars.render_page(stars_, interactive=True))  # not a git repo
        self.assertNotIn(
            "/sync", stars.render_page(stars_, git_sync=True)
        )  # a git repo, but archived
        served = stars.render_page(stars_, interactive=True, git_sync=True)
        self.assertIn('<form class="sync" method="post" action="/sync">', served)

    def test_the_sync_button_is_disabled_when_nothing_is_pending(self):
        served = stars.render_page(
            [star_of(101)], interactive=True, git_sync=True, sync_pending=False
        )
        self.assertIn('<button type="submit" disabled>⇪ Sync</button>', served)

    def test_the_sync_button_is_enabled_when_something_is_pending(self):
        served = stars.render_page(
            [star_of(101)], interactive=True, git_sync=True, sync_pending=True
        )
        self.assertIn('<button type="submit">⇪ Sync</button>', served)

    def test_the_sync_button_and_the_trash_link_share_one_spacer(self):
        # each is independently optional; the heading must still read right with
        # both, either alone, or neither
        page = stars.render_page(
            [star_of(101)], interactive=True, git_sync=True, trash_count=1
        )
        self.assertEqual(page.count('<span class="spacer">'), 1)

    def test_the_published_address_is_shown_when_configured(self):
        page = stars.render_page([star_of(101)], public_url="https://art.example.com/")
        self.assertIn(
            '<a class="published" href="https://art.example.com/" '
            'target="_blank" rel="noopener noreferrer">https://art.example.com/ ↗</a>',
            page,
        )

    def test_no_link_when_no_address_is_configured(self):
        # the default: nothing published, nothing to point at. Matched on the
        # markup, not the word — the shared CSS carries a `.published` rule.
        self.assertNotIn('<a class="published"', stars.render_page([star_of(101)]))

    def test_the_archived_page_carries_it_too(self):
        # stars.html is also a page where you're looking at your own collection
        page = stars.render_page([star_of(101)], interactive=False, public_url="https://x.test/")
        self.assertIn('href="https://x.test/"', page)

    def test_it_is_shown_on_an_empty_gallery_as_well(self):
        self.assertIn('href="https://x.test/"', stars.render_page([], public_url="https://x.test/"))

    def test_the_address_is_escaped(self):
        page = stars.render_page([], public_url='https://x.test/"><script>alert(1)</script>')
        self.assertNotIn("<script>", page)

    def test_the_published_page_does_not_link_to_itself(self):
        # render_public IS the live site; a link back to its own address is noise
        self.assertNotIn('<a class="published"', stars.render_public([star_of(101)]))

    def test_the_archived_page_never_links_to_the_trash(self):
        # nothing would serve /trash once the command exits
        page = stars.render_page([star_of(101)], interactive=False, trash_count=3)
        self.assertNotIn("/trash", page)

    def test_the_undo_banner_names_the_painting_and_posts_back(self):
        flash = stars.Flash("Removed", "Painting 101", "Q101")
        page = stars.render_page([star_of(102)], interactive=True, flash=flash)
        self.assertIn("Removed <i>Painting 101</i>.", page)
        self.assertIn('<form method="post" action="/restore/Q101">', page)

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
        flash = stars.Flash("Removed", "Painting 102", "Q102")
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
        page = stars.render_page([], interactive=True, flash=stars.Flash("Removed", "X", "Q101"))
        self.assertIn("Nothing starred yet", page)
        self.assertIn('action="/restore/Q101"', page)


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
        self.assertIn('action="/restore/Q101"', page)
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
        return app.run(cfg, random.Random(0), Recorder(), fake_desktop("DP-1"))[0]

    def test_stars_the_current_painting_and_archives_its_image(self):
        router = wikidata_router([101])
        with serve(router) as s:
            router.base = s.base_url
            cfg = self.config(s)
            qid = self.set_a_wallpaper(cfg)
            starred = stars.star(cfg, output="DP-1")

        self.assertTrue(starred)
        self.assertEqual([s["key"] for s in stars.load(cfg)], [f"Q{qid}"])
        # the painting itself is archived beside the list, ready to be backed up
        self.assertEqual(cfg.star_image(f"Q{qid}").read_bytes(), IMAGE_BYTES)
        self.assertEqual(cfg.star_image(f"Q{qid}"), self.data_dir / "images" / f"Q{qid}.jpg")

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
        self.assertFalse(cfg.star_image(f"Q{qid}").exists())

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

        self.assertEqual([s["key"] for s in stars.load(cfg)], [f"Q{qid}"])
        self.assertTrue(cfg.star_image(f"Q{qid}").exists())

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


class RenderPublic(unittest.TestCase):
    """The page meant for the open internet: a list, and nothing that acts on it."""

    def test_the_grid_loads_the_web_sized_copy_not_the_archive(self):
        # the whole reason the published site exists: a page of 2560px museum scans
        # is tens of megabytes, which is the difference between usable and not on a
        # phone. The archive is still one tap away.
        page = stars.render_public([star_of(101)])
        self.assertIn('<img src="thumbs/Q101.jpg"', page)
        self.assertIn('<a class="art" href="images/Q101.jpg">', page)

    def test_every_path_is_relative_so_the_directory_uploads_anywhere(self):
        page = stars.render_public([star_of(101)])
        self.assertNotIn('src="/', page)
        self.assertNotIn('href="/', page)  # works from a subdirectory of a host too

    def test_nothing_on_the_page_can_change_anything(self):
        page = stars.render_public([star_of(101), star_of(102)])
        self.assertNotIn("<form", page)  # no unstar, no paste box, no delete
        self.assertNotIn("/trash", page)
        self.assertNotIn("<script", page)

    def test_it_still_credits_and_links_each_painting(self):
        page = stars.render_public([star_of(101)])
        self.assertIn("Rembrandt", page)
        self.assertIn('href="https://en.wikipedia.org/wiki/Painting_101"', page)

    def test_it_is_headed_like_the_rest_of_the_gallery(self):
        self.assertIn("★ 1 starred painting<", stars.render_public([star_of(101)]))
        self.assertIn("★ 2 starred paintings<", stars.render_public([star_of(101), star_of(102)]))

    def test_the_empty_state_does_not_talk_about_the_desktop(self):
        # "click the ★ next to a wallpaper caption" means nothing to a visitor
        page = stars.render_public([])
        self.assertIn("No paintings here yet", page)
        self.assertNotIn("wallpaper", page)

    def test_it_declares_a_viewport_so_phones_do_not_zoom_out(self):
        self.assertIn('name="viewport" content="width=device-width', stars.render_public([]))

    def test_the_tab_is_named_for_the_thing_not_the_count(self):
        # a bookmark or a search result wants the gallery's name, not a number that
        # changes every time a painting is starred
        page = stars.render_public([star_of(101), star_of(102)])
        self.assertIn("<title>artwall - stars</title>", page)
        self.assertIn("★ 2 starred paintings<", page)  # the heading still counts

    def test_an_owner_names_the_tab_too_but_still_not_the_count(self):
        page = stars.render_public([star_of(101), star_of(102)], owner="Alex")
        self.assertIn("<title>Alex starred paintings - artwall</title>", page)
        self.assertIn("★ Alex starred 2 paintings<", page)  # the heading still counts

    def test_it_credits_the_tool_that_built_it(self):
        page = stars.render_public([star_of(101)])
        self.assertIn(
            '<footer><p>Starred with <a href="https://github.com/artemave/artwall" '
            'target="_blank" rel="noopener noreferrer">artwall</a>.</p>',
            page,
        )

    def test_it_credits_wikidata_and_wikimedia_commons(self):
        # a stranger who found this page has no other way to know where the
        # paintings and their data actually come from
        page = stars.render_public([star_of(101)])
        self.assertIn(
            '<a href="https://www.wikidata.org/" target="_blank" '
            'rel="noopener noreferrer">Wikidata</a>',
            page,
        )
        self.assertIn(
            '<a href="https://commons.wikimedia.org/" target="_blank" '
            'rel="noopener noreferrer">Wikimedia Commons</a>',
            page,
        )

    def test_the_two_credits_are_on_separate_lines(self):
        page = stars.render_public([star_of(101)])
        footer = page.split("<footer>")[1].split("</footer>")[0]
        self.assertEqual(footer.count("<p>"), 2)

    def test_the_empty_page_is_credited_too(self):
        self.assertIn("<footer>", stars.render_public([]))

    def test_only_the_published_page_carries_the_footer(self):
        # the local gallery has no strangers to introduce itself to
        self.assertNotIn("<footer>", stars.render_page([star_of(101)], interactive=True))
        self.assertNotIn("<footer>", stars.render_page([star_of(101)]))
        self.assertNotIn("<footer>", stars.render_trash([]))

    def test_the_tab_title_does_not_carry_the_count(self):
        # same reasoning as render_public()'s PUBLIC_TITLE: a tab or a bookmark
        # wants something that doesn't change on every star
        one = stars.render_page([star_of(101)])
        two = stars.render_page([star_of(101), star_of(102)])
        self.assertIn("<title>★ Starred paintings</title>", one)
        self.assertIn("<title>★ Starred paintings</title>", two)
        self.assertIn("★ 1 starred painting<", one)  # the heading still counts
        self.assertIn("★ 2 starred paintings<", two)

    def test_the_tab_title_with_an_owner_names_them_but_not_the_count(self):
        page = stars.render_page([star_of(101), star_of(102)], owner="Alex")
        self.assertIn("<title>★ Alex starred paintings</title>", page)
        self.assertIn("★ Alex starred 2 paintings<", page)

    def test_the_theme_colour_is_the_page_background(self):
        # Safari paints its own chrome from theme-color. Told nothing, it derives a
        # near-miss of its own (a ~9% light layer over the page) and the gallery ends
        # in a visibly paler band. Told the background, the seam disappears.
        for page in (stars.render_public([]), stars.render_page([]), stars.render_trash([])):
            self.assertIn(
                '<meta name="theme-color" media="(prefers-color-scheme: light)" '
                f'content="{stars.BG_LIGHT}">',
                page,
            )
            self.assertIn(
                '<meta name="theme-color" media="(prefers-color-scheme: dark)" '
                f'content="{stars.BG_DARK}">',
                page,
            )

    def test_the_theme_colour_cannot_drift_from_the_css(self):
        # two declarations of one colour, and only a phone would show them differing
        self.assertIn(f"--bg: {stars.BG_LIGHT};", stars.CSS)
        self.assertIn(f"--bg: {stars.BG_DARK};", stars.CSS)

    def test_the_root_carries_the_background_not_just_the_body(self):
        # iOS paints the strip behind its toolbar from the canvas background, which
        # comes from the root element. Set on <body> alone, a dark gallery ends in a
        # pale grey band on a real iPhone — which no desktop browser reproduces.
        self.assertIn("html { background: var(--bg); }", stars.render_public([]))

    def test_hover_only_polish_is_gated_away_from_touchscreens(self):
        # a tap leaves :hover stuck on the thing you tapped, so a hover *reveal*
        # would stay revealed on one painting for the whole visit
        page = stars.render_public([star_of(101)])
        gated = page.split("@media (hover: hover)")[1]
        for rule in [".art:hover img", ".title:hover"]:
            self.assertIn(rule, gated)


class Publish(unittest.TestCase):
    """`--publish`: the self-contained directory you upload."""

    def setUp(self):
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        self.cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=self.data_dir)
        self.runner = Recorder()

    def archive(self, *keys):
        """Star some paintings, with the archived image each one needs."""
        self.cfg.star_image("x").parent.mkdir(parents=True, exist_ok=True)
        for key in keys:
            self.cfg.star_image(key).write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(0, key=key) for key in keys])

    def test_builds_a_page_the_images_and_the_thumbnails(self):
        self.archive("Q101", "Q102")

        out = stars.publish(self.cfg, self.runner)

        self.assertEqual(out, self.data_dir / "public")
        self.assertIn('<img src="thumbs/Q101.jpg"', (out / "index.html").read_text())
        # the full-size copies are real files, so the archive links resolve offline
        self.assertEqual((out / "images" / "Q101.jpg").read_bytes(), IMAGE_BYTES)
        self.assertEqual((out / "images" / "Q102.jpg").read_bytes(), IMAGE_BYTES)

    def test_it_is_index_html_so_a_host_serves_it_for_the_bare_url(self):
        self.archive("Q101")
        self.assertTrue((stars.publish(self.cfg, self.runner) / "index.html").exists())

    def test_a_thumbnail_is_shrunk_from_the_archive_at_the_configured_size(self):
        self.cfg.public_image_width = 900
        self.archive("Q101")

        stars.publish(self.cfg, self.runner)

        (argv, check) = self.runner.calls[0]
        self.assertTrue(check)
        self.assertEqual(argv[:2], ["magick", str(self.cfg.star_image("Q101"))])
        self.assertIn("900x900>", argv)  # fits inside the box, and only ever shrinks
        self.assertEqual(argv[-1], str(self.cfg.public_thumb("Q101")))

    def test_the_export_holds_nothing_but_paintings_and_the_page(self):
        # publishing data_dir itself would upload stars.json and the record of
        # every painting ever removed; this directory is why it doesn't.
        self.archive("Q101")
        self.cfg.trash_image("Q999").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.trash_image("Q999").write_bytes(IMAGE_BYTES)

        out = stars.publish(self.cfg, self.runner)

        self.assertEqual(sorted(p.name for p in out.iterdir()), ["images", "index.html", "thumbs"])
        self.assertEqual([p.name for p in (out / "images").iterdir()], ["Q101.jpg"])

    def test_republishing_does_not_rebuild_what_is_already_current(self):
        # a build you re-run should be cheap — otherwise every publish is a magick
        # process per painting for output that is already correct
        self.archive("Q101")
        stars.publish(self.cfg, self.runner)
        self.cfg.public_thumb("Q101").write_bytes(IMAGE_BYTES)  # the Recorder ran nothing

        stars.publish(self.cfg, self.runner)

        self.assertEqual(len(self.runner.calls), 1)

    def test_restarring_a_painting_rebuilds_its_export(self):
        self.archive("Q101")
        stars.publish(self.cfg, self.runner)
        self.cfg.public_thumb("Q101").write_bytes(b"stale")
        os.utime(self.cfg.star_image("Q101"), (time.time() + 10, time.time() + 10))

        stars.publish(self.cfg, self.runner)

        self.assertEqual(len(self.runner.calls), 2)

    def test_unstarring_removes_the_painting_from_the_published_site(self):
        # a file left behind goes on being served at its own URL long after the
        # painting stopped appearing on the page
        self.archive("Q101", "Q102")
        stars.publish(self.cfg, self.runner)
        self.cfg.public_thumb("Q102").write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [s for s in stars.load(self.cfg) if s["key"] == "Q101"])

        out = stars.publish(self.cfg, self.runner)

        self.assertFalse((out / "images" / "Q102.jpg").exists())
        self.assertFalse((out / "thumbs" / "Q102.jpg").exists())
        self.assertTrue((out / "images" / "Q101.jpg").exists())
        self.assertNotIn("Q102", (out / "index.html").read_text())

    def test_publishing_an_empty_gallery_still_produces_a_site(self):
        out = stars.publish(self.cfg, self.runner)
        self.assertIn("No paintings here yet", (out / "index.html").read_text())
        self.assertEqual(self.runner.calls, [])


class GitSync(unittest.TestCase):
    """`is_git_repo()` and `sync()` against a real git repo and a real bare
    remote — no mocks, matching how everything else here is tested."""

    def setUp(self):
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )
        self.remote = Path(tempfile.mkdtemp()) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.cfg.data_dir)], check=True)
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.email", "t@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.name", "Test"], check=True
        )

    def commits(self):
        log = subprocess.run(
            ["git", "-C", str(self.remote), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        return log.stdout.splitlines()

    def test_is_git_repo_is_false_with_no_git_directory(self):
        cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()))
        self.assertFalse(stars.is_git_repo(cfg))

    def test_is_git_repo_is_true_once_cloned(self):
        self.assertTrue(stars.is_git_repo(self.cfg))

    def test_sync_commits_and_pushes_a_new_file(self):
        (self.cfg.data_dir / "stars.json").write_text("[]")

        stars.sync(self.cfg)

        self.assertEqual(len(self.commits()), 1)

    def test_sync_with_nothing_changed_pushes_without_committing_again(self):
        (self.cfg.data_dir / "stars.json").write_text("[]")
        stars.sync(self.cfg)

        stars.sync(self.cfg)  # nothing changed the second time

        self.assertEqual(len(self.commits()), 1)  # no empty commit

    def test_sync_never_pushes_the_trash(self):
        # the template ships this .gitignore; sync() relies on the repo already
        # having it rather than filtering paths itself
        (self.cfg.data_dir / ".gitignore").write_text(".trash/\n")
        self.cfg.trash_image("Q999").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.trash_image("Q999").write_bytes(IMAGE_BYTES)

        stars.sync(self.cfg)

        show = subprocess.run(
            ["git", "-C", str(self.remote), "show", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertNotIn(".trash", show.stdout)

    def test_sync_without_a_git_repo_raises(self):
        cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()))
        with self.assertRaises(stars.SyncError):
            stars.sync(cfg)

    def test_sync_raises_with_gits_own_message_when_the_push_fails(self):
        (self.cfg.data_dir / "stars.json").write_text("[]")
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "remote", "set-url", "origin", "/nope"],
            check=True,
        )

        with self.assertRaises(stars.SyncError):
            stars.sync(self.cfg)


class SyncPending(unittest.TestCase):
    """`sync_pending()` — what disables the ⇪ Sync button."""

    def setUp(self):
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )
        remote = Path(tempfile.mkdtemp()) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
        subprocess.run(["git", "clone", str(remote), str(self.cfg.data_dir)], check=True)
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.email", "t@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.name", "Test"], check=True
        )
        # a baseline already on the remote, so "clean" reflects something real
        (self.cfg.data_dir / "stars.json").write_text("[]")
        stars.sync(self.cfg)

    def test_false_once_committed_and_pushed(self):
        self.assertFalse(stars.sync_pending(self.cfg))

    def test_true_with_an_uncommitted_change(self):
        (self.cfg.data_dir / "stars.json").write_text("[1]")
        self.assertTrue(stars.sync_pending(self.cfg))

    def test_true_with_an_untracked_file(self):
        (self.cfg.data_dir / "new.txt").write_text("x")
        self.assertTrue(stars.sync_pending(self.cfg))

    def test_true_with_a_commit_not_yet_pushed(self):
        (self.cfg.data_dir / "stars.json").write_text("[1]")
        subprocess.run(["git", "-C", str(self.cfg.data_dir), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.cfg.data_dir), "commit", "-m", "x"], check=True)

        self.assertTrue(stars.sync_pending(self.cfg))


class StartGallery(unittest.TestCase):
    """`start_gallery()` — what the overlay hosts, minus the browser launch."""

    def setUp(self):
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )
        self.runner = Recorder()

    def start(self, **kwargs):
        server = stars.start_gallery(self.cfg, runner=self.runner, **kwargs)
        self.addCleanup(server.server_close)
        return server

    def archive(self, *keys):
        self.cfg.star_image("x").parent.mkdir(parents=True, exist_ok=True)
        for key in keys:
            self.cfg.star_image(key).write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(0, key=key) for key in keys])

    def test_binds_without_opening_a_browser(self):
        # the overlay has a button for that; it must not launch one on login
        server = self.start(republish=False)
        self.assertTrue(server.url.startswith("http://127.0.0.1:"))
        self.assertEqual(self.runner.calls, [])

    def test_it_refreshes_the_archived_page(self):
        stars.save(self.cfg, [star_of(101)])
        self.start(republish=False)
        self.assertIn("Q101.jpg", self.cfg.stars_page.read_text())

    def test_one_gallery_is_structural_not_enforced(self):
        # there used to be a pid file and a SIGTERM dance here, because `--stars`
        # could be run twice. Only the overlay serves now, and only one of those
        # can exist, so a second server is not something to defend against.
        first, second = self.start(republish=False), self.start(republish=False)
        self.assertNotEqual(first.url, second.url)  # each binds its own port, fine
        self.assertFalse((self.cfg.cache_dir / "stars.pid").exists())

    def test_it_publishes_at_startup_by_default(self):
        # the site is rebuilt before anything is served, so a collection changed
        # while the overlay was down doesn't leave a stale page on the web
        self.archive("Q101")

        self.start()

        self.assertIn("Q101", self.cfg.public_page.read_text())

    def test_opting_out_builds_no_site_at_all(self):
        # --no-publish-stars: someone who never uploads shouldn't carry a second
        # copy of every painting on disk
        self.archive("Q101")
        self.start(republish=False)
        self.assertFalse(self.cfg.public_dir.exists())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An opener that reports a 3xx instead of chasing it."""

    def redirect_request(self, *_args):
        return None


class ServeGallery(unittest.TestCase):
    """Drives the real loopback server with real HTTP requests — no mocks."""

    def setUp(self):
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.data_dir = Path(tempfile.mkdtemp()) / "artwall"
        self.cfg = Config(cache_dir=Path(tempfile.mkdtemp()), data_dir=self.data_dir)
        self.cfg.star_image("Q101").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.star_image("Q101").write_bytes(IMAGE_BYTES)
        self.cfg.star_image("Q102").write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(101), star_of(102)])

        self.runner = Recorder()
        self.server = stars.start_gallery(self.cfg, republish=False, runner=self.runner)
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

    def test_binding_opens_no_browser_and_shells_out_to_nothing(self):
        # the overlay starts this at login; a browser tab per login would be rude,
        # and the caption's gallery button is what opens it on demand
        self.assertEqual(self.runner.calls, [])

    def test_serves_the_gallery_with_unstar_buttons(self):
        status, body = self.get("/")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('action="/unstar/Q101"', page)
        self.assertIn('action="/unstar/Q102"', page)

    def test_the_heading_uses_the_configured_owner(self):
        self.cfg.owner = "Alex"
        _status, body = self.get("/")
        self.assertIn("★ Alex starred 2 paintings<", body.decode())

    def test_serves_the_archived_paintings(self):
        status, body = self.get("/images/Q101.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, IMAGE_BYTES)

    def test_unstarring_trashes_the_painting_and_redirects_to_a_clean_url(self):
        status, location = self.post("/unstar/Q101")

        self.assertEqual(status, 303)  # so a reload can't repeat the post
        self.assertEqual(location, "/")  # no ?removed= left in the address bar
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q102"])
        self.assertFalse(self.cfg.star_image("Q101").exists())
        self.assertTrue(self.cfg.star_image("Q102").exists())  # the other one is untouched
        # nothing is deleted: the image is parked and the record remembers its place
        self.assertEqual(self.cfg.trash_image("Q101").read_bytes(), IMAGE_BYTES)
        self.assertEqual(stars.load_trash(self.cfg), [{"index": 0, "star": star_of(101)}])

    def test_the_undo_banner_is_a_flash_shown_once(self):
        self.post("/unstar/Q101")
        _status, first = self.get("/")
        _status, second = self.get("/")

        self.assertIn('action="/restore/Q101"', first.decode())  # offered on the next render
        self.assertNotIn('class="flash"', second.decode())  # never again — reload is clean

    def test_fetching_a_thumbnail_does_not_swallow_the_flash(self):
        # the browser fetches images right after the page; only a page consumes the flash
        self.post("/unstar/Q101")
        self.get("/images/Q102.jpg")
        _status, body = self.get("/")
        self.assertIn('action="/restore/Q101"', body.decode())

    def test_restore_still_works_from_a_page_whose_flash_is_spent(self):
        # you loaded the banner, then reloaded elsewhere; the button must still work
        self.post("/unstar/Q101")
        self.get("/")  # consumes the flash
        self.get("/")  # banner gone from the UI
        self.post("/restore/Q101")  # ...but the stale page's button still posts
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101", "Q102"])

    def test_restore_returns_the_painting_its_image_and_its_position(self):
        self.post("/unstar/Q101")  # 101 was first in the list
        status, location = self.post("/restore/Q101")

        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101", "Q102"])  # order kept
        self.assertEqual(self.cfg.star_image("Q101").read_bytes(), IMAGE_BYTES)
        self.assertFalse(self.cfg.trash_image("Q101").exists())  # moved back out of the trash
        self.assertEqual(stars.load_trash(self.cfg), [])

    def test_restoring_replaces_the_pending_undo_with_its_own_flash(self):
        self.post("/unstar/Q101")
        self.post("/restore/Q101")  # before the banner was ever rendered
        _status, body = self.get("/")
        page = body.decode()
        self.assertIn("Restored <i>Painting 101</i>.", page)
        self.assertNotIn('action="/restore/Q101"', page.split("</div>")[0])  # no stale undo

    def test_a_painting_can_only_be_restored_once(self):
        self.post("/unstar/Q101")
        self.post("/restore/Q101")
        status, _location = self.post("/restore/Q101")  # no longer in the trash
        self.assertEqual(status, 404)

    def test_unstarring_a_painting_that_is_not_starred_is_not_found(self):
        status, _location = self.post("/unstar/Q999")
        self.assertEqual(status, 404)

    def test_restore_after_a_second_unstar_still_returns_the_right_one(self):
        self.post("/unstar/Q101")
        self.post("/unstar/Q102")
        self.post("/restore/Q101")

        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101"])
        self.assertTrue(self.cfg.star_image("Q101").exists())
        self.assertFalse(self.cfg.star_image("Q102").exists())  # still trashed
        self.assertEqual([t["star"]["key"] for t in stars.load_trash(self.cfg)], ["Q102"])

    def test_unstarring_updates_the_archived_page_too(self):
        self.post("/unstar/Q101")
        self.assertNotIn("Q101.jpg", self.cfg.stars_page.read_text())
        self.post("/restore/Q101")
        self.assertIn("Q101.jpg", self.cfg.stars_page.read_text())

    def test_serves_the_trash_page_with_restore_buttons(self):
        self.post("/unstar/Q101")
        _status, body = self.get("/trash")
        page = body.decode()
        self.assertIn("1 painting in the trash", page)
        self.assertIn('action="/restore/Q101"', page)
        self.assertIn('<img src="/trash/images/Q101.jpg"', page)

    def test_serves_trashed_images(self):
        self.post("/unstar/Q101")
        status, body = self.get("/trash/images/Q101.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, IMAGE_BYTES)

    def test_the_gallery_links_to_the_trash_once_it_has_something(self):
        _status, before = self.get("/")
        self.assertNotIn('href="/trash"', before.decode())
        self.post("/unstar/Q101")
        self.get("/")  # burn the flash so it doesn't confuse the assertion
        _status, after = self.get("/")
        self.assertIn('<a href="/trash">Trash (1 painting)</a>', after.decode())

    def test_emptying_the_trash_deletes_everything_in_it(self):
        self.post("/unstar/Q101")
        self.post("/unstar/Q102")
        status, location = self.post("/trash/empty")

        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertFalse(self.cfg.trash_dir.exists())
        self.assertEqual(stars.load_trash(self.cfg), [])
        self.assertEqual(stars.load(self.cfg), [])  # they were unstarred, and now gone
        status, _location = self.post("/restore/Q101")
        self.assertEqual(status, 404)  # nothing left to restore

    def test_emptying_the_trash_says_how_many_it_deleted(self):
        self.post("/unstar/Q101")
        self.post("/unstar/Q102")
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
        self.post("/unstar/Q101")
        self.server.shutdown()
        self.server.server_close()

        reopened = stars.start_gallery(self.cfg, republish=False, runner=Recorder())
        self.addCleanup(reopened.server_close)

        self.assertEqual([t["star"]["key"] for t in stars.load_trash(self.cfg)], ["Q101"])
        self.assertEqual(self.cfg.trash_image("Q101").read_bytes(), IMAGE_BYTES)
        stars.restore(self.cfg, "Q101")  # still restorable in the new session
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101", "Q102"])

    def test_does_not_serve_the_rest_of_the_filesystem(self):
        for path in ("/stars.json", "/images/../stars.json", "/etc/passwd", "/.trash/trash.json"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_an_unknown_post_is_not_found(self):
        status, _location = self.post("/nope")
        self.assertEqual(status, 404)


class SyncRoute(unittest.TestCase):
    """POST /sync — the button a non-tech user clicks instead of a terminal."""

    def setUp(self):
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )
        self.remote = Path(tempfile.mkdtemp()) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.remote)], check=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.cfg.data_dir)], check=True)
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.email", "t@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "config", "user.name", "Test"], check=True
        )
        self.server = stars.start_gallery(self.cfg, republish=False, runner=Recorder())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.url.rstrip("/")

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read()

    def post(self, path):
        request = urllib.request.Request(self.base + path, data=b"", method="POST")
        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Location")
        return response.status, response.headers.get("Location")

    def commits(self):
        log = subprocess.run(
            ["git", "-C", str(self.remote), "log", "--oneline"],
            capture_output=True,
            text=True,
            check=True,
        )
        return log.stdout.splitlines()

    def test_the_button_appears_because_the_data_dir_is_a_git_repo(self):
        _status, body = self.get("/")
        self.assertIn('action="/sync"', body.decode())

    def test_the_button_starts_enabled(self):
        # start_gallery() already wrote stars.html, so there's something to send
        _status, body = self.get("/")
        self.assertIn('<button type="submit">⇪ Sync</button>', body.decode())

    def test_the_button_disables_once_there_is_nothing_left_to_send(self):
        self.post("/sync")
        _status, body = self.get("/")
        self.assertIn('<button type="submit" disabled>⇪ Sync</button>', body.decode())

    def test_posting_it_commits_and_pushes_and_redirects_home(self):
        status, location = self.post("/sync")
        self.assertEqual(status, 303)
        self.assertEqual(location, "/")
        self.assertEqual(len(self.commits()), 1)

    def test_it_does_not_republish(self):
        # the collection didn't change, so there's nothing for publish() to rebuild
        self.post("/sync")
        self.assertFalse(self.cfg.public_dir.exists())

    def test_a_flash_confirms_it_synced(self):
        self.post("/sync")
        _status, body = self.get("/")
        self.assertIn("Synced.", body.decode())

    def test_a_push_failure_is_shown_as_a_flash_not_a_crash(self):
        subprocess.run(
            ["git", "-C", str(self.cfg.data_dir), "remote", "set-url", "origin", "/nope"],
            check=True,
        )

        status, _location = self.post("/sync")

        self.assertEqual(status, 303)  # still redirects; the failure is a flash, not a 500
        _status, body = self.get("/")
        self.assertIn("Sync failed", body.decode())


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
        self.assertEqual([s["key"] for s in stars.load(cfg)], ["Q101"])
        self.assertEqual(cfg.star_image("Q101").read_bytes(), IMAGE_BYTES)

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
        self.assertEqual([s["key"] for s in stars.load(cfg)], ["Q101"])
        self.assertTrue(cfg.star_image("Q101").exists())

    def test_pasting_a_trashed_painting_restores_it(self):
        # adding it afresh would leave the trash holding the same QID, and
        # restoring that later would hang a second copy of the painting
        cfg = self.config()
        stars.star_link(cfg, link_to(101))
        stars.unstar(cfg, "Q101")

        _record, outcome = stars.star_link(cfg, link_to(101))

        self.assertEqual(outcome, "restored")
        self.assertEqual([s["key"] for s in stars.load(cfg)], ["Q101"])
        self.assertEqual(stars.load_trash(cfg), [])
        self.assertEqual(cfg.star_image("Q101").read_bytes(), IMAGE_BYTES)
        self.assertFalse(cfg.trash_image("Q101").exists())

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

        self.server = stars.start_gallery(self.cfg, republish=False, runner=Recorder())
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
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101"])
        self.assertEqual(self.cfg.star_image("Q101").read_bytes(), IMAGE_BYTES)

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
        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q101"])

    def test_pasting_a_trashed_painting_reports_the_restore(self):
        self.paste(link_to(101))
        stars.unstar(self.cfg, "Q101")
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


class ServeGalleryRepublishing(unittest.TestCase):
    """The gallery with `republish` on — what `--serve-stars --publish-stars` gives
    you. Every mutation has to reach the published site, not just `stars.json`."""

    def setUp(self):
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )
        self.cfg.star_image("Q101").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.star_image("Q101").write_bytes(IMAGE_BYTES)
        self.cfg.star_image("Q102").write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(101), star_of(102)])

        self.runner = Recorder()
        self.server = stars.start_gallery(self.cfg, republish=True, runner=self.runner)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.url.rstrip("/")

    def post(self, path):
        request = urllib.request.Request(self.base + path, data=b"", method="POST")
        with contextlib.suppress(urllib.error.HTTPError):
            self.opener.open(request)

    def site(self):
        return self.cfg.public_page.read_text()

    def test_the_site_exists_before_anything_is_clicked(self):
        self.assertIn("Q101", self.site())
        self.assertIn("Q102", self.site())

    def test_unstarring_removes_the_painting_from_the_published_site(self):
        # the point of the whole flag: a painting you took down stops being on
        # the web, without you having to remember to run --publish
        self.post("/unstar/Q102")

        self.assertNotIn("Q102", self.site())
        self.assertFalse(self.cfg.public_image("Q102").exists())
        self.assertIn("Q101", self.site())

    def test_restoring_puts_it_back_on_the_site(self):
        self.post("/unstar/Q102")
        self.post("/restore/Q102")

        self.assertIn("Q102", self.site())
        self.assertTrue(self.cfg.public_image("Q102").exists())

    def test_emptying_the_trash_leaves_the_site_alone(self):
        # the painting already left the site when it was unstarred; deleting the
        # trashed copy is not another change to what's published
        self.post("/unstar/Q102")
        self.post("/trash/empty")

        self.assertIn("Q101", self.site())
        self.assertNotIn("Q102", self.site())

    def test_opting_out_publishes_nothing(self):
        server = stars.start_gallery(self.cfg, republish=False, runner=Recorder())
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = server.url.rstrip("/")
        shutil.rmtree(self.cfg.public_dir)

        request = urllib.request.Request(base + "/unstar/Q101", data=b"", method="POST")
        with contextlib.suppress(urllib.error.HTTPError):
            self.opener.open(request)

        self.assertFalse(self.cfg.public_dir.exists())


class TrashFile(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(
            cache_dir=Path(tempfile.mkdtemp()), data_dir=Path(tempfile.mkdtemp()) / "artwall"
        )

    def test_an_absent_trash_reads_as_empty(self):
        self.assertEqual(stars.load_trash(self.cfg), [])
        self.assertFalse(stars.in_trash(self.cfg, "Q101"))

    def test_emptying_an_absent_trash_is_a_no_op(self):
        self.assertEqual(stars.empty_trash(self.cfg), 0)
        self.assertFalse(self.cfg.trash_dir.exists())

    def test_restoring_into_a_gallery_that_shrank_clamps_the_index(self):
        # trashed from position 5, but only one painting is left: it goes at the end
        image = self.cfg.trash_image("Q101")
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(IMAGE_BYTES)
        stars.save(self.cfg, [star_of(102)])
        stars.save_trash(self.cfg, [{"index": 5, "star": star_of(101)}])

        stars.restore(self.cfg, "Q101")

        self.assertEqual([s["key"] for s in stars.load(self.cfg)], ["Q102", "Q101"])


class SessionTests(unittest.TestCase):
    def test_the_flash_is_read_once(self):
        session = stars.Session()
        session.flash = stars.Flash("Removed", "Painting 101", "Q101")
        self.assertEqual(session.take_flash().undo_key, "Q101")
        self.assertIsNone(session.take_flash())

    def test_a_fresh_session_has_no_flash(self):
        self.assertIsNone(stars.Session().take_flash())


if __name__ == "__main__":
    unittest.main()
