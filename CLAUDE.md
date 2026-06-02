# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python tool that sets a random painting from
[Wikidata](https://www.wikidata.org/) (every `instance of: painting` that has an
image — ~400k) as the **Sway** desktop wallpaper. The painting is composed (via
ImageMagick) onto a display-sized canvas so it's shown *whole* (no cropping); the
letterbox margins are filled with a soft gradient sampled from the painting's own
colours, and a caption (artist/title/date) is burned into the corner. The default
is *all* paintings; a TOML file at `~/.config/artwall/config.toml` narrows it via
a date window + QID filters (`movements`/`genres`/`artists`/`collections`) and
sets `language`/`font_size`. It's a **oneshot** — sets the wallpaper once and
exits. Rotation is driven by
Sway events, not a daemon: the Sway config subscribes to window-focus events and
runs artwall on each, with `--throttle` (using `Config.min_interval`) limiting it
to ~every 30 min. Launched as a child of Sway, it inherits `SWAYSOCK` — no
systemd, no env import. No third-party Python dependencies — keep it that way
(use `urllib`, not `requests`); external CLI tools (`swaymsg`, `magick`) are fine
since we already shell out.

## Commands

```bash
python3 -m unittest discover -s tests   # run all tests
python3 -m unittest tests.test_app      # run one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper  # one test
make install-dev                        # dev tooling: ruff, mypy, coverage
make check                              # all checks: lint + typecheck + 100%-coverage-gated tests
make lint / make typecheck / make test / make coverage  # individual targets (configs: ruff.toml, mypy.ini, .coveragerc)
python3 -m artwall                       # set the wallpaper once (hits network + swaymsg + magick)
python3 -m artwall --throttle            # set once, but no-op if changed < Config.min_interval ago (event throttle)
python3 -m artwall --find impressionism  # look up Wikidata QIDs for the config filters
```

## Architecture

The entry point is intentionally thin; all logic lives in importable modules so
it can be tested without network or `swaymsg`.

- `artwall/config.py` — `Config` dataclass holding cache paths, the Wikidata
  endpoints (`sparql_url` for WDQS, `api_url` for the Action API, `commons_url`
  for images), `ids_ttl`, the content knobs
  (`date_begin`/`date_end`, `language`, `artists`/`movements`/`genres`/
  `collections` QID lists, `font_size`) and `min_interval`. The field defaults
  are the built-ins; `Config.load(path)` overlays the user's TOML (`config_file()`
  → `$XDG_CONFIG_HOME/artwall/config.toml`), passing keys straight to the
  constructor so a typo fails loudly. `ids_file(query)` keys the catalogue cache
  by an md5 of the query, so changing a filter transparently refetches. **This is
  the test seam:** tests build a `Config` pointing at a temp dir and a local HTTP
  server. Don't hardcode paths/URLs elsewhere.
- `artwall/cache.py` — JSON load/save + `fresh()` (mtime-based TTL).
- `artwall/web.py` — low-level HTTP (`get_json`, `get_text`, `download`) over
  `urllib`, with a Wikimedia-compliant User-Agent and an `accept` arg (WDQS picks
  its result format — JSON vs CSV — from the `Accept` header, not a query param).
- `artwall/wikidata.py` — **pure** source logic: build the catalogue SPARQL
  (`catalogue_query`, filters → `VALUES`/property clauses) and parse its CSV
  (`parse_catalogue`); parse an Action-API entity into image-filename/creator/
  title/year (`parse_entity`) and read a `label`; parse the entity search
  (`parse_search`); build the sized Commons image URL from a filename
  (`image_url` → `Special:FilePath/<file>?width=`). Prefer adding source logic
  here. **Two services on purpose:** WDQS (`sparql_url`) is outage-prone, so it's
  used *only* for the monthly catalogue; every per-painting fetch goes to the
  stable Action API.
- `artwall/selection.py` — **pure** `caption` formatting (artist/title/date).
- `artwall/commands.py` — pure argv builders for `magick` (the gradient-canvas
  compose + caption) and `swaymsg`.
- `artwall/app.py` — orchestration. `run(config, rng, runner, get_outputs,
  throttle)` injects `rng`, `runner`, and `get_outputs` (defaulting to `random`,
  `subprocess.run`, and `sway_outputs`) so the full flow can be driven
  deterministically. `search_entities()` backs `--find`.

Flow in `run()`: if `throttle` and `config.stamp` was touched more recently than
`config.min_interval`, return early (the event-driven throttle). Otherwise:
fetch/cache the catalogue (one SPARQL query → all matching painting QIDs as a CSV
of bare ints, cached per filter-set under `painting-ids-<hash>.json`) → query the
active outputs (`get_outputs`, default `sway_outputs()` → `swaymsg -t
get_outputs`; each is an `Output` carrying name + pixel size) → for each display,
pick a random QID and fetch its image filename + title/date via the Action API
(`wbgetentities`), then a second `wbgetentities` for the creator's name (retry up
to `ATTEMPTS` only to skip a QID that has since lost its image), build and
download a width-capped Commons thumbnail, `magick`-compose it onto an
`Output`-sized gradient canvas (whole painting + caption) at
`current-<output>.jpg`, `swaymsg output <name> bg … fill` (a 1:1 blit, since the
canvas is already the display's size) → touch `config.stamp`. Selection is plain
random — no persisted history — but QIDs already chosen this run are excluded so
each display gets a *different* painting.
The pick/download/compose step is `_render()` (takes the target width/height),
also used by `preview()` (the `--preview` flag), which composes at a default
1920x1080, writes `preview.jpg`, opens it with `xdg-open`, and leaves the
wallpaper untouched.

`sway_outputs()` is the one function excluded from coverage (`# pragma: no
cover`) — it needs a live Sway compositor; its JSON parsing (name +
`current_mode` size) is split into the pure, tested `parse_outputs()`. All
state is cached under `~/.cache/artwall/`; deleting it is a safe reset.

## Testing conventions

No mocks (per the repo's global rule). Achieved by:
- Pure functions tested directly.
- The HTTP layer tested against a **real** loopback server (`tests/server.py`,
  `serve(router)`).
- `run()` tested with a real seeded `random.Random` and a `Recorder` callable
  that captures argv instead of executing `magick`/`swaymsg`.

When testing anything that does IO, follow this pattern (local server +
injected `runner`/`rng`) rather than reaching for `unittest.mock`.

## Deployment notes

No installer and no systemd. The user adds two lines to their Sway config: one
`exec` to set a wallpaper at startup, and one that subscribes to window events
and runs artwall per event with `--throttle` (see README). `bin/artwall`
is a small shell launcher that sets `PYTHONPATH` to the repo and execs `python3
-m artwall "$@"` — nothing more. A failed run prints to Sway's stderr and is
skipped; it doesn't touch the stamp, so the next event retries. Because the
process is a child of Sway it inherits `SWAYSOCK`, so `swaymsg` works with no
environment import (`swaymsg` talks to the IPC socket, it does not need
`WAYLAND_DISPLAY`). Nothing is pip-installed, so the checkout must stay put — the
`exec` line points at it.

There is no long-running process of ours — rotation is event-driven and
self-throttled via `config.stamp`'s mtime, so the only persistent process is the
stock `swaymsg -t subscribe` pipe in the Sway config.
