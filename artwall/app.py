from __future__ import annotations

import random
import subprocess
from collections.abc import Callable
from typing import Any

from . import cache, commands, met, selection
from .config import Config

ATTEMPTS = 20


def painting_ids(config: Config) -> list[int]:
    if cache.fresh(config.ids_file, config.ids_ttl):
        cached: list[int] = cache.load_json(config.ids_file, [])
        return cached

    data: dict[str, Any] = met.get_json(config.search_url, {"hasImages": "true", "q": "painting"})
    ids: list[int] = data.get("objectIDs") or []

    if not ids:
        raise RuntimeError("Met API returned no object IDs")

    cache.save_json(config.ids_file, ids)
    return ids


def metadata(config: Config, object_id: int) -> dict[str, Any]:
    file = config.cache_dir / f"{object_id}.json"

    if file.exists():
        cached: dict[str, Any] = cache.load_json(file, {})
        return cached

    data: dict[str, Any] = met.get_json(config.object_url.format(object_id))
    cache.save_json(file, data)
    return data


def choose(
    config: Config,
    ids: list[int],
    history: list[int],
    rng: random.Random,
    attempts: int = ATTEMPTS,
) -> tuple[int, dict[str, Any], str, list[int]]:
    """Pick a random unseen artwork that actually has an image.

    Returns (object_id, metadata, image_url, history) where history reflects a
    reset if every ID had already been shown.
    """
    available, history = selection.available_ids(ids, history)

    for _ in range(attempts):
        object_id = rng.choice(available)
        data = metadata(config, object_id)
        image_url = selection.pick_image_url(data)
        if image_url:
            return object_id, data, image_url, history

    raise RuntimeError("Could not find artwork with an image")


def run(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> int:
    """Fetch a painting, set it as the wallpaper, record it in history.

    `rng` and `runner` are injected so tests can drive run() deterministically
    with a real Random seed and a recording runner — no mocks, no real swaymsg.
    """
    config = config or Config()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    ids = painting_ids(config)
    history: list[int] = cache.load_json(config.history_file, [])

    object_id, data, image_url, history = choose(config, ids, history, rng)
    met.download(image_url, config.current_image)

    runner(commands.annotate_command(config.current_image, selection.caption(data)), check=True)
    runner(commands.wallpaper_command(config.current_image), check=True)

    history = selection.trim_history(history, object_id, config.history_limit)
    cache.save_json(config.history_file, history)

    return object_id
