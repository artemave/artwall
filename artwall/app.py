from __future__ import annotations

import json
import random
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from . import cache, commands, selection, web, wikidata
from .config import Config

# A cached QID may have lost its image or been deleted since; re-pick a few times
# before giving up. Hits are rare, so this almost always succeeds first try.
ATTEMPTS = 10

# Preview has no display to target, so render it at a common desktop size.
PREVIEW_WIDTH, PREVIEW_HEIGHT = 1920, 1080


class Output(NamedTuple):
    """A connected display: its name and its pixel size."""

    name: str
    width: int
    height: int


def painting_ids(config: Config) -> list[int]:
    query = wikidata.catalogue_query(config.filters, config.date_begin, config.date_end)
    cache_file = config.ids_file(query)
    if cache.fresh(cache_file, config.ids_ttl):
        cached: list[int] = cache.load_json(cache_file, [])
        return cached

    csv_text = web.get_text(config.sparql_url, {"query": query}, accept="text/csv")
    ids = wikidata.parse_catalogue(csv_text)

    if not ids:
        raise RuntimeError("Wikidata returned no paintings for the configured filters")

    cache.save_json(cache_file, ids)
    return ids


def _get_entity(config: Config, entity_id: str, props: str) -> dict[str, Any]:
    result: dict[str, Any] = web.get_json(
        config.api_url,
        {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": props,
            "languages": config.language,
            "format": "json",
        },
    )
    return result


def _artist(config: Config, creator_qid: str) -> str:
    """Resolve a creator QID to a name (a second Action-API call), or ""."""
    if not creator_qid:
        return ""
    result = _get_entity(config, creator_qid, "labels")
    return wikidata.label(result, creator_qid, config.language)


def choose(
    config: Config,
    ids: list[int],
    rng: random.Random,
    exclude: list[int],
    attempts: int = ATTEMPTS,
) -> tuple[int, dict[str, str]]:
    """Pick a random painting (avoiding `exclude`) and fetch its image + caption.

    Per-painting data comes from the Action API, not WDQS, so a query-service
    outage doesn't break runs once the catalogue is cached. `exclude` holds the
    ids already used this run, so several displays each get a different painting.
    """
    candidates = [i for i in ids if i not in exclude]

    for _ in range(attempts):
        qid = rng.choice(candidates)
        result = _get_entity(config, f"Q{qid}", "claims|labels")
        painting = wikidata.parse_entity(result, qid, config.language)
        if painting:
            painting["artist"] = _artist(config, painting["creator_qid"])
            return qid, painting

    raise RuntimeError("Could not fetch a usable painting from Wikidata")


def search_entities(term: str, config: Config | None = None) -> list[tuple[str, str, str]]:
    """Resolve a name to candidate (QID, label, description) rows for `--find`."""
    config = config or Config.load()
    result = web.get_json(
        config.api_url,
        {
            "action": "wbsearchentities",
            "search": term,
            "language": config.language,
            "type": "item",
            "limit": "10",
            "format": "json",
        },
    )
    return wikidata.parse_search(result)


def parse_outputs(raw: str) -> list[Output]:
    """Active outputs (name + pixel size) from `swaymsg -t get_outputs -r` JSON."""
    return [
        Output(o["name"], o["current_mode"]["width"], o["current_mode"]["height"])
        for o in json.loads(raw)
        if o["active"]
    ]


def sway_outputs() -> list[Output]:  # pragma: no cover - needs a live Sway compositor
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
    width: int,
    height: int,
) -> int:
    """Pick a painting (avoiding `exclude`), download it, and compose it for `width`x`height`."""
    qid, painting = choose(config, ids, rng, exclude)
    url = wikidata.image_url(config.commons_url, painting["image"], width)
    web.download(url, image_path)
    caption = selection.caption(painting)
    command = commands.compose_command(image_path, caption, width, height, config.font_size)
    runner(command, check=True)
    return qid


def run(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
    get_outputs: Callable[[], list[Output]] = sway_outputs,
    throttle: bool = False,
) -> list[int]:
    """Set a different random captioned painting on each connected display.

    With `throttle`, do nothing if the last change was more recent than
    `config.min_interval` — so this can be triggered from frequent Sway events
    (e.g. window focus) without thrashing the wallpaper. `rng`, `runner` and
    `get_outputs` are injected so tests can drive run() deterministically — no
    mocks, no real Sway.
    """
    config = config or Config.load()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    if throttle and cache.fresh(config.stamp, config.min_interval):
        return []

    ids = painting_ids(config)

    shown: list[int] = []
    for output in get_outputs():
        image_path = config.output_image(output.name)
        shown.append(
            _render(config, rng, runner, ids, shown, image_path, output.width, output.height)
        )
        runner(commands.wallpaper_command(output.name, image_path), check=True)

    config.stamp.touch()
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
    config = config or Config.load()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    ids = painting_ids(config)
    _render(config, rng, runner, ids, [], config.preview_image, PREVIEW_WIDTH, PREVIEW_HEIGHT)
    runner(commands.open_command(config.preview_image), check=True)

    return config.preview_image
