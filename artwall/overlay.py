"""Interactive caption overlay for `caption_mode = "interactive"`.

A small, persistent GTK layer-shell widget — launched once from the Sway config —
that shows each display's current painting caption as a clickable link (it opens
the Wikipedia article), followed by a refresh button that re-rolls the wallpaper
on that one display. It reads the per-output caption files `run()` writes
(`caption-<output>.json`) and updates whenever they change.

This is the one component that needs a GUI toolkit (PyGObject + gtk-layer-shell)
and a long-lived process, so it lives outside the stdlib-only oneshot and is
launched separately (`python3 -m artwall.overlay`). It can't run under the
headless test suite, so it's excluded from coverage.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import GtkLayerShell as Layer  # type: ignore[attr-defined]  # noqa: E402

from .app import parse_font_name  # noqa: E402 - reuse the pure font parsing
from .config import Config  # noqa: E402

# config.caption_corner -> (vertical edge, horizontal edge) to anchor the surface.
CORNER_EDGES = {
    "top-left": (Layer.Edge.TOP, Layer.Edge.LEFT),
    "top-right": (Layer.Edge.TOP, Layer.Edge.RIGHT),
    "bottom-left": (Layer.Edge.BOTTOM, Layer.Edge.LEFT),
    "bottom-right": (Layer.Edge.BOTTOM, Layer.Edge.RIGHT),
}

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


def sway_outputs_raw() -> list[dict[str, Any]]:
    """The active Sway outputs (each a parsed `get_outputs` object)."""
    raw = subprocess.run(
        ["swaymsg", "-t", "get_outputs", "-r"], capture_output=True, text=True, check=True
    ).stdout
    return [o for o in json.loads(raw) if o["active"]]


def sway_output_positions() -> dict[str, tuple[int, int]]:
    """Active Sway outputs as name -> (x, y) logical origin, to match GTK monitors."""
    return {o["name"]: (o["rect"]["x"], o["rect"]["y"]) for o in sway_outputs_raw()}


def sway_output_scales() -> dict[str, float]:
    """Active Sway outputs as name -> scale. Sway is authoritative and immediate;
    GTK's per-monitor scale can still read 1 for a beat after a hotplug (Sway
    applies the configured scale only once the output is added), so the margin is
    recomputed from this rather than cached from an early GTK read."""
    return {o["name"]: o["scale"] for o in sway_outputs_raw()}


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
    """A Pango font string matching the burned-in caption: system family, sized
    from `config.font_size` (falling back to the system size)."""
    settings = Gtk.Settings.get_default()
    assert settings is not None  # there is always a default while GTK is running
    family, system_size = parse_font_name(settings.get_property("gtk-font-name"))
    size = config.font_size if config.font_size is not None else system_size
    return f"{family} {size}"


class Caption:
    """One clickable caption surface, pinned to a display, reloaded from its file."""

    def __init__(
        self, config: Config, monitor: Gdk.Monitor, name: str, font: str
    ) -> None:
        self.name = name
        self.config = config
        self.path = config.caption_file(name)
        self.font = font
        self.url: str | None = None

        # the caption text — clicking it opens the painting's Wikipedia article
        self.label = Gtk.Label()
        link = Gtk.EventBox()
        link.add(self.label)
        link.connect("button-press-event", self._open)
        link.connect("realize", self._set_link_cursor)

        # a refresh button — clicking it re-rolls the wallpaper on this display.
        # Size the icon to the caption's point size (converted to pixels) so it sits
        # at the same visual height as the text instead of towering over it.
        icon = Gtk.Image()
        icon.set_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU)
        point = int(font.rpartition(" ")[2])
        icon.set_pixel_size(round(point * 96 / 72))
        self.refresh = Gtk.EventBox()
        self.refresh.add(icon)
        self.refresh.connect("button-press-event", self._reroll)
        self.refresh.connect("realize", self._set_link_cursor)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_name("cap")
        box.pack_start(link, False, False, 0)
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
        # ignore other surfaces' exclusive zones (e.g. a bar) so the margin is
        # measured from the true screen edge, matching the burned-in caption.
        Layer.set_exclusive_zone(self.window, -1)
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

    def _set_link_cursor(self, widget: Gtk.Widget) -> None:
        window = widget.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))

    def _open(self, *_args: object) -> None:
        if self.url:
            subprocess.Popen(["xdg-open", self.url])

    def _set_refresh_enabled(self, enabled: bool) -> None:
        """Show the refresh button as active or, while a re-roll runs, as disabled:
        dimmed (our CSS pins the icon white, so `insensitive` alone wouldn't grey it)
        and with the plain arrow cursor instead of the link pointer."""
        self.refresh.set_sensitive(enabled)
        self.refresh.set_opacity(1.0 if enabled else 0.4)
        window = self.refresh.get_window()
        if window is not None:
            cursor = "pointer" if enabled else "default"
            window.set_cursor(Gdk.Cursor.new_from_name(self.refresh.get_display(), cursor))

    def _reroll(self, *_args: object) -> None:
        """Set a fresh painting on just this display. Disable the button until the
        re-roll process finishes — both so a double-click can't stack runs and as
        progress feedback; the caption text itself reloads when the file rewrites."""
        self._set_refresh_enabled(False)
        proc = subprocess.Popen([sys.executable, "-m", "artwall", "--output", self.name])
        GLib.timeout_add(250, self._reroll_done, proc)

    def _reroll_done(self, proc: subprocess.Popen[bytes]) -> bool:
        """Poll the re-roll: keep waiting while it runs, re-enable the button once it
        exits (whether it set the wallpaper or failed, so it never stays stuck)."""
        if proc.poll() is None:
            return True  # still running — poll again
        self._set_refresh_enabled(True)
        return False  # finished — stop polling

    def reload(self) -> None:
        """Recompute the margins from Sway's current scale, then re-read the caption
        file and show it (hide if it isn't there yet). Runs at build, on every
        rotation, and right after a hotplug — the output-event subscription reruns
        artwall, which rewrites the caption files the directory monitor watches — so
        a margin baked at a stale post-hotplug scale self-corrects on the next run."""
        scale = sway_output_scales().get(self.name)
        if scale:  # absent only if the output vanished mid-reload; keep the old margin
            self._apply_margins(scale)
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.window.hide()
            return
        self.url = data["url"]
        text = GLib.markup_escape_text(data["text"])
        self.label.set_markup(f'<span font_desc="{self.font}">{text}</span>')
        self.window.show_all()


def main() -> None:
    supersede_running_instances()  # last launch wins; never stack duplicates
    config = Config.load()
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
        for name, (x, y) in sway_output_positions().items():
            monitor = monitor_at(display, x, y)
            if monitor is not None:
                captions[name] = Caption(config, monitor, name, font)

    rebuild()
    # react to monitors being plugged/unplugged (artwall is triggered separately,
    # by the Sway output-event subscription, to set the new display's wallpaper)
    display.connect("monitor-added", rebuild)
    display.connect("monitor-removed", rebuild)

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
