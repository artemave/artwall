"""Build the argv for the external programs we shell out to.

Kept pure (argv in, list out) so the wiring is testable without running swaymsg
or ImageMagick.
"""
from __future__ import annotations

from pathlib import Path


def compose_command(
    image_path: Path, text: str, width: int, height: int, font_size: int = 22
) -> list[str]:
    """Lay the painting, fully visible, on a colour-matched gradient, in place.

    Builds a `width`x`height` canvas (the display's pixel size) so nothing is
    cropped. The background is the painting shrunk to 2x2 — four quadrant-average
    colours — then stretched back up, which interpolates into a soft gradient in
    the artwork's own palette. The painting is then fitted (aspect preserved,
    letterboxed) and centred over it, and the caption burned into the corner at
    `font_size` points.

    One ImageMagick 7 (`magick`) invocation; it reads `image_path` before writing
    it, so reading and writing the same path is safe.
    """
    path = str(image_path)
    canvas = f"{width}x{height}"
    return [
        "magick",
        # gradient backfill from the painting's own quadrant colours
        "(", path, "-resize", "2x2!", "-filter", "triangle", "-resize", f"{canvas}!", ")",
        # the painting itself, fitted whole inside the canvas
        "(", path, "-resize", canvas, ")",
        "-gravity", "center", "-composite",
        # caption, bottom-right, legible over anything via the undercolor box
        "-gravity", "SouthEast",
        "-pointsize", str(font_size),
        "-fill", "white",
        "-undercolor", "#00000099",
        "-annotate", "+24+64", f" {text} ",
        path,
    ]


def wallpaper_command(output: str, image_path: Path) -> list[str]:
    # The composed image is already the display's exact size, so `fill` is a
    # 1:1 blit — no cropping, no scaling.
    return ["swaymsg", "output", output, "bg", str(image_path), "fill"]


def outputs_command() -> list[str]:
    return ["swaymsg", "-t", "get_outputs", "-r"]


def open_command(image_path: Path) -> list[str]:
    return ["xdg-open", str(image_path)]
