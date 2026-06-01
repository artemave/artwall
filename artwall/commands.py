"""Build the argv for the external programs we shell out to.

Kept pure (argv in, list out) so the wiring is testable without running swaymsg
or ImageMagick.
"""
from __future__ import annotations

from pathlib import Path


def annotate_command(image_path: Path, text: str) -> list[str]:
    """Burn a caption into the bottom-right corner of the image, in place.

    Uses ImageMagick 7 (`magick`); the undercolor box keeps the text legible
    over any painting.
    """
    path = str(image_path)
    return [
        "magick",
        path,
        "-gravity", "SouthEast",
        "-pointsize", "22",
        "-fill", "white",
        "-undercolor", "#00000099",
        "-annotate", "+24+24", f" {text} ",
        path,
    ]


def wallpaper_command(output: str, image_path: Path) -> list[str]:
    return ["swaymsg", "output", output, "bg", str(image_path), "fill"]


def outputs_command() -> list[str]:
    return ["swaymsg", "-t", "get_outputs", "-r"]


def open_command(image_path: Path) -> list[str]:
    return ["xdg-open", str(image_path)]
