"""Pure decision logic — no IO, no network, fully unit-testable."""
from __future__ import annotations


def caption(painting: dict[str, str]) -> str:
    """Corner caption from a parsed painting (artist, title, date)."""
    artist = painting.get("artist") or "Unknown artist"
    title = painting.get("title") or "Untitled"
    date = painting.get("date") or ""
    return f"{artist} — {title} {date}".strip()
