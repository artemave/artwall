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


class Rendered(NamedTuple):
    """What `_render()` composed: the painting it picked, and its article link
    (empty when the caption was burned in, where no link is needed)."""

    qid: int
    painting: dict[str, str]
    url: str


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

    # The cache is stale (or absent with no bundle): refresh the monthly catalogue
    # from WDQS. WDQS is outage-prone, so if the fetch fails, fall back to whatever
    # catalogue we already have -- a stale list of ~400k paintings is fine, a
    # crashed wallpaper is not. The stale cache keeps its old mtime, so the next
    # run retries WDQS and self-heals once it recovers.
    try:
        csv_text = web.get_text(config.sparql_url, {"query": query}, accept="text/csv")
    except OSError:
        stale: list[int] = cache.load_json(cache_file, []) or cache.load_json(bundled, [])
        if stale:
            return stale
        raise
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
    """One `wbgetentities` call, labels narrowed to `config.language`.

    `languagefallback` matters: Wikidata stores a name that is spelled the same
    everywhere under the pseudo-language `mul` rather than duplicating it into
    300 languages, so plenty of artists have *no* label in any real language
    (Q22002875 — John Paul Selinger — has only `mul`). Asked for `en` alone the
    API answers with an empty `labels`, and the painting gets captioned "Unknown
    artist" while its own page plainly names the man. With the flag the API walks
    the fallback chain server-side and returns the result still keyed under the
    requested language, so `wikidata.label()` needs to know none of this.
    """
    result: dict[str, Any] = web.get_json(
        config.api_url,
        {
            "action": "wbgetentities",
            "ids": entity_id,
            "props": props,
            "languages": config.language,
            "languagefallback": "1",
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


def _image_size(config: Config, filename: str) -> tuple[int, int] | None:
    """An image's native pixel size (a Commons `imageinfo` call), or None if the
    file has since been deleted or renamed."""
    result = web.get_json(
        config.commons_api_url,
        {
            "action": "query",
            "titles": wikidata.FILE_PREFIX + filename,
            "prop": "imageinfo",
            "iiprop": "size",
            "format": "json",
        },
    )
    return wikidata.parse_image_size(result)


def choose(
    config: Config,
    ids: list[int],
    rng: random.Random,
    exclude: list[int],
    width: int,
    height: int,
    attempts: int = ATTEMPTS,
) -> tuple[int, dict[str, str]]:
    """Pick a random painting (avoiding `exclude`) and fetch its image + caption.

    Per-painting data comes from the Action API, not WDQS, so a query-service
    outage doesn't break runs once the catalogue is cached. `exclude` holds the
    ids already used this run, so several displays each get a different painting.
    `width`/`height` are the target display's pixel size: a candidate whose
    native image would be upscaled onto it (`wikidata.fits`) is skipped, same as
    one whose image has vanished — both just cost a retry.
    """
    candidates = [i for i in ids if i not in exclude]

    for _ in range(attempts):
        qid = rng.choice(candidates)
        result = _get_entity(config, f"Q{qid}", "claims|labels")
        painting = wikidata.parse_entity(result, qid, config.language)
        if not painting:
            continue
        size = _image_size(config, painting["image"])
        if size is None or not wikidata.fits(*size, width, height):
            continue
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


class LinkError(Exception):
    """A pasted link that doesn't lead to a painting we can star.

    Carries a message meant to be read by whoever pasted it, so the gallery can
    show it verbatim instead of translating error types into prose.
    """


def resolve_link(config: Config, link: str) -> dict[str, Any]:
    """Turn a pasted Wikipedia image link into the record `run()` writes.

    The image itself is the only thing a Wikipedia article gives you a link to —
    the article behind it is usually the *artist*, not the painting. So the file
    is the starting point: Commons' structured data says which Wikidata artwork
    the scan reproduces (P6243), and from that QID the ordinary lookup takes over.
    The result is indistinguishable from a painting that arrived on the wallpaper,
    which is what keeps the gallery, the trash and the archive a single code path.
    """
    title = wikidata.parse_file_link(link)
    if title is None:
        raise LinkError(
            "that link doesn't point at an image — open the painting on Wikipedia "
            "and copy the link to the image itself"
        )

    found = web.get_json(
        config.commons_api_url,
        {"action": "query", "titles": title, "format": "json"},
    )
    pageid = wikidata.parse_file_pageid(found)
    if pageid is None:
        # Wikimedia Commons holds the freely-licensed scans; an image that lives
        # only on a language Wikipedia is there because it *isn't* free to reuse.
        raise LinkError(f"{title} isn't on Wikimedia Commons, so it can't be archived")

    media_id = f"M{pageid}"
    structured = web.get_json(
        config.commons_api_url,
        {"action": "wbgetentities", "ids": media_id, "format": "json"},
    )
    qid = wikidata.parse_artwork_qid(structured, media_id)
    if qid is None:
        return _record_from_template(config, title, media_id)

    entity = _get_entity(config, f"Q{qid}", "claims|labels")
    if not wikidata.is_painting(entity, f"Q{qid}"):
        raise LinkError(f"{title} is linked to Q{qid}, which isn't a painting")

    painting = wikidata.parse_entity(entity, qid, config.language)
    if painting is None:
        raise LinkError(f"Q{qid} has no image on Wikidata")
    painting["artist"] = _artist(config, painting["creator_qid"])
    return selection.record(
        f"Q{qid}", painting, _wiki_url(config, qid, painting["creator_qid"])
    )


def _record_from_template(config: Config, title: str, media_id: str) -> dict[str, Any]:
    """The same record, for a file whose artwork exists only in its wikitext.

    Plenty of Commons scans describe the painting fully in an `{{Artwork}}`
    template — artist, title, date, "object type = painting" — while their
    structured data carries nothing but MIME type and pixel dimensions, and the
    template's own `|wikidata =` field sits empty. There is no QID to resolve and
    often no Wikidata item to resolve it to, so the file itself becomes the
    painting's identity: `M<pageid>`, and the Commons description page as its link.

    Everything downstream is unchanged, which is the point — the gallery, the
    trash and the archive still see one kind of record.
    """
    parsed = web.get_json(
        config.commons_api_url,
        {"action": "parse", "page": title, "prop": "wikitext", "format": "json"},
    )
    described = wikidata.parse_artwork_template(
        parsed["parse"]["wikitext"]["*"], config.language
    )
    if described is None:
        raise LinkError(
            f"{title} isn't linked to a painting on Wikidata, and its Commons page "
            "doesn't describe one either"
        )
    painting = {**described, "image": title.removeprefix(wikidata.FILE_PREFIX)}
    return selection.record(
        media_id, painting, wikidata.file_page_url(config.commons_file_url, title)
    )


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
) -> Rendered:
    """Pick a painting (avoiding `exclude`), download it, and compose it for `width`x`height`.

    When `burn_caption`, the caption is drawn in `font` at `point_size`, converted
    to a pixel size for this display's `scale` so it looks the same physical size
    on any resolution; otherwise the painting is composed bare and a Wikipedia
    link is resolved (interactive-overlay mode).
    """
    qid, painting = choose(config, ids, rng, exclude, width, height)
    image = wikidata.image_url(config.commons_url, painting["image"], width)
    web.download(image, image_path)
    command = commands.compose_command(
        image_path,
        selection.caption(painting) if burn_caption else None,
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
    return Rendered(qid, painting, url)


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
            rendered = _render(
                config, rng, runner, ids, shown, image_path,
                output.width, output.height, output.scale, font, point_size, burn,
            )
            shown.append(rendered.qid)
            runner(commands.wallpaper_command(output.name, image_path), check=True)
            if not burn:
                # everything the overlay needs to draw the caption, open the article,
                # and — if you click the star — record it without another lookup.
                cache.save_json(
                    config.caption_file(output.name),
                    selection.record(f"Q{rendered.qid}", rendered.painting, rendered.url),
                )

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
