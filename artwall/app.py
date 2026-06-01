from __future__ import annotations

import json
import random
import subprocess
from collections.abc import Callable
from pathlib import Path
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
    rng: random.Random,
    exclude: list[int],
    attempts: int = ATTEMPTS,
) -> tuple[int, dict[str, Any], str]:
    """Pick a random artwork that has an image, avoiding ids in `exclude`.

    `exclude` holds the ids already used this run, so several displays each get
    a different painting.
    """
    candidates = [i for i in ids if i not in exclude]

    for _ in range(attempts):
        object_id = rng.choice(candidates)
        data = metadata(config, object_id)
        image_url = selection.pick_image_url(data)
        if image_url:
            return object_id, data, image_url

    raise RuntimeError("Could not find artwork with an image")


def parse_outputs(raw: str) -> list[str]:
    """Names of the active outputs in `swaymsg -t get_outputs -r` JSON."""
    names: list[str] = [o["name"] for o in json.loads(raw) if o["active"]]
    return names


def sway_outputs() -> list[str]:  # pragma: no cover - needs a live Sway compositor
    result = subprocess.run(
        commands.outputs_command(), capture_output=True, text=True, check=True
    )
    return parse_outputs(result.stdout)


def _render(
    config: Config,
    rng: random.Random,
    runner: Callable[..., object],
    ids: list[int],
    exclude: list[int],
    image_path: Path,
) -> int:
    """Pick a painting (avoiding `exclude`), download it, and caption it."""
    object_id, data, image_url = choose(config, ids, rng, exclude)
    met.download(image_url, image_path)
    runner(commands.annotate_command(image_path, selection.caption(data)), check=True)
    return object_id


def run(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
    get_outputs: Callable[[], list[str]] = sway_outputs,
) -> list[int]:
    """Set a different random captioned painting on each connected display.

    `rng`, `runner` and `get_outputs` are injected so tests can drive run()
    deterministically — no mocks, no real Sway.
    """
    config = config or Config()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    ids = painting_ids(config)

    shown: list[int] = []
    for output in get_outputs():
        image_path = config.output_image(output)
        shown.append(_render(config, rng, runner, ids, shown, image_path))
        runner(commands.wallpaper_command(output, image_path), check=True)

    return shown


def preview(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    """Generate one random captioned painting and open it, changing nothing else.

    The wallpaper is left untouched — this just writes a preview image and hands
    it to the system image viewer.
    """
    config = config or Config()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    ids = painting_ids(config)
    _render(config, rng, runner, ids, [], config.preview_image)
    runner(commands.open_command(config.preview_image), check=True)

    return config.preview_image
