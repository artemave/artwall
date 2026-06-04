# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python tool that sets a random painting from
[Wikidata](https://www.wikidata.org/) (every `instance of: painting` that has an
image — ~400k) as the **Sway** desktop wallpaper. The painting is composed (via
ImageMagick) onto a display-sized canvas so it's shown *whole* (no cropping); the
letterbox margins are filled with a soft gradient sampled from the painting's own
colours. The caption (artist/title/date) is shown one of two ways, set by
`caption_mode`: `"link"` (default) draws it as an interactive overlay (a separate
`artwall.overlay` process — see below) with a clickable Wikipedia link, leaving
the wallpaper caption-free; `"text"` burns it into the corner. The default
`collections` is a curated set of clean-scan, open-access museums
(`DEFAULT_COLLECTIONS` — Rijksmuseum, Cleveland, …) so the wallpaper is the
artwork, not a framed-on-the-wall photo; its catalogue **ships pre-fetched**
(`artwall/catalogue/`, regenerate with `make catalogue`) so the first run skips
the rate-limited WDQS. `collections = []` draws from *all* ~400k paintings. A TOML
file at `~/.config/artwall/config.toml` narrows it via a date window + QID filters
(`movements`/`genres`/`artists`/`collections`) and sets
`language`/`font_size`/`caption_mode`. It's a **oneshot** — sets the
wallpaper once and exits. Rotation is driven by
Sway events, not a daemon: the Sway config subscribes to window-focus events and
runs artwall on each, with `--throttle` (using `Config.min_interval`) limiting it
to ~every 30 min. Launched as a child of Sway, it inherits `SWAYSOCK` — no
systemd, no env import. The caption is drawn in the desktop's system font
(`gsettings` for the name/size + `fc-match` to resolve the file) at a point size
scaled per display, so it looks the same physical size on HiDPI screens;
`font_size` overrides the size. The **core oneshot has no third-party Python
dependencies — keep it that way** (use `urllib`, not `requests`); external CLI
tools (`swaymsg`, `magick`, `gsettings`, `fc-match`) are fine since we already
shell out. The one exception is `artwall/overlay.py` (the `"link"`-mode widget),
which needs PyGObject + gtk-layer-shell — it's the lone GUI/daemon component and
is quarantined there (omitted from coverage; typed against GTK3 PyGObject-stubs).

## Commands

```bash
python3 -m unittest discover -s tests   # run all tests
python3 -m unittest tests.test_app      # run one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper  # one test
make install-dev                        # dev tooling: ruff, mypy, coverage, GTK3 stubs
make catalogue                          # regenerate the shipped first-run catalogue (hits WDQS)
make check                              # all checks: lint + typecheck + 100%-coverage-gated tests
make lint / make typecheck / make test / make coverage  # individual targets (configs: ruff.toml, mypy.ini, .coveragerc)
python3 -m artwall                       # set the wallpaper once (hits network + swaymsg + magick)
python3 -m artwall --throttle            # set once, but no-op if changed < Config.min_interval ago (event throttle)
python3 -m artwall --throttle --min-interval 5  # throttle with a 5s window (coalesce a hotplug's output-event burst)
python3 -m artwall --find impressionism  # look up Wikidata QIDs for the config filters
python3 -m artwall.overlay               # the "link"-mode caption overlay (needs PyGObject + gtk-layer-shell)
```

## Architecture

The entry point is intentionally thin; all logic lives in importable modules so
it can be tested without network or `swaymsg`.

- `artwall/config.py` — `Config` dataclass holding cache paths, the Wikidata
  endpoints (`sparql_url` for WDQS, `api_url` for the Action API, `commons_url`
  for images), `ids_ttl`, the content knobs
  (`date_begin`/`date_end`, `language`, `artists`/`movements`/`genres`/
  `collections` QID lists, `font_size`, `caption_mode`) and `min_interval`.
  `collections` defaults to `DEFAULT_COLLECTIONS` (curated clean-scan museums).
  `caption_file(name)` is where `run()` writes a display's caption + link for the
  overlay (`caption-<name>.json`). The field defaults
  are the built-ins; `Config.load(path)` overlays the user's TOML (`config_file()`
  → `$XDG_CONFIG_HOME/artwall/config.toml`), passing keys straight to the
  constructor so a typo fails loudly. `ids_filename(query)` is the md5-of-query
  catalogue filename (so changing a filter refetches); `ids_file()` is it under
  `cache_dir`, `bundled_ids_file()` under `catalogue_dir` (the shipped seed). **This
  is the test seam:** tests build a `Config` pointing at a temp dir and a local HTTP
  server (and an empty `catalogue_dir`, so they fetch rather than read the bundle).
  Don't hardcode paths/URLs elsewhere.
- `artwall/cache.py` — JSON load/save + `fresh()` (mtime-based TTL).
- `artwall/web.py` — low-level HTTP (`get_json`, `get_text`, `download`) over
  `urllib`, with a Wikimedia-compliant User-Agent and an `accept` arg (WDQS picks
  its result format — JSON vs CSV — from the `Accept` header, not a query param).
- `artwall/wikidata.py` — **pure** source logic: build the catalogue SPARQL
  (`catalogue_query`, filters → `VALUES`/property clauses) and parse its CSV
  (`parse_catalogue`); parse an Action-API entity into image-filename/creator/
  title/year (`parse_entity`) and read a `label`; parse the entity search
  (`parse_search`); build the sized Commons image URL from a filename
  (`image_url` → `Special:FilePath/<file>?width=`); pull the Wikipedia article
  URL from a `sitelinks/urls` response (`parse_sitelink`) and build the
  always-present Wikidata page URL (`entity_url`, the article fallback). Prefer
  adding source logic here. **Two services on purpose:** WDQS (`sparql_url`) is outage-prone, so it's
  used *only* for the monthly catalogue; every per-painting fetch goes to the
  stable Action API.
- `artwall/selection.py` — **pure** `caption` formatting (artist/title/date).
- `artwall/commands.py` — pure argv builders for `magick` (the gradient-canvas
  compose + optional caption; `text=None` composes the painting bare, for
  `"link"` mode) and `swaymsg`.
- `artwall/app.py` — orchestration. `run(config, rng, runner, get_outputs,
  get_font, throttle)` injects `rng`, `runner`, `get_outputs`, and `get_font`
  (defaulting to `random`, `subprocess.run`, `sway_outputs`, and `system_font`)
  so the full flow can be driven deterministically. `search_entities()` backs
  `--find`. In `"link"` mode it skips the caption burn, resolves the Wikipedia
  URL (`_wiki_url`), and writes `caption_file(name)` for the overlay.
- `artwall/overlay.py` — the `"link"`-mode interactive caption: a persistent
  GTK3 + gtk-layer-shell widget (`python3 -m artwall.overlay`, launched from the
  Sway config) showing one `BOTTOM`-layer clickable caption per display, matched
  to GTK monitors **by geometry** (GTK exposes the monitor model, not the Sway
  connector name) and reloaded via a `Gio.FileMonitor` on the cache dir whenever
  `run()` rewrites a `caption-<name>.json`. **The lone module that needs a GUI
  toolkit + a live display + a long-lived process** — kept out of the stdlib-only
  oneshot, omitted from coverage, but type-checked (GTK3 PyGObject-stubs, built
  via `PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3` in `make install-dev`).

Flow in `run()`: if `throttle` and `config.stamp` was touched more recently than
`config.min_interval`, return early (the event-driven throttle). Otherwise:
fetch/cache the catalogue (`painting_ids()`: a fresh per-filter-set cache wins;
else on a true first run, seed from the shipped `bundled_ids_file()` if present —
the default filters ship one, so no WDQS hit; else one SPARQL query → all matching
painting QIDs as a CSV of bare ints, cached under `painting-ids-<hash>.json`.
`dump_catalogue()` / `make catalogue` regenerates the shipped seed) → query the
active outputs (`get_outputs`, default `sway_outputs()` → `swaymsg -t
get_outputs`; each is an `Output` carrying name + pixel size + HiDPI scale) and
the system font (`get_font`, default `system_font()`) → for each display,
pick a random QID and fetch its image filename + title/date via the Action API
(`wbgetentities`), then a second `wbgetentities` for the creator's name (retry up
to `ATTEMPTS` only to skip a QID that has since lost its image), build and
download a width-capped Commons thumbnail, `magick`-compose it onto an
`Output`-sized gradient canvas (whole painting; caption burned in only in
`"text"` mode) at `current-<output>.jpg`, `swaymsg output <name> bg … fill` (a
1:1 blit, since the canvas is already the display's size); in `"link"` mode also
write `caption-<output>.json` (text + Wikipedia URL) for the overlay → touch
`config.stamp`. Selection is plain random — no persisted history — but QIDs
already chosen this run are excluded so each display gets a *different* painting.
The pick/download/compose step is `_render()` (takes the target width/height and
a `burn_caption` flag), also used by `preview()` (the `--preview` flag), which
always burns the caption (a preview is one self-contained image), composes at a
default 1920x1080, writes `preview.jpg`, opens it with `xdg-open`, and leaves the
wallpaper untouched.

`sway_outputs()` and `system_font()` are the functions excluded from coverage
(`# pragma: no cover`) — they need a live Sway compositor / desktop; their pure
parsing+math is split out and tested (`parse_outputs()`, and `parse_font_name()`
+ `scaled_pointsize()`). `artwall/overlay.py` is excluded wholesale (`.coveragerc`
omit) — it can't run headless. All state is cached under `~/.cache/artwall/`;
deleting it is a safe reset.

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

No installer and no systemd. The user adds `exec` lines to their Sway config: one
to set a wallpaper at startup; one subscribing to window events that runs artwall
per event with `--throttle`; one subscribing to output events with `--throttle
--min-interval 5` so a monitor hotplug re-rolls (the short interval coalesces the
event burst a single hotplug fires — any run sets every connected display, so the
new screen gets a wallpaper); and, in `"link"` mode, `bin/artwall-overlay` for the
caption overlay daemon (which itself rebuilds its surfaces on monitor hotplug via
`Gdk.Display` `monitor-added`/`monitor-removed`). `bin/artwall`
and `bin/artwall-overlay` are small shell launchers that set `PYTHONPATH` to the
repo and exec `python3 -m artwall "$@"` / `python3 -m artwall.overlay`. A failed
run prints to Sway's stderr and is
skipped; it doesn't touch the stamp, so the next event retries. Because the
process is a child of Sway it inherits `SWAYSOCK`, so `swaymsg` works with no
environment import (`swaymsg` talks to the IPC socket, it does not need
`WAYLAND_DISPLAY`). Nothing is pip-installed, so the checkout must stay put — the
`exec` line points at it.

Rotation itself is event-driven and self-throttled via `config.stamp`'s mtime —
the oneshot never lingers. The one persistent process of ours is the optional
`artwall.overlay` daemon (`"link"` mode only); in `"text"` mode there is none,
and the only standing process is the stock `swaymsg -t subscribe` pipe.
