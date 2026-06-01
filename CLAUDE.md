# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python tool that sets a random painting from the
[Met Museum open collection API](https://metmuseum.github.io/) as the **Sway**
desktop wallpaper, with a caption (artist/title/date) burned into the corner via
ImageMagick. It's a **oneshot** — sets the wallpaper once and exits. Rotation is
driven by Sway events, not a daemon: the Sway config subscribes to window-focus
events and runs artwall on each, with `--min-interval` throttling it to ~every 30
min. Launched as a child of Sway, it inherits `SWAYSOCK` — no systemd, no env
import. No third-party Python dependencies — keep it that way (use `urllib`, not
`requests`); external CLI tools (`swaymsg`, `magick`) are fine since we already
shell out.

## Commands

```bash
python3 -m unittest discover -s tests   # run all tests
python3 -m unittest tests.test_app      # run one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper  # one test
make install-dev                        # dev tooling: ruff, mypy, coverage
make check                              # all checks: lint + typecheck + 100%-coverage-gated tests
make lint / make typecheck / make test / make coverage  # individual targets (configs: ruff.toml, mypy.ini, .coveragerc)
python3 -m artwall                       # set the wallpaper once (hits network + swaymsg + magick)
python3 -m artwall --min-interval 1800   # set once, but no-op if changed <1800s ago (event-driven throttle)
```

## Architecture

The entry point is intentionally thin; all logic lives in importable modules so
it can be tested without network or `swaymsg`.

- `artwall/config.py` — `Config` dataclass holding cache paths, Met endpoints,
  and TTLs. **This is the test seam:** tests build a `Config` pointing at a temp
  dir and a local HTTP server. Don't hardcode paths/URLs elsewhere.
- `artwall/cache.py` — JSON load/save + `fresh()` (mtime-based TTL).
- `artwall/met.py` — low-level HTTP (`get_json`, `download`) over `urllib`.
- `artwall/selection.py` — **pure** decision logic (image-URL precedence,
  caption formatting). Prefer adding new logic here so it stays unit-testable.
- `artwall/commands.py` — pure argv builders for `magick` (caption) and
  `swaymsg`.
- `artwall/app.py` — orchestration. `run(config, rng, runner, get_outputs,
  min_interval)` injects `rng`, `runner`, and `get_outputs` (defaulting to
  `random`, `subprocess.run`, and `sway_outputs`) so the full flow can be driven
  deterministically.

Flow in `run()`: if `min_interval` > 0 and `config.stamp` was touched more
recently than that, return early (the event-driven throttle). Otherwise:
fetch/cache painting IDs → query the active outputs (`get_outputs`, default
`sway_outputs()` → `swaymsg -t get_outputs`) → for each display, pick a random ID
with an image (retry up to `ATTEMPTS`), download, `magick`-caption to
`current-<output>.jpg`, `swaymsg output <name> bg` → touch `config.stamp`.
Selection is plain random — no persisted history — but ids already chosen this
run are excluded so each display gets a *different* painting. The
pick/download/caption step is `_render()`, also used by `preview()` (the
`--preview` flag), which writes `preview.jpg`, opens it with `xdg-open`, and
leaves the wallpaper untouched.

`sway_outputs()` is the one function excluded from coverage (`# pragma: no
cover`) — it needs a live Sway compositor; its JSON parsing is split into the
pure, tested `parse_outputs()`. All state is cached under `~/.cache/artwall/`;
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

No installer and no systemd. The user adds two lines to their Sway config: one
`exec` to set a wallpaper at startup, and one that subscribes to window events
and runs artwall per event with `--min-interval 1800` (see README). `bin/artwall`
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
