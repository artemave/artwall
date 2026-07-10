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


def record(qid: int, painting: dict[str, str], url: str) -> dict[str, Any]:
    """A painting reduced to what the overlay and the star gallery need.

    `run()` writes this as `caption-<output>.json`; starring copies it verbatim
    into the star list. It carries the image filename and the link, so neither
    the overlay nor the gallery has to go back to Wikidata to draw a painting.
    """
    return {
        "qid": qid,
        "artist": painting["artist"],
        "title": painting["title"],
        "date": painting["date"],
        "image": painting["image"],
        "url": url,
    }
