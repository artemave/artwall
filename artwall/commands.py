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


def thumbnail_command(source: Path, dest: Path, size: int) -> list[str]:
    """Shrink an archived painting to a web-sized copy for the published gallery.

    `{size}x{size}>` fits the painting inside a square of that side and — the `>` —
    *only ever shrinks*, so a scan that was already small isn't blown up into a
    blurry, larger file than the original. Bounding both sides rather than the
    width alone keeps a tall portrait from being the one huge download on the page.

    `-strip` drops the archive's metadata: smaller, and nothing on a public site
    should carry EXIF it doesn't need. `-interlace Plane` writes a progressive
    JPEG, which paints a whole low-detail image early instead of a sharp top edge
    over blank space — the difference you actually feel on a phone.
    """
    return [
        "magick", str(source),
        "-resize", f"{size}x{size}>",
        "-strip", "-interlace", "Plane", "-quality", "82",
        str(dest),
    ]


def wallpaper_command(output: str, image_path: Path) -> list[str]:
    # The composed image is already the display's exact size, so `fill` is a
    # 1:1 blit — no cropping, no scaling.
    return ["swaymsg", "output", output, "bg", str(image_path), "fill"]


def outputs_command() -> list[str]:
    return ["swaymsg", "-t", "get_outputs", "-r"]


def open_command(target: str | Path) -> list[str]:
    """Hand a file or URL to the desktop's default handler — the preview image, or
    the star gallery's loopback URL (which lands in a browser)."""
    return ["xdg-open", str(target)]


def git_diff_cached_command(data_dir: Path) -> list[str]:
    """Exit 0 if nothing is staged, 1 if something is — the check that decides
    whether `sync()` has anything to commit."""
    return ["git", "-C", str(data_dir), "diff", "--cached", "--quiet"]


def git_status_porcelain_command(data_dir: Path) -> list[str]:
    """Machine-readable status — empty output means a clean working tree."""
    return ["git", "-C", str(data_dir), "status", "--porcelain"]


def git_unpushed_count_command(data_dir: Path) -> list[str]:
    """How many local commits `HEAD` has that `@{u}` (the upstream branch)
    doesn't. Fails if there's no upstream ref to compare against yet — which
    `sync_pending()` treats the same as "there's something to push"."""
    return ["git", "-C", str(data_dir), "rev-list", "--count", "@{u}..HEAD"]


def git_add_command(data_dir: Path) -> list[str]:
    return ["git", "-C", str(data_dir), "add", "-A"]


def git_commit_command(data_dir: Path, message: str) -> list[str]:
    return ["git", "-C", str(data_dir), "commit", "-m", message]


def git_push_command(data_dir: Path) -> list[str]:
    return ["git", "-C", str(data_dir), "push"]
