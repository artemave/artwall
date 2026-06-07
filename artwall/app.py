from __future__ import annotations

import contextlib
import fcntl
import json
import random
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from . import cache, commands, selection, web, wikidata
from .config import Config

# A cached QID may have lost its image or been deleted since; re-pick a few times
# before giving up. Hits are rare, so this almost always succeeds first try.
ATTEMPTS = 10

# Caption presentation: burn text into the wallpaper, or show the interactive overlay.
CAPTION_MODES = ("interactive", "text")

# Preview has no display to target, so render it at a common desktop size.
PREVIEW_WIDTH, PREVIEW_HEIGHT = 1920, 1080

# Reference DPI for an unscaled display (the X/CSS convention). A point is 1/72
# inch, so a point maps to BASE_DPI/72 device pixels before the output's scale.
BASE_DPI = 96


class Output(NamedTuple):
    """A connected display: its name, pixel size, and HiDPI scale factor."""

    name: str
    width: int
    height: int
    scale: float = 1.0


def painting_ids(config: Config) -> list[int]:
    query = wikidata.catalogue_query(config.filters, config.date_begin, config.date_end)
    cache_file = config.ids_file(query)
    if cache.fresh(cache_file, config.ids_ttl):
        cached: list[int] = cache.load_json(cache_file, [])
        return cached

    # First run for this filter-set: seed from the packaged catalogue if we ship
    # one (the default filters do), so we don't hit the rate-limited WDQS at all.
    bundled = config.bundled_ids_file(query)
    if not cache_file.exists() and bundled.exists():
        seeded: list[int] = cache.load_json(bundled, [])
        cache.save_json(cache_file, seeded)  # adopt it into the cache; TTL takes over
        return seeded

    csv_text = web.get_text(config.sparql_url, {"query": query}, accept="text/csv")
    ids = wikidata.parse_catalogue(csv_text)

    if not ids:
        raise RuntimeError("Wikidata returned no paintings for the configured filters")

    cache.save_json(cache_file, ids)
    return ids


def dump_catalogue() -> Path:  # pragma: no cover - hits WDQS, writes packaged data
    """Fetch the default filter-set's catalogue from WDQS and write the packaged
    seed (`make catalogue`). Run when the default `collections` change."""
    config = Config()  # built-in defaults, not the user's TOML
    query = wikidata.catalogue_query(config.filters, config.date_begin, config.date_end)
    ids = wikidata.parse_catalogue(
        web.get_text(config.sparql_url, {"query": query}, accept="text/csv")
    )
    if not ids:
        raise RuntimeError("Wikidata returned no paintings for the default filters")
    dest = config.bundled_ids_file(query)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cache.save_json(dest, ids)
    return dest


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
    """Active outputs (name + pixel size + scale) from `swaymsg -t get_outputs -r`."""
    return [
        Output(o["name"], o["current_mode"]["width"], o["current_mode"]["height"], o["scale"])
        for o in json.loads(raw)
        if o["active"]
    ]


def sway_outputs() -> list[Output]:  # pragma: no cover - needs a live Sway compositor
    result = subprocess.run(
        commands.outputs_command(), capture_output=True, text=True, check=True
    )
    return parse_outputs(result.stdout)


def parse_font_name(name: str) -> tuple[str, int]:
    """Split a desktop font setting like "Adwaita Sans 11" into (family, point size)."""
    family, _, size = name.rpartition(" ")
    return family, int(size)


def system_font() -> tuple[str, int]:  # pragma: no cover - reads the live desktop
    """The desktop's UI font as (file, point size), via gsettings + fontconfig."""
    name = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "font-name"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().strip("'")
    family, size = parse_font_name(name)
    file = subprocess.run(
        ["fc-match", "-f", "%{file}", family],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return file, size


def scaled_pointsize(point_size: int, scale: float) -> int:
    """A point size as the magick pointsize for a display at `scale` (HiDPI-aware)."""
    return round(point_size * BASE_DPI * scale / 72)


def _sitelink(config: Config, entity_id: str) -> str | None:
    """The entity's Wikipedia article URL (in `config.language`), or None."""
    result = _get_entity(config, entity_id, "sitelinks/urls")
    return wikidata.parse_sitelink(result, entity_id, config.language)


def _wiki_url(config: Config, qid: int, creator_qid: str) -> str:
    """A human-readable link for the painting: its own Wikipedia article if it has
    one, else its artist's (paintings rarely do, artists usually do), else — as a
    last resort — its Wikidata page."""
    return (
        _sitelink(config, f"Q{qid}")
        or (creator_qid and _sitelink(config, creator_qid))
        or wikidata.entity_url(qid)
    )


def _render(
    config: Config,
    rng: random.Random,
    runner: Callable[..., object],
    ids: list[int],
    exclude: list[int],
    image_path: Path,
    width: int,
    height: int,
    scale: float,
    font: str | None,
    point_size: int,
    burn_caption: bool,
) -> tuple[int, str, str]:
    """Pick a painting (avoiding `exclude`), download it, and compose it for `width`x`height`.

    When `burn_caption`, the caption is drawn in `font` at `point_size`, converted
    to a pixel size for this display's `scale` so it looks the same physical size
    on any resolution; otherwise the painting is composed bare and a Wikipedia
    link is resolved (interactive-overlay mode). Returns the chosen QID, its
    caption text, and the link (empty when burning, where it isn't needed).
    """
    qid, painting = choose(config, ids, rng, exclude)
    image = wikidata.image_url(config.commons_url, painting["image"], width)
    web.download(image, image_path)
    caption = selection.caption(painting)
    command = commands.compose_command(
        image_path,
        caption if burn_caption else None,
        width,
        height,
        scaled_pointsize(point_size, scale),
        config.caption_corner,
        config.caption_pad_x,
        config.caption_pad_y,
        font,
    )
    runner(command, check=True)
    url = "" if burn_caption else _wiki_url(config, qid, painting["creator_qid"])
    return qid, caption, url


@contextlib.contextmanager
def _single_instance(path: Path) -> Iterator[bool]:
    """Hold an exclusive, non-blocking lock for the duration of a run, so two
    overlapping triggers can't both rotate. Window-focus and output events fire
    independently — and a run's own `swaymsg output … bg` calls emit output
    events — so without this they cascade into several wallpaper changes in a row.
    Yields True to the one run that acquires the lock; yields False (run nothing)
    to any trigger that arrives while another run already holds it."""
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        handle.close()  # closing the descriptor releases the flock


def run(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
    get_outputs: Callable[[], list[Output]] = sway_outputs,
    get_font: Callable[[], tuple[str, int]] = system_font,
    throttle: bool = False,
    min_interval: float | None = None,
    only: str | None = None,
) -> list[int]:
    """Set a different random captioned painting on each connected display.

    With `throttle`, do nothing if the last change was more recent than
    `min_interval` seconds (default `config.min_interval`) — so this can be
    triggered from frequent Sway events without thrashing the wallpaper. A small
    `min_interval` suits output events (coalesce a hotplug's burst); the long
    default suits window events. `only` restricts the change to the single output
    of that name (the overlay's refresh button re-rolls just its own display).
    `rng`, `runner`, `get_outputs` and `get_font` are injected so tests can drive
    run() deterministically — no mocks, no real Sway.
    """
    config = config or Config.load()
    rng = rng or random.Random()
    if config.caption_mode not in CAPTION_MODES:
        raise ValueError(f"unknown caption_mode: {config.caption_mode!r} (use {CAPTION_MODES})")
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    with _single_instance(config.lock) as acquired:
        # A run already in progress will set every display itself; this overlapping
        # trigger is redundant, so drop it rather than rotate a second time.
        if not acquired:
            return []

        interval = config.min_interval if min_interval is None else min_interval
        if throttle and cache.fresh(config.stamp, interval):
            return []

        # "text" burns the caption with the system font; "interactive" composes bare
        # and writes the caption + Wikipedia link for the overlay, so it needs no font.
        burn = config.caption_mode == "text"
        if burn:
            font, sys_size = get_font()
            point_size = config.font_size if config.font_size is not None else sys_size
        else:
            font, point_size = None, 0
        ids = painting_ids(config)

        displays = get_outputs()
        if only is not None:
            displays = [o for o in displays if o.name == only]
            if not displays:
                raise RuntimeError(f"no active output named {only!r}")

        shown: list[int] = []
        for output in displays:
            image_path = config.output_image(output.name)
            qid, caption, url = _render(
                config, rng, runner, ids, shown, image_path,
                output.width, output.height, output.scale, font, point_size, burn,
            )
            shown.append(qid)
            runner(commands.wallpaper_command(output.name, image_path), check=True)
            if not burn:
                cache.save_json(config.caption_file(output.name), {"text": caption, "url": url})

        config.stamp.touch()
        return shown


def preview(
    config: Config | None = None,
    rng: random.Random | None = None,
    runner: Callable[..., object] = subprocess.run,
    get_font: Callable[[], tuple[str, int]] = system_font,
) -> Path:
    """Generate one random captioned painting and open it, changing nothing else.

    The wallpaper is left untouched — this just writes a preview image and hands
    it to the system image viewer.
    """
    config = config or Config.load()
    rng = rng or random.Random()
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    # Preview is a single self-contained image, so it always burns the caption
    # in (the overlay only applies to the live wallpaper), regardless of mode.
    font, sys_size = get_font()
    point_size = config.font_size if config.font_size is not None else sys_size
    ids = painting_ids(config)
    _render(
        config, rng, runner, ids, [], config.preview_image,
        PREVIEW_WIDTH, PREVIEW_HEIGHT, 1.0, font, point_size, True,
    )
    runner(commands.open_command(config.preview_image), check=True)

    return config.preview_image
