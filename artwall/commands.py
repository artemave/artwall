"""Build the argv for the external programs we shell out to.

Kept pure (argv in, list out) so the wiring is testable without running swaymsg
or ImageMagick.
"""
from __future__ import annotations

from pathlib import Path

# Map the user-facing corner names to ImageMagick gravities. A positive annotate
# offset insets the caption from that corner regardless of which one it is.
CORNER_GRAVITY = {
    "top-left": "NorthWest",
    "top-right": "NorthEast",
    "bottom-left": "SouthWest",
    "bottom-right": "SouthEast",
}


def compose_command(
    image_path: Path,
    text: str | None,
    width: int,
    height: int,
    font_size: int = 22,
    corner: str = "bottom-right",
    pad_x: int = 24,
    pad_y: int = 64,
    font: str | None = None,
) -> list[str]:
    """Lay the painting, fully visible, on a colour-matched gradient, in place.

    Builds a `width`x`height` canvas (the display's pixel size) so nothing is
    cropped. The background is the painting shrunk to 2x2 — four quadrant-average
    colours — then stretched back up, which interpolates into a soft gradient in
    the artwork's own palette. The painting is then fitted (aspect preserved,
    letterboxed) and centred over it.

    If `text` is given, it's burned into the chosen `corner` at `font_size` (a
    magick pointsize) in optional `font`, inset by `pad_x`/`pad_y` pixels
    (absolute, so it sits the same fixed distance from the edge on every display).
    Pass `text=None` to compose the painting with no caption (the interactive
    overlay draws the caption itself, as a separate clickable surface).

    One ImageMagick 7 (`magick`) invocation; it reads `image_path` before writing
    it, so reading and writing the same path is safe.
    """
    path = str(image_path)
    canvas = f"{width}x{height}"
    command = [
        "magick",
        # gradient backfill from the painting's own quadrant colours
        "(", path, "-resize", "2x2!", "-filter", "triangle", "-resize", f"{canvas}!", ")",
        # the painting itself, fitted whole inside the canvas
        "(", path, "-resize", canvas, ")",
        "-gravity", "center", "-composite",
    ]
    if text:
        command += [
            # caption in the chosen corner, legible over anything via the undercolor box
            "-gravity", CORNER_GRAVITY[corner],  # unknown corner fails loudly
            *(["-font", font] if font else []),
            "-pointsize", str(font_size),
            "-fill", "white",
            "-undercolor", "#00000099",
            # non-breaking spaces pad the box on both sides: a regular edge space is
            # stripped by the gravity alignment, so it would never pad the box
            "-annotate", f"+{pad_x}+{pad_y}", f"\u00a0{text}\u00a0",
        ]
    command.append(path)
    return command


def wallpaper_command(output: str, image_path: Path) -> list[str]:
    # The composed image is already the display's exact size, so `fill` is a
    # 1:1 blit — no cropping, no scaling.
    return ["swaymsg", "output", output, "bg", str(image_path), "fill"]


def outputs_command() -> list[str]:
    return ["swaymsg", "-t", "get_outputs", "-r"]


def open_command(image_path: Path) -> list[str]:
    return ["xdg-open", str(image_path)]
