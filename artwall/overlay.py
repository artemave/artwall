"""The caption overlay.

A small, persistent GTK layer-shell widget — launched once with the desktop session —
that shows each display's current painting caption as a clickable link (it opens
the Wikipedia article), followed by three buttons: a star that adds the painting
to the gallery, a gallery button that opens the whole collection, and a refresh
that re-rolls the wallpaper on that one display. It reads the per-output caption
files `run()` writes (`caption-<output>.json`) and updates whenever they change.

**This is the daemon, so this is where the gallery's lifetime belongs.** With
`--serve-stars` the overlay binds the gallery server itself, on a background
thread, for as long as it runs — so the collection is always one click away, with
no foreground command to start and remember to Ctrl-C. With `--publish-stars` it
also keeps the published static site in step: the gallery's own buttons republish
in-process, and a star republishes once the `--star` child exits. Between those
two paths every way a painting can enter or leave the collection is covered —
which is the thing no one-shot command could have promised, since the two paths
live in different processes.

This is the one component that needs a GUI toolkit (PyGObject + gtk-layer-shell)
and a long-lived process, so it lives outside the stdlib-only oneshot and is
launched separately (`python3 -m artwall.overlay`). It can't run under the
headless test suite, so it's excluded from coverage — which is why everything it
does beyond GTK wiring is a call into a tested function in `stars`.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import GtkLayerShell as Layer  # type: ignore[attr-defined]  # noqa: E402

from . import selection, stars  # noqa: E402
from .config import Config  # noqa: E402
from .desktop import Desktop, detect, parse_font_name  # noqa: E402

# config.caption_corner -> (vertical edge, horizontal edge) to anchor the surface.
CORNER_EDGES = {
    "top-left": (Layer.Edge.TOP, Layer.Edge.LEFT),
    "top-right": (Layer.Edge.TOP, Layer.Edge.RIGHT),
    "bottom-left": (Layer.Edge.BOTTOM, Layer.Edge.LEFT),
    "bottom-right": (Layer.Edge.BOTTOM, Layer.Edge.RIGHT),
}

# How often to ask artwall for a new painting. `--throttle` turns all but one
# call per `Config.min_interval` into a no-op, so this only bounds how late a
# rotation can be — after a suspend, say.
ROTATION_CHECK_SECONDS = 60

# A hotplug re-rolls every display, so the new one gets a painting; the short
# throttle folds a dock's several monitors, each its own `monitor-added`, into one run.
HOTPLUG_MIN_INTERVAL = "5"

CSS = b"""
window { background-color: transparent; }
/* one translucent box shared by the caption text and the refresh button; the
   white `color` is inherited by the label and the symbolic refresh icon alike. */
#cap { background-color: rgba(0,0,0,0.6); color: #ffffff; padding: 1px 3px; }
"""


def supersede_running_instances() -> None:
    """Kill any other running overlay so the newest launch wins — no stacked,
    duplicate caption surfaces, and a relaunch (e.g. after a code change) cleanly
    replaces a stale instance instead of orphaning it."""
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            cmdline = (Path("/proc") / entry / "cmdline").read_bytes()
        except OSError:
            continue  # the process vanished between listing and reading — fine
        if b"python" in cmdline and b"artwall.overlay" in cmdline:
            try:
                os.kill(int(entry), signal.SIGTERM)
            except OSError:
                pass  # already gone


def artwall(*args: str) -> subprocess.Popen[bytes]:
    """Start `python3 -m artwall <args>` without blocking the GTK main loop, and
    reap it once it exits."""
    proc = subprocess.Popen([sys.executable, "-m", "artwall", *args])
    GLib.timeout_add(250, lambda: proc.poll() is None)
    return proc


def output_scales(desktop: Desktop) -> dict[str, float]:
    """Active outputs as name -> scale. The compositor is authoritative and
    immediate; GTK's per-monitor scale can still read 1 for a beat after a hotplug
    (Sway applies the configured scale only once the output is added), so the
    margin is recomputed from this rather than cached from an early GTK read."""
    return {o.name: o.scale for o in desktop.outputs()}


def monitor_at(display: Gdk.Display, x: int, y: int) -> Gdk.Monitor | None:
    """The GTK monitor whose geometry origin is (x, y) — i.e. the same display."""
    for i in range(display.get_n_monitors()):
        monitor = display.get_monitor(i)
        assert monitor is not None  # i is within get_n_monitors()
        geometry = monitor.get_geometry()
        if (geometry.x, geometry.y) == (x, y):
            return monitor
    return None


def font_description(config: Config) -> str:
    """A Pango font string: the system family, sized from `config.font_size`
    (falling back to the system size)."""
    settings = Gtk.Settings.get_default()
    assert settings is not None  # there is always a default while GTK is running
    family, system_size = parse_font_name(settings.get_property("gtk-font-name"))
    size = config.font_size if config.font_size is not None else system_size
    return f"{family} {size}"


class Caption:
    """One clickable caption surface, pinned to a display, reloaded from its file."""

    def __init__(
        self,
        config: Config,
        desktop: Desktop,
        monitor: Gdk.Monitor,
        name: str,
        font: str,
        gallery_url: str,
        republish: bool = False,
    ) -> None:
        self.name = name
        self.config = config
        self.desktop = desktop
        self.path = config.caption_file(name)
        self.font = font
        self.gallery_url = gallery_url
        self.republish = republish
        self.url: str | None = None
        self.key: str | None = None

        # the caption text — clicking it opens the painting's Wikipedia article
        self.label = Gtk.Label()
        link = Gtk.EventBox()
        link.add(self.label)
        link.connect("button-press-event", self._open)
        link.connect("realize", self._set_link_cursor)

        # Icons sized to the caption's point size (converted to pixels) so they sit
        # at the same visual height as the text instead of towering over it.
        self.icon_pixels = round(int(font.rpartition(" ")[2]) * 96 / 72)

        # a star button — clicking it adds this painting to the gallery (or removes it).
        # The glyph is visually tighter than the refresh arrow, so give it a little
        # breathing room on both sides rather than letting it crowd its neighbours.
        self.star_icon = Gtk.Image()
        self.star = self._button(self.star_icon, self._toggle_star)
        self.star.set_margin_start(4)
        self.star.set_margin_end(4)

        # a gallery button — clicking it opens the whole collection. Next to the
        # star, because the two are about the same thing: one adds this painting,
        # the other shows you everything you've added.
        gallery_icon = Gtk.Image()
        gallery_icon.set_from_icon_name("view-grid-symbolic", Gtk.IconSize.MENU)
        gallery_icon.set_pixel_size(self.icon_pixels)
        self.gallery = self._button(gallery_icon, self._open_gallery)
        # Only an end margin: the star's own `margin_end` already opens up the gap
        # on this button's left, and matching it on the right keeps all three icons
        # evenly spaced instead of leaving refresh crowded against the grid.
        self.gallery.set_margin_end(4)

        # a refresh button — clicking it re-rolls the wallpaper on this display
        refresh_icon = Gtk.Image()
        refresh_icon.set_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
        refresh_icon.set_pixel_size(self.icon_pixels)
        self.refresh = self._button(refresh_icon, self._reroll)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_name("cap")
        box.pack_start(link, False, False, 0)
        box.pack_start(self.star, False, False, 0)
        box.pack_start(self.gallery, False, False, 0)
        box.pack_start(self.refresh, False, False, 0)

        self.window = Gtk.Window()
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None:  # composite the translucent box over the wallpaper
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self.window.add(box)

        Layer.init_for_window(self.window)
        Layer.set_monitor(self.window, monitor)
        Layer.set_layer(self.window, Layer.Layer.BOTTOM)  # below windows, like wallpaper
        Layer.set_keyboard_mode(self.window, Layer.KeyboardMode.NONE)
        vertical, horizontal = CORNER_EDGES[config.caption_corner]
        Layer.set_anchor(self.window, vertical, True)
        Layer.set_anchor(self.window, horizontal, True)

        self.reload()  # sets the margins (from Sway's scale) and the caption text

    def _apply_margins(self, scale: float) -> None:
        """Inset the caption by `caption_pad_*` *device* pixels from its corner.
        layer-shell margins are logical (the compositor multiplies them by the
        surface's scale), so divide by the scale to land a fixed device distance.
        Recomputed on every reload rather than cached — a margin baked at the
        wrong scale right after a hotplug would otherwise leave the caption adrift."""
        vertical, horizontal = CORNER_EDGES[self.config.caption_corner]
        for edge, pad in (
            (vertical, self.config.caption_pad_y),
            (horizontal, self.config.caption_pad_x),
        ):
            Layer.set_margin(self.window, edge, round(pad / scale))

    def _button(self, icon: Gtk.Image, on_click: Callable[..., None]) -> Gtk.EventBox:
        button = Gtk.EventBox()
        button.add(icon)
        button.connect("button-press-event", on_click)
        button.connect("realize", self._set_link_cursor)
        return button

    def _set_link_cursor(self, widget: Gtk.Widget) -> None:
        window = widget.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))

    def _open(self, *_args: object) -> None:
        if self.url:
            subprocess.Popen(["xdg-open", self.url])

    def _set_enabled(self, button: Gtk.EventBox, enabled: bool) -> None:
        """Show a button as active or, while its command runs, as disabled: dimmed
        (our CSS pins the icon white, so `insensitive` alone wouldn't grey it) and
        with the plain arrow cursor instead of the link pointer."""
        button.set_sensitive(enabled)
        button.set_opacity(1.0 if enabled else 0.4)
        window = button.get_window()
        if window is not None:
            cursor = "pointer" if enabled else "default"
            window.set_cursor(Gdk.Cursor.new_from_name(button.get_display(), cursor))

    def _spawn(
        self, args: list[str], button: Gtk.EventBox, done: Callable[[], None]
    ) -> None:
        """Run `python3 -m artwall <args>` without blocking the GTK main loop, and
        disable `button` until it exits — both so a double-click can't stack runs,
        and as progress feedback. `done` runs whether it succeeded or failed, so the
        button never stays stuck."""
        self._set_enabled(button, False)
        proc = artwall(*args)

        def poll() -> bool:
            if proc.poll() is None:
                return True  # still running — poll again
            self._set_enabled(button, True)
            done()
            return False  # finished — stop polling

        GLib.timeout_add(250, poll)

    def _reroll(self, *_args: object) -> None:
        """Set a fresh painting on just this display. The caption text reloads on its
        own, when `run()` rewrites the file the directory monitor watches."""
        self._spawn(["--output", self.name], self.refresh, lambda: None)

    def _open_gallery(self, *_args: object) -> None:
        """Open the whole starred collection — the live gallery if this overlay is
        serving one, the archived `stars.html` otherwise (see `gallery_url`)."""
        subprocess.Popen(["xdg-open", self.gallery_url])

    def _toggle_star(self, *_args: object) -> None:
        """Add this painting to the gallery, or take it out. Shelled out rather than
        done inline: starring downloads the full-size image to archive it, which would
        freeze the overlay. Nothing rewrites the caption file, so refresh the icon
        ourselves once it's done."""
        self._spawn(["--star", self.name], self.star, self._after_star)

    def _after_star(self) -> None:
        """Show the new star state, then — if we're keeping the published site in
        step — rebuild it on a background thread (see `_publish_async`)."""
        self._show_star_state()
        if self.republish:
            _publish_async(self.config)

    def _show_star_state(self) -> None:
        """Point the star icon at the truth on disk — filled if this display's
        painting is in the gallery, hollow if not."""
        starred = self.key is not None and stars.is_starred(
            stars.load(self.config), self.key
        )
        name = "starred-symbolic" if starred else "non-starred-symbolic"
        self.star_icon.set_from_icon_name(name, Gtk.IconSize.MENU)
        self.star_icon.set_pixel_size(self.icon_pixels)

    def reload(self) -> None:
        """Recompute the margins from the compositor's current scale, then re-read the caption
        file and show it (hide if it isn't there yet). Runs at build, on every
        rotation, and right after a hotplug — the output-event subscription reruns
        artwall, which rewrites the caption files the directory monitor watches — so
        a margin baked at a stale post-hotplug scale self-corrects on the next run."""
        scale = output_scales(self.desktop).get(self.name)
        if scale:  # absent only if the output vanished mid-reload; keep the old margin
            self._apply_margins(scale)
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.window.hide()
            return
        self.url = data["url"]
        self.key = data["key"]
        text = GLib.markup_escape_text(selection.caption(data))
        self.label.set_markup(f'<span font_desc="{self.font}">{text}</span>')
        self._show_star_state()  # a new painting is (almost always) not yet starred
        self.window.show_all()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Both features are **on by default** — they're what the daemon is for — so the
    flags exist to be *negated* (`--no-serve-stars`, `--no-publish-stars`)."""
    parser = argparse.ArgumentParser(
        prog="artwall.overlay",
        description="artwall's caption overlay.",
    )
    parser.add_argument(
        "--serve-stars",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Host the starred gallery for as long as the overlay runs, so the gallery "
        "button opens the live, editable page. With --no-serve-stars it opens the "
        "archived, read-only stars.html instead, and nothing listens on a port.",
    )
    parser.add_argument(
        "--publish-stars",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep the uploadable static site under the data directory's public/ in "
        "step with the collection — rebuilt at startup and on every star, unstar, "
        "restore and paste. --no-publish-stars builds no site at all.",
    )
    return parser.parse_args(argv)


def _publish_async(config: Config) -> None:
    """Rebuild the published site off the GTK main loop.

    A thread, not a child process — publishing is `stars.publish()`, which touches
    no GTK at all, so there is nothing to gain from a second interpreter and a CLI
    flag to drive it. It must not run *here* though: it shrinks every newly starred
    painting with `magick`, and doing that on the main loop would freeze every
    caption on every screen while it ran. `publish()` takes a lock, so overlapping
    calls from here and from the gallery's request threads queue up rather than
    interleave.
    """
    threading.Thread(target=stars.publish, args=(config,), daemon=True).start()


def gallery_url(config: Config, server: stars.GalleryServer | None) -> str:
    """What the gallery button opens.

    The live server's loopback URL when we're hosting one; otherwise the archived
    `stars.html` as a `file://` link. The fallback is a real page — the same
    gallery, just read-only — so the button is never dead, it only does less.
    """
    if server is not None:
        return server.url
    return config.stars_page.as_uri()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    supersede_running_instances()  # last launch wins; never stack duplicates
    config = Config.load()
    desktop = detect(os.environ)

    server: stars.GalleryServer | None = None
    if args.serve_stars:
        server = stars.start_gallery(config, args.publish_stars)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    else:
        # No server, so the button falls back to `stars.html` — make sure there is
        # one, and that it matches the list, before anything can click it.
        stars.write_page(config)
        if args.publish_stars:
            _publish_async(config)

    display = Gdk.Display.get_default()
    screen = Gdk.Screen.get_default()
    assert display is not None and screen is not None  # GTK is running
    font = font_description(config)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )

    captions: dict[str, Caption] = {}

    def rebuild(*_args: object) -> None:
        """(Re)build one caption surface per output — on startup and on hotplug."""
        for caption in captions.values():
            caption.window.destroy()
        captions.clear()
        url = gallery_url(config, server)
        for output in desktop.outputs():
            monitor = monitor_at(display, output.x, output.y)
            if monitor is not None:
                captions[output.name] = Caption(
                    config, desktop, monitor, output.name, font, url, args.publish_stars
                )

    rebuild()
    display.connect("monitor-added", rebuild)
    display.connect("monitor-removed", rebuild)
    display.connect(
        "monitor-added",
        lambda *_: artwall("--throttle", "--min-interval", HOTPLUG_MIN_INTERVAL),
    )

    def rotate() -> bool:
        artwall("--throttle")
        return True  # keep the timer

    rotate()
    GLib.timeout_add_seconds(ROTATION_CHECK_SECONDS, rotate)

    def on_change(
        _monitor: Gio.FileMonitor,
        changed: Gio.File,
        _other: Gio.File | None,
        _event: Gio.FileMonitorEvent,
    ) -> None:
        for caption in captions.values():
            if changed.get_path() == str(caption.path):
                caption.reload()

    watch = Gio.File.new_for_path(str(config.cache_dir)).monitor_directory(
        Gio.FileMonitorFlags.NONE, None
    )
    watch.connect("changed", on_change)

    Gtk.main()


if __name__ == "__main__":
    main()
