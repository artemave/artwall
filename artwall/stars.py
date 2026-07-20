"""Starred paintings: the persisted list, the archived images, and the gallery.

Everything lives together under `config.data_dir` — `stars.json`, the paintings
in `images/`, and the `stars.html` that links to them by *relative* path. Copy or
back up that one directory and the gallery still opens, offline, anywhere.

Starring is driven by the interactive overlay, which shells out to
`artwall --star <output>` rather than downloading on its GTK main loop. The
overlay already has the whole painting record on screen (`run()` wrote it to
`caption-<output>.json`), so `star()` never has to look the painting up again —
it only fetches the image bytes.

**Why `--stars` runs a server.** The overlay's ★ can only unstar the painting
currently on that display, so the gallery has to be able to remove an older one —
and a `file://` page cannot delete a file. `serve_gallery()` therefore renders the
page over a loopback `http.server` and takes unstar, restore and empty-trash as
plain form POSTs (no JavaScript; a 303 sends the browser back to `/`). The static
`stars.html` is still written on every run, without those buttons — nothing would
answer them once the server is gone, and its job is to make the backed-up
directory readable anywhere.

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
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from . import app, cache, commands, selection, web, wikidata
from .config import STARS_IMAGE_DIR, Config

Star = dict[str, Any]
# A trashed painting: the record, plus the index it held in the gallery.
Trashed = dict[str, Any]

# Loopback only. The gallery can delete your paintings; it is not for the network.
HOST = "127.0.0.1"

# How long a previous gallery gets to exit after SIGTERM before we give up on it.
# It serves in the foreground and catches nothing, so it goes at once in practice.
TERMINATE_TIMEOUT = 5.0
TERMINATE_POLL = 0.02

# Masonry geometry, in px — must match `columns`/`column-gap` in CSS, because
# `_grid()` uses them to cap the container's width (see there for why).
COLUMN, GAP = 260, 32

# The only paths the gallery server answers. Anything else 404s — it serves the
# archived paintings, not the filesystem.
IMAGE_PATH = re.compile(rf"^/{STARS_IMAGE_DIR}/Q(?P<qid>\d+)\.jpg$")
TRASH_IMAGE_PATH = re.compile(r"^/trash/images/Q(?P<qid>\d+)\.jpg$")
UNSTAR_PATH = re.compile(r"^/unstar/(?P<qid>\d+)$")
RESTORE_PATH = re.compile(r"^/restore/(?P<qid>\d+)$")


class Flash(NamedTuple):
    """A one-shot message for the next render. `undo_qid` adds an Undo button."""

    verb: str
    title: str = ""
    undo_qid: int | None = None


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


CSS = """
:root { color-scheme: light dark; --bg: #fbfbf9; --fg: #1a1a1a; --dim: #6b6b6b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #16161a; --fg: #ececec; --dim: #8f8f96; }
}
body {
  margin: 0; padding: 3rem clamp(1rem, 5vw, 5rem);
  background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, sans-serif;
}
h1 {
  display: flex; align-items: baseline; gap: 1.5rem;
  font-size: 1.1rem; font-weight: 500; color: var(--dim); margin: 0 0 2.5rem;
}
h1 a { color: var(--dim); text-decoration: none; font-size: .9rem; }
h1 a:hover { color: var(--fg); text-decoration: underline; }
h1 .spacer { flex: 1; }
/* Masonry, not a grid: paintings range from wide landscapes to tall portraits,
   and grid rows are as tall as their tallest cell — which strands short works in
   a pocket of whitespace. Columns let each tile take only the height it needs. */
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
.art:hover img { box-shadow: 0 2px 6px rgba(0,0,0,.28), 0 14px 36px rgba(0,0,0,.2); }
figcaption { margin-top: .85rem; font-size: .875rem; }
figcaption b { display: block; font-weight: 600; }
figcaption time { color: var(--dim); }
.title { font-style: italic; color: inherit; text-decoration: none; }
.title:hover, .title:focus-visible { text-decoration: underline; }
.empty { color: var(--dim); }
.trashed img { opacity: .55; }
.trashed:hover img { opacity: 1; }

/* The corner button: ★ to unstar in the gallery, ⤺ to restore in the trash.
   Always visible — faintly, so it doesn't compete with the artwork, but never
   hidden behind a hover you'd have to discover — and solid once you reach it. */
.corner { position: absolute; top: .5rem; right: .5rem; margin: 0; }
.corner button {
  display: block; width: 2rem; height: 2rem; padding: 0;
  border: 0; border-radius: 50%; cursor: pointer;
  background: rgba(0,0,0,.45); color: #fff;
  font-size: .95rem; line-height: 2rem;
  opacity: .5; transition: opacity .15s ease, background .15s ease;
}
figure:hover .corner button { opacity: .85; }
.corner button:hover, .corner button:focus-visible { opacity: 1; background: rgba(0,0,0,.8); }

/* The flash: shown once, right after an unstar, a restore or an emptying. */
.flash {
  display: flex; align-items: center; gap: .9rem;
  margin: -1.5rem 0 2.5rem; color: var(--dim); font-size: .9rem;
}
.flash form { margin: 0; }
.flash button, .danger button, .add button {
  border: 0; border-radius: 4px; padding: .35rem .9rem; cursor: pointer;
  background: var(--fg); color: var(--bg); font: inherit; font-weight: 600;
}

/* Paste a Wikipedia image link to hang a painting you found yourself. Sits above
   the grid on the served page only — the archived copy has nothing to post to. */
.add { display: flex; gap: .6rem; margin: 0 0 2.5rem; }
.add input {
  flex: 1; max-width: 34rem; padding: .4rem .7rem;
  border: 1px solid rgba(128,128,128,.45); border-radius: 4px;
  background: transparent; color: var(--fg); font: inherit;
}
.add input:focus-visible { outline: 2px solid var(--fg); outline-offset: -1px; }
.danger { margin: 0; }
.danger button { background: #a4302c; color: #fff; }
"""

PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
<h1>{heading}</h1>
{body}
</html>
"""


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


def in_trash(config: Config, qid: int) -> bool:
    return any(t["star"]["qid"] == qid for t in load_trash(config))


def toggle(stars: list[Star], star: Star) -> tuple[list[Star], bool]:
    """Star a painting, or unstar it if it's already there (matched on QID).

    Returns the new list and whether the painting ends up starred, so the caller
    knows whether to archive the image or delete it.
    """
    without = [s for s in stars if s["qid"] != star["qid"]]
    if len(without) < len(stars):
        return without, False
    return [*stars, star], True


def is_starred(stars: list[Star], qid: int) -> bool:
    return any(s["qid"] == qid for s in stars)


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
    image = config.star_image(record["qid"])
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
    leave the trash holding the same QID, and restoring that later would hang a
    second copy. Returns the record and which of the three happened.
    """
    record = app.resolve_link(config, link)
    qid = record["qid"]
    if is_starred(load(config), qid):
        return record, "already"
    if in_trash(config, qid):
        return restore(config, qid), "restored"

    image = config.star_image(qid)
    image.parent.mkdir(parents=True, exist_ok=True)
    url = wikidata.image_url(config.commons_url, record["image"], config.stars_image_width)
    web.download(url, image)  # archive before saving, as `star()` does
    save(config, [*load(config), record])
    return record, "starred"


def unstar(config: Config, qid: int) -> Star:
    """Move a painting out of the gallery and into the trash.

    Nothing is deleted: the image is *renamed* into `.trash/` and the record is
    written to `trash.json` with the index it held, so `restore()` can put both
    back exactly as they were — now, or after a reboot.
    """
    stars = load(config)
    index = next(i for i, s in enumerate(stars) if s["qid"] == qid)
    removed = stars.pop(index)
    trashed = config.trash_image(qid)
    trashed.parent.mkdir(parents=True, exist_ok=True)
    config.star_image(qid).replace(trashed)  # a rename: same filesystem, no copy
    save(config, stars)
    save_trash(config, [*load_trash(config), {"index": index, "star": removed}])
    return removed


def restore(config: Config, qid: int) -> Star:
    """Put a trashed painting back where it was, image and all."""
    trash = load_trash(config)
    entry = next(t for t in trash if t["star"]["qid"] == qid)
    trash.remove(entry)
    image = config.star_image(qid)
    image.parent.mkdir(parents=True, exist_ok=True)
    config.trash_image(qid).replace(image)
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
        f'<form method="post" action="/restore/{flash.undo_qid}">'
        f'<button type="submit">Undo</button></form>'
        if flash.undo_qid is not None
        else ""
    )
    # A <div>, not a <p>: a paragraph can't contain a form, so browsers close it
    # early and hoist the button out of the flex row.
    return f'<div class="flash"><span>{verb}{named}.</span>{undo}</div>'


def _tile(star: Star, src: str, button: str, classes: str = "") -> str:
    """One painting. The image links to the full-size file it was archived as; the
    title links to its Wikipedia article, in a new tab so the gallery stays put."""
    artist = html.escape(star["artist"] or selection.UNKNOWN_ARTIST)
    title = html.escape(star["title"] or selection.UNTITLED)
    date = html.escape(star["date"])
    article = html.escape(star["url"])
    return (
        f'<figure class="{classes}">'
        f'<a class="art" href="{src}"><img src="{src}" alt="{title}" loading="lazy"></a>'
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


def render_page(
    stars: list[Star],
    interactive: bool = False,
    flash: Flash | None = None,
    trash_count: int = 0,
) -> str:
    """The gallery as one HTML string, newest star first.

    `interactive` adds the unstar buttons and the trash link — only the *served*
    page sets it, because both post back and the archived `stars.html` has no
    server behind it. `flash` is the one-shot banner.
    """
    if stars:
        plural = "" if len(stars) == 1 else "s"
        title = f"★ {len(stars)} starred painting{plural}"
        tiles = [
            _tile(
                s,
                f"{STARS_IMAGE_DIR}/Q{s['qid']}.jpg",  # relative: keeps the dir portable
                _corner(f"/unstar/{s['qid']}", "★", f"Unstar {_label(s)}")
                if interactive
                else "",
            )
            for s in reversed(stars)
        ]
        body = _grid(tiles)
    else:
        title = "★ Starred paintings"
        body = (
            '<p class="empty">Nothing starred yet. Click the ★ next to a wallpaper '
            "caption, or paste a link to a painting you found on Wikipedia.</p>"
        )

    if interactive:
        body = ADD_FORM + body
    heading = title
    if interactive and trash_count:
        plural = "" if trash_count == 1 else "s"
        heading += (
            f'<span class="spacer"></span>'
            f'<a href="/trash">Trash ({trash_count} painting{plural})</a>'
        )
    if flash:
        body = _flash(flash) + body
    return PAGE.format(title=title, heading=heading, css=CSS, body=body)


def render_trash(trashed: list[Trashed], flash: Flash | None = None) -> str:
    """The trash as one HTML string. Only ever served, never archived."""
    if trashed:
        plural = "" if len(trashed) == 1 else "s"
        title = f"{len(trashed)} painting{plural} in the trash"
        tiles = [
            _tile(
                t["star"],
                f"/trash/images/Q{t['star']['qid']}.jpg",
                _corner(f"/restore/{t['star']['qid']}", "⤺", f"Restore {_label(t['star'])}"),
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
    return PAGE.format(title=title, heading=heading, css=CSS, body=body)


def write_page(config: Config) -> Path:
    """Write the archived, button-less `stars.html` beside the images it links to,
    so the backed-up directory opens in any browser, on any machine, offline."""
    page = config.stars_page
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render_page(load(config)))
    return page


# --------------------------------------------------------------------------- #
# the gallery server — `--stars`
# --------------------------------------------------------------------------- #


class _Gallery(http.server.BaseHTTPRequestHandler):
    """Serves the gallery, the trash, their images, and the posts that move
    paintings between them. Nothing else."""

    def __init__(self, *args: Any, config: Config, session: Session, **kwargs: Any) -> None:
        self.config = config
        self.session = session
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
            self._html(
                render_page(
                    load(self.config),
                    interactive=True,
                    flash=self.session.take_flash(),
                    trash_count=len(load_trash(self.config)),
                )
            )
        elif self.path == "/trash":
            self._html(render_trash(load_trash(self.config), self.session.take_flash()))
        elif image:
            self._send(200, "image/jpeg", self.config.star_image(int(image["qid"])).read_bytes())
        elif trash_image:
            qid = int(trash_image["qid"])
            self._send(200, "image/jpeg", self.config.trash_image(qid).read_bytes())
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
        unstar_match = UNSTAR_PATH.match(self.path)
        restore_match = RESTORE_PATH.match(self.path)
        if self.path == "/star":
            self.session.flash = self._add_link()
        elif unstar_match and is_starred(load(self.config), int(unstar_match["qid"])):
            removed = unstar(self.config, int(unstar_match["qid"]))
            self.session.flash = Flash("Removed", removed["title"], removed["qid"])
        elif restore_match and in_trash(self.config, int(restore_match["qid"])):
            back = restore(self.config, int(restore_match["qid"]))
            self.session.flash = Flash("Restored", back["title"])
        elif self.path == "/trash/empty":
            count = empty_trash(self.config)
            plural = "" if count == 1 else "s"
            self.session.flash = Flash(f"Deleted {count} painting{plural} for good")
        else:
            self._not_found()
            return
        write_page(self.config)  # keep the archived copy in step with the list
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


def _cmdline(pid: int) -> str:
    """A process's argv, NUL-separated, or "" if it isn't running.

    Read from `/proc` (this is a Sway/Linux tool). Identity, not just liveness:
    PIDs are recycled, and `stars.pid` can outlive a reboot.
    """
    try:
        return Path(f"/proc/{pid}/cmdline").read_text()
    except OSError:
        return ""


def is_gallery(cmdline: str) -> bool:
    """Whether a `/proc` cmdline is an artwall gallery server. Pure."""
    return "artwall" in cmdline and "--stars" in cmdline


def stop_previous(config: Config, timeout: float = TERMINATE_TIMEOUT) -> int | None:
    """Terminate the gallery server a previous `--stars` left running, if any.

    Running the command again means "show me the gallery", and two servers make
    that ambiguous: each binds its own port, so the tab you already have open —
    and the URL you copied — still belong to the *old* one. Worse, a long-lived
    server keeps the code it started with, so an old process quietly serves
    stale behaviour long after the source changed. Replacing it is what you meant.

    Returns the PID it stopped, or None if there was nothing to stop.
    """
    if not config.stars_pid.exists():
        return None
    pid = int(config.stars_pid.read_text())
    # Not us, and still the process we wrote down — otherwise the file is stale
    # or its PID has been recycled onto something innocent, and must not be killed.
    if pid == os.getpid() or not is_gallery(_cmdline(pid)):
        return None

    # Not guarded against the process exiting between the check above and this
    # signal: that window is microseconds, and a ProcessLookupError saying so is
    # more use than a silent branch nothing can test.
    os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_gallery(_cmdline(pid)):
            return pid
        time.sleep(TERMINATE_POLL)
    raise RuntimeError(f"the gallery already running as PID {pid} would not exit")


def serve_gallery(
    config: Config | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> GalleryServer:
    """Replace any previous gallery, refresh the archived page, then open this one.

    Returns the bound server; the caller runs it (`--stars` serves until you
    interrupt it). It's a command that stays up while you look at it, not a daemon.
    """
    config = config or Config.load()
    stop_previous(config)
    write_page(config)
    handler = functools.partial(_Gallery, config=config, session=Session())
    server = GalleryServer(config, handler)
    # After binding, so the PID on file always belongs to a server that got up.
    # Never removed on exit: a crash or a kill -9 would skip that anyway, so the
    # cmdline check above is what makes a leftover file harmless.
    config.stars_pid.parent.mkdir(parents=True, exist_ok=True)
    config.stars_pid.write_text(str(os.getpid()))
    runner(commands.open_command(server.url), check=True)
    return server
