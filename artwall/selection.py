"""Pure decision logic — no IO, no network, fully unit-testable."""
from __future__ import annotations

from typing import Any

UNKNOWN_ARTIST = "Unknown artist"
UNTITLED = "Untitled"


def caption(painting: dict[str, Any]) -> str:
    """Corner caption from a parsed painting (artist, title, date)."""
    artist = painting.get("artist") or UNKNOWN_ARTIST
    title = painting.get("title") or UNTITLED
    date = painting.get("date") or ""
    return f"{artist} — {title} {date}".strip()


def record(key: str, painting: dict[str, str], url: str) -> dict[str, Any]:
    """A painting reduced to what the caption and the star gallery need.

    `run()` writes this as `caption-<output>.json`; starring copies it verbatim
    into the star list. It carries the image filename and the link, so neither
    the caption nor the gallery has to go back to Wikidata to draw a painting.

    `key` is the painting's identity, and it is namespaced because paintings now
    arrive from two places: `Q<n>` for a Wikidata item (the wallpaper, and any
    pasted link whose file names its artwork in structured data), `M<n>` for a
    Commons page whose artwork exists only in an `{{Artwork}}` template and has
    no Wikidata item at all. The two numbering spaces overlap, so an unprefixed
    int would silently collide — the same key naming two different paintings.
    """
    return {
        "key": key,
        "artist": painting["artist"],
        "title": painting["title"],
        "date": painting["date"],
        "image": painting["image"],
        "url": url,
    }
