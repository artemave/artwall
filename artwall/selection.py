"""Pure decision logic — no IO, no network, fully unit-testable."""
from __future__ import annotations

from typing import Any


def pick_image_url(metadata: dict[str, Any]) -> str | None:
    url: str | None = metadata.get("primaryImage") or metadata.get("primaryImageSmall") or None
    return url


def caption(metadata: dict[str, Any]) -> str:
    title = metadata.get("title") or "Untitled"
    artist = metadata.get("artistDisplayName") or "Unknown artist"
    date = metadata.get("objectDate") or ""
    return f"{artist} — {title} {date}".strip()
