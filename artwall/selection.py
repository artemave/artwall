"""Pure decision logic — no IO, no network, fully unit-testable."""
from __future__ import annotations

from typing import Any


def available_ids(ids: list[int], history: list[int]) -> tuple[list[int], list[int]]:
    """IDs not shown yet. When all have been shown, reset and reuse the pool.

    Returns (available, history): the candidate IDs and the (possibly cleared)
    history so the caller knows a reset happened.
    """
    available = [i for i in ids if i not in history]
    if available:
        return available, history
    return list(ids), []


def pick_image_url(metadata: dict[str, Any]) -> str | None:
    url: str | None = metadata.get("primaryImage") or metadata.get("primaryImageSmall") or None
    return url


def caption(metadata: dict[str, Any]) -> str:
    title = metadata.get("title") or "Untitled"
    artist = metadata.get("artistDisplayName") or "Unknown artist"
    date = metadata.get("objectDate") or ""
    return f"{artist} — {title} {date}".strip()


def trim_history(history: list[int], object_id: int, limit: int) -> list[int]:
    return (history + [object_id])[-limit:]
