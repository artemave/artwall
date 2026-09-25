"""The compositor-specific edges: which displays exist, the UI font, and how a
display's wallpaper is set. Everything else is desktop-agnostic."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from . import commands

# kscreen's rotation flags for a display turned on its side.
KSCREEN_SIDEWAYS = (2, 8)


class Output(NamedTuple):
    """A connected display: its name, pixel size, HiDPI scale factor, and the
    logical position of its top-left corner (how the overlay finds its GTK monitor)."""

    name: str
    width: int
    height: int
    scale: float = 1.0
    x: int = 0
    y: int = 0


class Desktop(NamedTuple):
    outputs: Callable[[], list[Output]]
    font: Callable[[], tuple[str, int]]
    wallpaper: Callable[[str, Path], list[str]]


def parse_outputs(raw: str) -> list[Output]:
    """Active outputs from `swaymsg -t get_outputs -r`."""
    return [
        Output(
            o["name"], o["current_mode"]["width"], o["current_mode"]["height"], o["scale"],
            o["rect"]["x"], o["rect"]["y"],
        )
        for o in json.loads(raw)
        if o["active"]
    ]


def parse_kscreen_outputs(raw: str) -> list[Output]:
    """Enabled outputs from `kscreen-doctor -j`."""
    displays = []
    for o in json.loads(raw)["outputs"]:
        if not (o["enabled"] and o["connected"]):
            continue
        mode = next(m for m in o["modes"] if m["id"] == o["currentModeId"])
        width, height = mode["size"]["width"], mode["size"]["height"]
        if o["rotation"] in KSCREEN_SIDEWAYS:
            width, height = height, width
        displays.append(Output(o["name"], width, height, o["scale"], o["pos"]["x"], o["pos"]["y"]))
    return displays


def parse_font_name(name: str) -> tuple[str, int]:
    """Split a desktop font setting like "Adwaita Sans 11" into (family, point size)."""
    family, _, size = name.rpartition(" ")
    return family, int(size)


def parse_kde_font(qfont: str) -> tuple[str, int]:
    """(family, point size) from a KDE font setting — a serialised QFont like
    "Noto Sans,10,-1,5,400,…", whose size may be fractional ("10.5")."""
    family, size, *_ = qfont.split(",")
    return family, round(float(size))


def detect(environ: Mapping[str, str]) -> Desktop:
    if "SWAYSOCK" in environ:
        return SWAY
    if "KDE" in environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
        return PLASMA
    raise RuntimeError("unsupported desktop: artwall runs under Sway or KDE Plasma")


def _read(argv: list[str]) -> str:  # pragma: no cover - needs a live desktop
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()


def _font_file(family: str) -> str:  # pragma: no cover - reads the live fontconfig
    return _read(["fc-match", "-f", "%{file}", family])


def sway_outputs() -> list[Output]:  # pragma: no cover - needs a live Sway compositor
    return parse_outputs(_read(commands.outputs_command()))


def gtk_font() -> tuple[str, int]:  # pragma: no cover - reads the live desktop
    """The desktop's UI font as (file, point size), via gsettings + fontconfig."""
    family, size = parse_font_name(_read(commands.gtk_font_command()).strip("'"))
    return _font_file(family), size


def plasma_outputs() -> list[Output]:  # pragma: no cover - needs a live KDE session
    return parse_kscreen_outputs(_read(commands.kscreen_outputs_command()))


def kde_font() -> tuple[str, int]:  # pragma: no cover - reads the live desktop
    family, size = parse_kde_font(_read(commands.kde_font_command()))
    return _font_file(family), size


def plasma_wallpaper(output: str, image_path: Path) -> list[str]:
    """Plasma keeps showing the image it already has when handed the same URL again,
    so the file's mtime rides along as a query string to make each render a new URL."""
    return commands.plasma_wallpaper_command(output, image_path, image_path.stat().st_mtime_ns)


SWAY = Desktop(sway_outputs, gtk_font, commands.wallpaper_command)
PLASMA = Desktop(plasma_outputs, kde_font, plasma_wallpaper)
