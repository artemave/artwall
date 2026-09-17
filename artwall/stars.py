"""Starred paintings: the persisted list, the archived images, and the gallery.

Everything lives together under `config.data_dir` — `stars.json`, the paintings
in `images/`, and the `stars.html` that links to them by *relative* path. Copy or
back up that one directory and the gallery still opens, offline, anywhere.

Starring is driven by the interactive overlay, which shells out to
`artwall --star <output>` rather than downloading on its GTK main loop. The
overlay already has the whole painting record on screen (`run()` wrote it to
`caption-<output>.json`), so `star()` never has to look the painting up again —
it only fetches the image bytes.

**Why the gallery is a server.** The overlay's ★ can only unstar the painting
currently on that display, so the gallery has to be able to remove an older one —
and a `file://` page cannot delete a file. `start_gallery()` therefore renders the
page over a loopback `http.server` and takes unstar, restore and empty-trash as
plain form POSTs (no JavaScript; a 303 sends the browser back to `/`). The static
`stars.html` is still written, without those buttons — nothing would answer them
where there is no server, and its job is to make the backed-up directory readable
anywhere.

**The overlay owns it.** There is no standalone gallery command, so there is no
"replace the previous one" dance and no PID file: one overlay (guaranteed by its
own `supersede_running_instances()`) means one gallery, structurally. `publish()`
is called from here on every mutation, and from the overlay after a star, which is
why it takes a lock — those callers are on different threads.

**Unstarring is never destructive.** It *moves* the painting into `.trash/`
(a rename) and records the position it held, in `trash.json` beside it. So the
undo banner, the `/trash` page's Restore, and a rescue three days later all put
back the same bytes in the same place. Only `empty_trash()` deletes anything.

**The banner is a Rails-style flash** held in `Session`, shown once and cleared —
never a query parameter, which would linger in the address bar and re-offer the
undo on every reload.
"""
from __future__ import annotations

import functools
import html
import http.server
import re
import shutil
import subprocess
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from . import app, cache, commands, selection, web, wikidata
from .config import PUBLIC_THUMB_DIR, STARS_IMAGE_DIR, Config

Star = dict[str, Any]
# A trashed painting: the record, plus the index it held in the gallery.
Trashed = dict[str, Any]

# Loopback only. The gallery can delete your paintings; it is not for the network.
HOST = "127.0.0.1"

# Credited in the published page's footer. Not a config key: it names artwall
# itself, not anything about this collection or where it's hosted.
ARTWALL_URL = "https://github.com/artemave/artwall"
# Also credited there: every painting's data comes from Wikidata and its image
# from Wikimedia Commons, and a stranger who found this page has no other way
# to know that.
WIKIDATA_URL = "https://www.wikidata.org/"
WIKIMEDIA_COMMONS_URL = "https://commons.wikimedia.org/"
# The published page's <title>. The heading still counts the paintings, but a
# browser tab, a bookmark and a search result want the name of the thing, not a
# number that changes every time you star something.
PUBLIC_TITLE = "artwall - stars"

# One publish at a time. Its callers are concurrent — the gallery runs a thread per
# request, and the overlay publishes on its own thread after a star — and two builds
# at once would interleave one's `_prune` with the other's copies.
_PUBLISHING = threading.Lock()

# Masonry geometry, in px — must match `columns`/`column-gap` in CSS, because
# `_grid()` uses them to cap the container's width (see there for why).
COLUMN, GAP = 260, 32

# The only paths the gallery server answers. Anything else 404s — it serves the
# archived paintings, not the filesystem.
# A key is `Q<n>` (a Wikidata painting) or `M<n>` (a Commons file describing one
# in wikitext only) — see `selection.record`. Matching the prefix rather than bare
# digits is what keeps the two numbering spaces from colliding in a URL.
KEY = r"[QM]\d+"
IMAGE_PATH = re.compile(rf"^/{STARS_IMAGE_DIR}/(?P<key>{KEY})\.jpg$")
TRASH_IMAGE_PATH = re.compile(rf"^/trash/images/(?P<key>{KEY})\.jpg$")
UNSTAR_PATH = re.compile(rf"^/unstar/(?P<key>{KEY})$")
RESTORE_PATH = re.compile(rf"^/restore/(?P<key>{KEY})$")


class SyncError(Exception):
    """A commit-and-push that didn't reach the remote.

    Carries git's own stderr, so the gallery can show *why* — no remote
    configured, no network, a rejected non-fast-forward — rather than a bare
    "failed" to whoever clicked the button.
    """


class Flash(NamedTuple):
    """A one-shot message for the next render. `undo_key` adds an Undo button."""

    verb: str
    title: str = ""
    undo_key: str | None = None


class Session:
    """What the open gallery remembers between requests: just the pending flash.

    Everything else is on disk, so the trash survives a Ctrl-C and a reboot.
    """

    def __init__(self) -> None:
        self.flash: Flash | None = None

    def take_flash(self) -> Flash | None:
        """Read the pending message and clear it — it's shown once."""
        flash, self.flash = self.flash, None
        return flash


# The page background per colour scheme, handed to Safari as `theme-color` so it
# paints its own chrome to match instead of deriving a near-miss of its own. These
# must stay equal to `--bg` in the CSS below; `test_the_theme_colour_is_the_page
# _background` pins them together, since a drift would show as a seam only on a phone.
BG_LIGHT, BG_DARK = "#fbfbf9", "#16161a"

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfbf9; --fg: #1a1a1a; --dim: #6b6b6b; --line: rgba(20,20,20,.28);
  --danger: #a4302c;
}
@media (prefers-color-scheme: dark) {
  /* a dustier coral, not the light mode red: that red is close to invisible
     against near-black, and a brighter one would be the one loud thing on an
     otherwise quiet page */
  :root {
    --bg: #16161a; --fg: #ececec; --dim: #8f8f96;
    --line: rgba(255,255,255,.28); --danger: #d9776f;
  }
}
/* On the root as well as the body. iOS paints the strip behind Safari's toolbar
   and the home indicator from the *canvas* background, which comes from the root
   element; a background set only on <body> leaves that strip Safari's own grey,
   so a dark gallery ends in a pale band. Only a real phone shows this. */
html { background: var(--bg); }
body {
  margin: 0; padding: clamp(1.5rem, 6vw, 3rem) clamp(1rem, 5vw, 5rem);
  background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, sans-serif;
  /* iOS inflates the font of any block it decides is a narrow column — which is
     every tile on a phone — so a caption would come out larger than the heading. */
  -webkit-text-size-adjust: 100%;
}
h1 {
  /* wraps rather than overflowing: the heading and the trash link don't fit on
     one line of a phone, and a heading is not something to scroll sideways for */
  display: flex; align-items: baseline; flex-wrap: wrap; gap: .4rem 1.5rem;
  font-size: 1.1rem; font-weight: 500; color: var(--dim); margin: 0 0 2.5rem;
}
h1 a { color: var(--dim); text-decoration: none; font-size: .9rem; }
h1 .spacer { flex: 1; }
/* The published address. A URL is long and not the point of the heading, so it
   sits quieter than the title and wraps rather than pushing anything sideways. */
.published { font-family: ui-monospace, monospace; font-size: .8rem; overflow-wrap: anywhere; }
/* Masonry, not a grid: paintings range from wide landscapes to tall portraits,
   and grid rows are as tall as their tallest cell — which strands short works in
   a pocket of whitespace. Columns let each tile take only the height it needs.
   A phone gets exactly one column of whatever width is left, so the painting is
   as large as the screen allows without any breakpoint saying so. */
.grid { columns: 260px; column-gap: 2rem; }
figure {
  break-inside: avoid;          /* never split a painting across two columns */
  margin: 0 0 2.5rem;
  position: relative;           /* anchors the corner button to the painting */
  /* deliberately block, not inline-block: an inline-block figure stops Chrome
     balancing across columns entirely and stacks every painting into the first. */
}
/* Two targets per tile: the painting opens the full-size file it was archived as,
   the title opens its Wikipedia article. */
.art { display: block; }
img {
  width: 100%; height: auto; display: block; border-radius: 2px;
  background: rgba(128,128,128,.15);
  box-shadow: 0 1px 3px rgba(0,0,0,.2), 0 8px 24px rgba(0,0,0,.12);
  transition: box-shadow .15s ease;
}
figcaption { margin-top: .85rem; font-size: .875rem; overflow-wrap: anywhere; }
figcaption b { display: block; font-weight: 600; }
figcaption time { color: var(--dim); }
.title { font-style: italic; color: inherit; text-decoration: none; }
.title:focus-visible { text-decoration: underline; }
.empty { color: var(--dim); }
.trashed img { opacity: .55; }

/* The corner button: ★ to unstar in the gallery, ⤺ to restore in the trash.
   Always visible, and 2.75rem square — a finger's worth, not a cursor's. */
.corner { position: absolute; top: .5rem; right: .5rem; margin: 0; }
.corner button {
  display: block; width: 2.75rem; height: 2.75rem; padding: 0;
  border: 0; border-radius: 50%; cursor: pointer;
  background: rgba(0,0,0,.45); color: #fff;
  font-size: 1.1rem; line-height: 2.75rem;
  transition: opacity .15s ease, background .15s ease;
}
.corner button:focus-visible { opacity: 1; background: rgba(0,0,0,.8); }

/* Hover-only polish, gated: a tap on a touchscreen leaves `:hover` stuck on the
   thing you tapped, so anything that *reveals* on hover would be revealed on one
   painting and hidden on the rest for the whole visit. Outside this block those
   states are simply the resting state, which is why the button is dimmed here
   rather than brightened — a phone gets it at full strength. */
@media (hover: hover) {
  h1 a:hover { color: var(--fg); text-decoration: underline; }
  .art:hover img { box-shadow: 0 2px 6px rgba(0,0,0,.28), 0 14px 36px rgba(0,0,0,.2); }
  .title:hover { text-decoration: underline; }
  .trashed:hover img { opacity: 1; }
  .corner button { width: 2rem; height: 2rem; font-size: .95rem; line-height: 2rem; opacity: .5; }
  figure:hover .corner button { opacity: .85; }
  .corner button:hover { opacity: 1; background: rgba(0,0,0,.8); }
  .flash button:hover, .add button:hover, .sync button:not(:disabled):hover {
    background: var(--fg); color: var(--bg); border-color: var(--fg);
  }
  .danger button:hover {
    background: var(--danger); color: var(--bg); border-color: var(--danger);
  }
}

/* The flash: shown once, right after an unstar, a restore or an emptying. */
.flash {
  display: flex; align-items: center; flex-wrap: wrap; gap: .9rem;
  margin: -1.5rem 0 2.5rem; color: var(--dim); font-size: .9rem;
}
.flash form { margin: 0; }
/* Quiet by default — an outline, not a filled block, so the paintings stay the
   only solid thing on the page. Each fills in only on hover (below), the same
   dim-until-touched treatment as the corner button. */
.flash button, .danger button, .add button, .sync button {
  border: 1px solid var(--line); border-radius: 2px; /* the images' own radius */
  padding: .3rem .8rem; cursor: pointer;
  background: transparent; color: var(--fg); font: inherit; font-weight: 500;
  transition: background .15s ease, color .15s ease, border-color .15s ease;
}

/* Paste a Wikipedia image link to hang a painting you found yourself. Sits above
   the grid on the served page only — the archived copy has nothing to post to. */
.add { display: flex; flex-wrap: wrap; gap: .6rem; margin: 0 0 2.5rem; }
.add input {
  flex: 1; min-width: 12rem; max-width: 34rem; padding: .4rem .7rem;
  border: 1px solid var(--line); border-radius: 2px;
  background: transparent; color: var(--fg); font: inherit;
}
.add input:focus-visible { outline: 2px solid var(--fg); outline-offset: -1px; }
.danger { margin: 0; }
.danger button { color: var(--danger); border-color: var(--danger); }
.sync { margin: 0; }
.sync button:disabled { opacity: .4; border-color: var(--line); cursor: default; }

/* Published page only: who made the thing you're looking at. Quiet, and clear of
   the last row of paintings — a masonry's columns end at different heights, so it
   needs real space above it rather than a hairline rule. Centered, unlike the
   rest of the (left-aligned) page: it's a closing note, not part of the content
   flow the grid and heading belong to. */
footer { margin: 3.5rem 0 0; color: var(--dim); font-size: .85rem; text-align: center; }
footer p { margin: 0 0 .35rem; }
footer p:last-child { margin-bottom: 0; }
footer a { color: inherit; }
"""

PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="{light}">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{dark}">
<title>{title}</title>
<style>{css}</style>
<h1>{heading}</h1>
{body}
</html>
"""


# Every page declares the same scheme colours; only the body differs.
_SCHEME = {"light": BG_LIGHT, "dark": BG_DARK}


# --------------------------------------------------------------------------- #
# the star list and the trash — plain files under data_dir
# --------------------------------------------------------------------------- #


def load(config: Config) -> list[Star]:
    stars: list[Star] = cache.load_json(config.stars_file, [])
    return stars


def save(config: Config, stars: list[Star]) -> None:
    config.stars_file.parent.mkdir(parents=True, exist_ok=True)
    cache.save_json(config.stars_file, stars)


def load_trash(config: Config) -> list[Trashed]:
    trashed: list[Trashed] = cache.load_json(config.trash_file, [])
    return trashed


def save_trash(config: Config, trashed: list[Trashed]) -> None:
    config.trash_file.parent.mkdir(parents=True, exist_ok=True)
    cache.save_json(config.trash_file, trashed)


def in_trash(config: Config, key: str) -> bool:
    return any(t["star"]["key"] == key for t in load_trash(config))


def toggle(stars: list[Star], star: Star) -> tuple[list[Star], bool]:
    """Star a painting, or unstar it if it's already there (matched on key).

    Returns the new list and whether the painting ends up starred, so the caller
    knows whether to archive the image or delete it.
    """
    without = [s for s in stars if s["key"] != star["key"]]
    if len(without) < len(stars):
        return without, False
    return [*stars, star], True


def is_starred(stars: list[Star], key: str) -> bool:
    return any(s["key"] == key for s in stars)


def star(config: Config | None = None, *, output: str) -> bool:
    """Toggle the star on whatever painting is currently shown on `output`.

    Reads the record `run()` left for the overlay, so this needs no Wikidata
    lookup — only the image fetch. The archive is written *before* the list is
    saved, so a failed download leaves no star pointing at a missing image.
    Returns whether the painting is now starred.
    """
    config = config or Config.load()
    record = cache.load_json(config.caption_file(output), None)
    if record is None:
        raise RuntimeError(f"no painting recorded for output {output!r}")

    stars, starred = toggle(load(config), record)
    image = config.star_image(record["key"])
    if starred:
        image.parent.mkdir(parents=True, exist_ok=True)
        url = wikidata.image_url(config.commons_url, record["image"], config.stars_image_width)
        web.download(url, image)
    else:
        # The overlay's ★ is a toggle, not the gallery's delete: clicking it again
        # re-downloads. No trash, nothing to restore.
        image.unlink()
    save(config, stars)
    return starred


def star_link(config: Config, link: str) -> tuple[Star, str]:
    """Star a painting from a pasted Wikipedia image link.

    Unlike the overlay's ★ this is *not* a toggle — you paste a link to add a
    painting, so pasting one that's already hung says so and changes nothing
    rather than quietly removing it. A painting still in the trash is restored
    (image, position and all) instead of re-downloaded: adding it afresh would
    leave the trash holding the same key, and restoring that later would hang a
    second copy. Returns the record and which of the three happened.
    """
    record = app.resolve_link(config, link)
    key = record["key"]
    if is_starred(load(config), key):
        return record, "already"
    if in_trash(config, key):
        return restore(config, key), "restored"

    image = config.star_image(key)
    image.parent.mkdir(parents=True, exist_ok=True)
    url = wikidata.image_url(config.commons_url, record["image"], config.stars_image_width)
    web.download(url, image)  # archive before saving, as `star()` does
    save(config, [*load(config), record])
    return record, "starred"


def unstar(config: Config, key: str) -> Star:
    """Move a painting out of the gallery and into the trash.

    Nothing is deleted: the image is *renamed* into `.trash/` and the record is
    written to `trash.json` with the index it held, so `restore()` can put both
    back exactly as they were — now, or after a reboot.
    """
    stars = load(config)
    index = next(i for i, s in enumerate(stars) if s["key"] == key)
    removed = stars.pop(index)
    trashed = config.trash_image(key)
    trashed.parent.mkdir(parents=True, exist_ok=True)
    config.star_image(key).replace(trashed)  # a rename: same filesystem, no copy
    save(config, stars)
    save_trash(config, [*load_trash(config), {"index": index, "star": removed}])
    return removed


def restore(config: Config, key: str) -> Star:
    """Put a trashed painting back where it was, image and all."""
    trash = load_trash(config)
    entry = next(t for t in trash if t["star"]["key"] == key)
    trash.remove(entry)
    image = config.star_image(key)
    image.parent.mkdir(parents=True, exist_ok=True)
    config.trash_image(key).replace(image)
    back: Star = entry["star"]
    stars = load(config)
    stars.insert(min(entry["index"], len(stars)), back)  # the list may have shrunk since
    save(config, stars)
    save_trash(config, trash)
    return back


def empty_trash(config: Config) -> int:
    """Delete every trashed painting. The one irreversible act in the gallery.
    Returns how many went."""
    count = len(load_trash(config))
    if config.trash_dir.exists():
        shutil.rmtree(config.trash_dir)
    return count


def is_git_repo(config: Config) -> bool:
    """Whether `data_dir` is a git working tree — what turns on the gallery's
    "Sync" button.

    A non-tech user gets one by cloning the artwall gallery template into
    `data_dir` in the first place (it ships a `.gitignore` for `.trash/`, so a
    plain `sync()` never pushes the record of a removed painting, and a
    GitHub Action that deploys `public/`). Nothing here creates the repo or
    configures a remote — it only detects one that's already there.
    """
    return (config.data_dir / ".git").exists()


def sync_pending(config: Config) -> bool:
    """Whether `sync()` would actually do anything — a dirty working tree, or a
    commit already made that hasn't reached the remote. What disables the
    gallery's ⇪ Sync button, so there's nothing to click when there's nothing
    to send.

    An unresolvable upstream (`git_unpushed_count_command` fails — no push has
    ever reached the remote yet) counts as pending too, rather than being read
    as "0 ahead": nothing has gone out, so there's definitely something to.
    """
    data_dir = config.data_dir
    status = subprocess.run(
        commands.git_status_porcelain_command(data_dir), capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        return True
    ahead = subprocess.run(
        commands.git_unpushed_count_command(data_dir), capture_output=True, text=True
    )
    return ahead.returncode != 0 or ahead.stdout.strip() != "0"


def sync(config: Config) -> None:
    """Commit and push whatever changed under `data_dir`, for a button a
    non-tech user can click instead of learning git.

    Whatever `git add -A` stages is committed; if nothing changed, the commit
    is skipped rather than left to fail on "nothing to commit"
    (`commands.git_diff_cached_command`, checked by exit code with `--quiet`
    rather than by parsing output). The push always runs regardless — an
    earlier sync's commit may have reached this point but not the remote, and
    a no-op push finds that out for free. A failure is reported back with
    git's own stderr rather than raised past the web handler that calls this
    from a form a non-tech user just clicked.
    """
    if not is_git_repo(config):
        raise SyncError("no git repository here to sync")
    data_dir = config.data_dir
    subprocess.run(commands.git_add_command(data_dir), check=True)
    if subprocess.run(commands.git_diff_cached_command(data_dir)).returncode != 0:
        subprocess.run(
            commands.git_commit_command(data_dir, "Sync starred paintings"), check=True
        )
    push = subprocess.run(commands.git_push_command(data_dir), capture_output=True, text=True)
    if push.returncode != 0:
        raise SyncError(push.stderr.strip() or "git push failed")


# --------------------------------------------------------------------------- #
# rendering — pure: markup in, no IO
# --------------------------------------------------------------------------- #


# `type="url"` lets the browser reject an obvious non-link before the round trip;
# everything else is decided by `resolve_link()`, which has to fetch to know.
ADD_FORM = (
    '<form class="add" method="post" action="/star">'
    '<input type="url" name="link" required '
    'placeholder="Paste a Wikipedia link to a painting" '
    'aria-label="Wikipedia link to a painting">'
    '<button type="submit">★ Add</button></form>'
)

# What the flash says for each outcome of a paste.
ADD_VERBS = {
    "starred": "Starred",
    "already": "Already in the gallery:",
    "restored": "Restored from the trash:",
}


def _flash(flash: Flash) -> str:
    verb = html.escape(flash.verb)
    named = f" <i>{html.escape(flash.title)}</i>" if flash.title else ""
    undo = (
        f'<form method="post" action="/restore/{flash.undo_key}">'
        f'<button type="submit">Undo</button></form>'
        if flash.undo_key is not None
        else ""
    )
    # A <div>, not a <p>: a paragraph can't contain a form, so browsers close it
    # early and hoist the button out of the flex row.
    return f'<div class="flash"><span>{verb}{named}.</span>{undo}</div>'


def _tile(star: Star, src: str, button: str, classes: str = "", link: str | None = None) -> str:
    """One painting. The image links to the full-size file it was archived as; the
    title links to its Wikipedia article, in a new tab so the gallery stays put.

    `link` splits those two apart for the published site, where the grid shows a
    web-sized copy and only the click pulls the multi-megabyte scan. Everywhere
    else the tile shows the same file it links to.
    """
    artist = html.escape(star["artist"] or selection.UNKNOWN_ARTIST)
    title = html.escape(star["title"] or selection.UNTITLED)
    date = html.escape(star["date"])
    article = html.escape(star["url"])
    return (
        f'<figure class="{classes}">'
        f'<a class="art" href="{link or src}">'
        f'<img src="{src}" alt="{title}" loading="lazy"></a>'
        f"<figcaption><b>{artist}</b>"
        f'<a class="title" href="{article}" target="_blank" rel="noopener noreferrer">'
        f"{title}</a> <time>{date}</time>"
        f"</figcaption>{button}</figure>"
    )


def _grid(tiles: list[str]) -> str:
    """Wrap the tiles in the masonry, capped to the width its content can fill.

    CSS multi-column balances columns to equal height, so a handful of paintings on
    a wide screen get squeezed into one tall column with the rest of the row empty.
    Bounding the container to `n` columns' worth of pixels means the browser can
    never offer more columns than there are paintings to put in them.
    """
    span = len(tiles) * COLUMN + (len(tiles) - 1) * GAP
    return f'<div class="grid" style="max-width: {span}px">{"".join(tiles)}</div>'


def _label(star: Star) -> str:
    """An escaped painting name for a button's tooltip and aria-label."""
    return html.escape(star["title"] or selection.UNTITLED)


def _corner(action: str, glyph: str, label: str) -> str:
    return (
        f'<form class="corner" method="post" action="{action}">'
        f'<button type="submit" title="{label}" aria-label="{label}">{glyph}</button></form>'
    )


def _title(stars: list[Star], owner: str = "") -> str:
    """The gallery's own name for itself — shared by every rendering of it, so the
    served page, the archived copy and the published site agree.

    The ★ that marks a starred painting everywhere else on the page always
    leads. `owner` (`Config.owner`) names whose collection it is; with none
    configured, it's just the count.
    """
    count = len(stars)
    plural = "" if count == 1 else "s"
    if owner:
        if not count:
            return f"★ {owner} starred paintings"
        return f"★ {owner} starred {count} painting{plural}"
    if not count:
        return "★ Starred paintings"
    return f"★ {count} starred painting{plural}"


def render_page(
    stars: list[Star],
    interactive: bool = False,
    flash: Flash | None = None,
    trash_count: int = 0,
    public_url: str = "",
    git_sync: bool = False,
    sync_pending: bool = True,
    owner: str = "",
) -> str:
    """The gallery as one HTML string, newest star first.

    `interactive` adds the unstar buttons and the trash link — only the *served*
    page sets it, because both post back and the archived `stars.html` has no
    server behind it. `flash` is the one-shot banner.

    `public_url` (`Config.public_url`) adds a link to wherever you upload the
    published site. It goes on *both* renderings this function produces — the
    served gallery and the archived `stars.html` — because both are pages where
    you are looking at your own collection and might want its public address.

    `git_sync` adds the "Sync" button (`is_git_repo()`) — served only, like the
    trash link, since it posts to a route the archived page has no server for.
    `sync_pending` (`sync_pending()`) disables it when there's nothing to send.

    `owner` (`Config.owner`) names whose collection this is in the heading —
    see `_title()`. The browser tab gets the count-free form regardless
    (`_title([], owner)`) — same reasoning as `render_public()`'s `PUBLIC_TITLE`:
    a tab or a bookmark wants something that doesn't change on every star.
    """
    tab_title = _title([], owner)
    heading = _title(stars, owner)
    if stars:
        tiles = [
            _tile(
                s,
                f"{STARS_IMAGE_DIR}/{s['key']}.jpg",  # relative: keeps the dir portable
                _corner(f"/unstar/{s['key']}", "★", f"Unstar {_label(s)}")
                if interactive
                else "",
            )
            for s in reversed(stars)
        ]
        body = _grid(tiles)
    else:
        body = (
            '<p class="empty">Nothing starred yet. Click the ★ next to a wallpaper '
            "caption, or paste a link to a painting you found on Wikipedia.</p>"
        )

    if interactive:
        body = ADD_FORM + body
    if public_url:
        # Beside the title rather than out at the right edge: it names this
        # collection's other address, so it belongs with the collection's name.
        # Deliberately absent from `render_public()` — that page *is* the live
        # site, and would only be linking to itself.
        url = html.escape(public_url)
        heading += (
            f'<a class="published" href="{url}" target="_blank" rel="noopener noreferrer">'
            f"{url} ↗</a>"
        )
    right = ""
    if interactive and git_sync:
        disabled = "" if sync_pending else " disabled"
        right += (
            '<form class="sync" method="post" action="/sync">'
            f'<button type="submit"{disabled}>⇪ Sync</button></form>'
        )
    if interactive and trash_count:
        plural = "" if trash_count == 1 else "s"
        right += f'<a href="/trash">Trash ({trash_count} painting{plural})</a>'
    if right:
        heading += f'<span class="spacer"></span>{right}'
    if flash:
        body = _flash(flash) + body
    return PAGE.format(**_SCHEME, title=tab_title, heading=heading, css=CSS, body=body)


def render_trash(trashed: list[Trashed], flash: Flash | None = None) -> str:
    """The trash as one HTML string. Only ever served, never archived."""
    if trashed:
        plural = "" if len(trashed) == 1 else "s"
        title = f"{len(trashed)} painting{plural} in the trash"
        tiles = [
            _tile(
                t["star"],
                f"/trash/images/{t['star']['key']}.jpg",
                _corner(f"/restore/{t['star']['key']}", "⤺", f"Restore {_label(t['star'])}"),
                classes="trashed",
            )
            for t in reversed(trashed)
        ]
        body = _grid(tiles)
        empty = (
            '<form class="danger" method="post" action="/trash/empty">'
            f'<button type="submit">Delete {len(trashed)} painting{plural} forever</button>'
            "</form>"
        )
    else:
        title = "The trash is empty"
        body = '<p class="empty">Unstarred paintings wait here until you delete them.</p>'
        empty = ""

    # Back link on the left, beside the title; the irreversible button alone on the
    # right, so a stray click near "← Gallery" can't delete the collection.
    heading = f'<a href="/">← Gallery</a>{title}<span class="spacer"></span>{empty}'
    if flash:
        body = _flash(flash) + body
    return PAGE.format(**_SCHEME, title=title, heading=heading, css=CSS, body=body)


def render_public(stars: list[Star], owner: str = "") -> str:
    """The gallery as a page fit for the open internet: a list of paintings, and
    nothing that acts on them.

    The difference from the archived `stars.html` isn't the buttons — that page
    has none either — it's the images. Here the grid loads the web-sized copies
    `publish()` builds under `PUBLIC_THUMB_DIR` and each tile *links* to the
    full-size archive, so a visitor on a phone downloads a page, not an archive,
    and still gets the whole scan if they ask for it.

    The empty-state copy is neutral: the desktop page tells you to click the ★ on
    a wallpaper caption, which means nothing to someone who found this on the web.

    It is also the only page that names the tool, and the only one that credits
    Wikidata and Wikimedia Commons for the paintings themselves. A tab, a bookmark
    and a search result want `PUBLIC_TITLE` rather than a count that changes on
    every star, and a stranger who likes the collection has nowhere else to find
    out what built it or where the art came from.

    `owner` (`Config.owner`) names whose collection this is, in the *heading*
    only — see `_title()`. `PUBLIC_TITLE` still names the tool in the browser
    tab, same reasoning as the count: a bookmark wants something that doesn't
    change, and an owner set after the page was first bookmarked would be a
    second thing that could.
    """
    if stars:
        body = _grid(
            [
                _tile(
                    s,
                    f"{PUBLIC_THUMB_DIR}/{s['key']}.jpg",
                    "",
                    link=f"{STARS_IMAGE_DIR}/{s['key']}.jpg",
                )
                for s in reversed(stars)
            ]
        )
    else:
        body = '<p class="empty">No paintings here yet.</p>'
    body += (
        f'<footer><p>Starred with <a href="{ARTWALL_URL}" '
        f'target="_blank" rel="noopener noreferrer">artwall</a>.</p>'
        f'<p>Paintings and their details from <a href="{WIKIDATA_URL}" target="_blank" '
        f'rel="noopener noreferrer">Wikidata</a> and <a href="{WIKIMEDIA_COMMONS_URL}" '
        f'target="_blank" rel="noopener noreferrer">Wikimedia Commons</a>.</p></footer>'
    )
    return PAGE.format(
        **_SCHEME, title=PUBLIC_TITLE, heading=_title(stars, owner), css=CSS, body=body
    )


def write_page(config: Config) -> Path:
    """Write the archived, button-less `stars.html` beside the images it links to,
    so the backed-up directory opens in any browser, on any machine, offline."""
    page = config.stars_page
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render_page(load(config), public_url=config.public_url, owner=config.owner))
    return page


# --------------------------------------------------------------------------- #
# the publishable static site — maintained by the overlay's `--publish-stars`
# --------------------------------------------------------------------------- #


def _stale(source: Path, dest: Path) -> bool:
    """Whether `dest` still has to be built from `source` — missing, or older.

    Publishing is a build, and a build you re-run should be cheap: without this,
    every publish re-copies and re-shrinks the whole collection, which is a
    `magick` process per painting for output that is already correct.
    """
    return not dest.exists() or dest.stat().st_mtime < source.stat().st_mtime


def _prune(directory: Path, keep: set[str]) -> None:
    """Delete exported images for paintings that are no longer starred.

    Publishing has to be able to *remove*, not just add: a file left behind here
    goes on being served at its own URL long after the painting stopped appearing
    on the page. Only the derived export is touched — the archive it was built
    from, and the trash, are somewhere else entirely.
    """
    for path in directory.glob("*.jpg"):
        if path.stem not in keep:
            path.unlink()


def publish(
    config: Config | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    """Build the publishable static site under `config.public_dir` and return it.

    A self-contained directory — `index.html`, the web-sized `thumbs/` the page
    loads, and the full-size `images/` it links to — with nothing in it but the
    paintings and the page. That separation is the point: `data_dir` also holds
    `stars.json` and `.trash/`, so uploading *it* would publish the record of
    every painting you ever removed. Upload this instead; there is no server side
    to it, no JavaScript, and every link inside is relative, so it works from a
    bare static host or a subdirectory of one.

    Incremental (`_stale`) and self-cleaning (`_prune`), so the overlay can call it
    after every change to the collection and pay only for what actually moved.

    Serialised on `_PUBLISHING`, because the callers are concurrent: the gallery
    runs a thread per request, and the overlay publishes on a thread of its own
    after a star. Two builds at once would interleave a `_prune` with another
    build's copies and delete a painting it had just written.
    """
    config = config or Config.load()
    with _PUBLISHING:
        stars = load(config)
        keys = {s["key"] for s in stars}

        config.public_image_dir.mkdir(parents=True, exist_ok=True)
        config.public_thumb_dir.mkdir(parents=True, exist_ok=True)
        for key in keys:
            archive = config.star_image(key)
            if _stale(archive, config.public_image(key)):
                shutil.copyfile(archive, config.public_image(key))
            if _stale(archive, config.public_thumb(key)):
                runner(
                    commands.thumbnail_command(
                        archive, config.public_thumb(key), config.public_image_width
                    ),
                    check=True,
                )
        _prune(config.public_image_dir, keys)
        _prune(config.public_thumb_dir, keys)

        config.public_page.write_text(render_public(stars, owner=config.owner))
    return config.public_dir


# --------------------------------------------------------------------------- #
# the gallery server — hosted by the overlay (`--serve-stars`)
# --------------------------------------------------------------------------- #


class _Gallery(http.server.BaseHTTPRequestHandler):
    """Serves the gallery, the trash, their images, and the posts that move
    paintings between them. Nothing else."""

    def __init__(
        self,
        *args: Any,
        config: Config,
        session: Session,
        republish: bool = True,
        runner: Callable[..., object] = subprocess.run,
        **kwargs: Any,
    ) -> None:
        self.config = config
        self.session = session
        # Rebuild the published site after every mutation, so what's on the web
        # keeps up with what you just removed. On unless you opt out
        # (`--no-publish-stars`): a published site that silently lags behind the
        # collection is the failure nobody notices.
        self.republish = republish
        self.runner = runner
        super().__init__(*args, **kwargs)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, page: str) -> None:
        self._send(200, "text/html; charset=utf-8", page.encode())

    def _not_found(self) -> None:
        self._send(404, "text/plain", b"not found")

    def _see_other(self, location: str) -> None:
        """Send the browser back, so a reload can't repeat the post."""
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:
        image = IMAGE_PATH.match(self.path)
        trash_image = TRASH_IMAGE_PATH.match(self.path)
        if self.path == "/":
            # Reading the flash clears it, so the banner shows once. Only the two
            # pages do this — a thumbnail fetch must not swallow the message.
            git_sync = is_git_repo(self.config)
            self._html(
                render_page(
                    load(self.config),
                    interactive=True,
                    flash=self.session.take_flash(),
                    trash_count=len(load_trash(self.config)),
                    public_url=self.config.public_url,
                    git_sync=git_sync,
                    sync_pending=sync_pending(self.config) if git_sync else False,
                    owner=self.config.owner,
                )
            )
        elif self.path == "/trash":
            self._html(render_trash(load_trash(self.config), self.session.take_flash()))
        elif image:
            self._send(200, "image/jpeg", self.config.star_image(image["key"]).read_bytes())
        elif trash_image:
            self._send(
                200, "image/jpeg", self.config.trash_image(trash_image["key"]).read_bytes()
            )
        else:
            self._not_found()

    def _add_link(self) -> Flash:
        """Star whatever the pasted link resolves to, reporting either way.

        A bad link is the expected case here — it's typed input — so it comes back
        as a flash on the page rather than an error status the browser would show
        instead of the gallery.
        """
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        try:
            record, outcome = star_link(self.config, form.get("link", [""])[0])
        except app.LinkError as error:
            return Flash(f"Couldn't add that link — {error}")
        return Flash(ADD_VERBS[outcome], record["title"])

    def do_POST(self) -> None:
        if self.path == "/sync":
            # Its own early return: unlike the routes below, this doesn't change
            # the collection, so there's nothing for write_page()/publish() to
            # catch up on.
            try:
                sync(self.config)
                self.session.flash = Flash("Synced")
            except SyncError as error:
                self.session.flash = Flash(f"Sync failed — {error}")
            self._see_other("/")
            return
        unstar_match = UNSTAR_PATH.match(self.path)
        restore_match = RESTORE_PATH.match(self.path)
        if self.path == "/star":
            self.session.flash = self._add_link()
        elif unstar_match and is_starred(load(self.config), unstar_match["key"]):
            removed = unstar(self.config, unstar_match["key"])
            self.session.flash = Flash("Removed", removed["title"], removed["key"])
        elif restore_match and in_trash(self.config, restore_match["key"]):
            back = restore(self.config, restore_match["key"])
            self.session.flash = Flash("Restored", back["title"])
        elif self.path == "/trash/empty":
            count = empty_trash(self.config)
            plural = "" if count == 1 else "s"
            self.session.flash = Flash(f"Deleted {count} painting{plural} for good")
        else:
            self._not_found()
            return
        write_page(self.config)  # keep the archived copy in step with the list
        if self.republish:
            # and the published site, if we're keeping it live
            publish(self.config, self.runner)
        self._see_other("/")

    def log_message(self, *_args: Any) -> None:
        """Silence the per-request access log — this is a desktop command, not a
        server, and every thumbnail would print a line."""


class GalleryServer(http.server.ThreadingHTTPServer):
    """The gallery's loopback server. Bound to a port the OS picks, so two of them
    never collide, which is why the URL has to be read back off the socket."""

    def __init__(self, config: Config, handler: Callable[..., Any]) -> None:
        self.config = config
        super().__init__((HOST, 0), handler)

    @property
    def url(self) -> str:
        port: int = self.socket.getsockname()[1]
        return f"http://{HOST}:{port}/"


def start_gallery(
    config: Config,
    republish: bool = True,
    runner: Callable[..., object] = subprocess.run,
) -> GalleryServer:
    """Bind a gallery server and refresh the archived page. Nothing is opened.

    The overlay is the only caller: it hosts the gallery for its own lifetime and
    has a button to open it, rather than launching a browser at login.

    **There is no "replace the previous gallery" step, and no PID file.** There
    used to be, because `artwall --stars` could be run twice and leave two servers
    answering two ports. One gallery is now structural instead of enforced: the
    overlay is the only thing that serves one, and `supersede_running_instances()`
    already guarantees a single overlay.
    """
    write_page(config)
    if republish:
        # start from a site that matches the list, not whatever it was left at
        publish(config, runner)
    handler = functools.partial(
        _Gallery, config=config, session=Session(), republish=republish, runner=runner
    )
    return GalleryServer(config, handler)
