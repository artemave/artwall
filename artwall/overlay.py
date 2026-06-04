"""Interactive caption overlay for `caption_mode = "link"`.

A small, persistent GTK layer-shell widget — launched once from the Sway config —
that shows each display's current painting caption as a clickable link (it opens
the Wikipedia article). It reads the per-output caption files `run()` writes
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
from pathlib import Path

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


def sway_output_positions() -> dict[str, tuple[int, int]]:
    """Active Sway outputs as name -> (x, y) logical origin, to match GTK monitors."""
    raw = subprocess.run(
        ["swaymsg", "-t", "get_outputs", "-r"], capture_output=True, text=True, check=True
    ).stdout
    return {
        o["name"]: (o["rect"]["x"], o["rect"]["y"])
        for o in json.loads(raw)
        if o["active"]
    }


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
        self, config: Config, monitor: Gdk.Monitor, path: Path, font: str
    ) -> None:
        self.path = path
        self.font = font
        self.url: str | None = None

        self.label = Gtk.Label()
        self.label.set_name("cap")
        event_box = Gtk.EventBox()
        event_box.add(self.label)
        event_box.connect("button-press-event", self._open)
        event_box.connect("realize", self._set_link_cursor)

        self.window = Gtk.Window()
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None:  # composite the translucent box over the wallpaper
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self.window.add(event_box)

        Layer.init_for_window(self.window)
        Layer.set_monitor(self.window, monitor)
        Layer.set_layer(self.window, Layer.Layer.BOTTOM)  # below windows, like wallpaper
        Layer.set_keyboard_mode(self.window, Layer.KeyboardMode.NONE)
        # ignore other surfaces' exclusive zones (e.g. a bar) so the margin is
        # measured from the true screen edge, matching the burned-in caption.
        Layer.set_exclusive_zone(self.window, -1)
        vertical, horizontal = CORNER_EDGES[config.caption_corner]
        scale = monitor.get_scale_factor() or 1
        # caption_pad_* are device pixels (as for the burned-in caption); layer-shell
        # margins are logical, so divide by scale to sit the same distance from the edge.
        for edge, pad in ((vertical, config.caption_pad_y), (horizontal, config.caption_pad_x)):
            Layer.set_anchor(self.window, edge, True)
            Layer.set_margin(self.window, edge, round(pad / scale))

        self.reload()

    def _set_link_cursor(self, widget: Gtk.Widget) -> None:
        window = widget.get_window()
        if window is not None:
            window.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))

    def _open(self, *_args: object) -> None:
        if self.url:
            subprocess.Popen(["xdg-open", self.url])

    def reload(self) -> None:
        """Re-read the caption file and show it; hide if it isn't there yet."""
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
                captions[name] = Caption(config, monitor, config.caption_file(name), font)

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
