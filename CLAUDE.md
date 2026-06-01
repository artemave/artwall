# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python tool that sets a random painting from the
[Met Museum open collection API](https://metmuseum.github.io/) as the **Sway**
desktop wallpaper, with a caption (artist/title/date) burned into the corner via
ImageMagick. Designed to run on a systemd user timer. No third-party Python
dependencies — keep it that way (use `urllib`, not `requests`); external CLI
tools (`swaymsg`, `magick`) are fine since we already shell out.

## Commands

```bash
python3 -m unittest discover -s tests   # run all tests
python3 -m unittest tests.test_app      # run one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper_and_records_history  # one test
make install-dev                        # dev tooling: ruff, mypy, coverage
make check                              # all checks: lint + typecheck + 100%-coverage-gated tests
make lint / make typecheck / make test / make coverage  # individual targets (configs: ruff.toml, mypy.ini, .coveragerc)
python3 -m artwall                       # run the app from a checkout (hits network + swaymsg + magick)
./install.sh                             # write systemd user units pointing at this checkout + enable the timer
```

## Architecture

The entry point is intentionally thin; all logic lives in importable modules so
it can be tested without network or `swaymsg`.

- `artwall/config.py` — `Config` dataclass holding cache paths, Met endpoints,
  and TTLs. **This is the test seam:** tests build a `Config` pointing at a temp
  dir and a local HTTP server. Don't hardcode paths/URLs elsewhere.
- `artwall/cache.py` — JSON load/save + `fresh()` (mtime-based TTL).
- `artwall/met.py` — low-level HTTP (`get_json`, `download`) over `urllib`.
- `artwall/selection.py` — **pure** decision logic (unseen-ID picking with
  reset-on-exhaustion, image-URL precedence, history trim). Prefer adding new
  logic here so it stays unit-testable.
- `artwall/commands.py` — pure argv builders for `magick` (caption) and
  `swaymsg`.
- `artwall/app.py` — orchestration. `run(config, rng, runner)` injects `rng`
  and `runner` (defaulting to `random` and `subprocess.run`) so the full flow
  can be driven deterministically.

Flow in `run()`: fetch/cache painting IDs → pick an unseen ID with an image
(retry up to `ATTEMPTS`) → download → `magick` burn caption in place → `swaymsg`
set bg → append to history. All state is cached under `~/.cache/artwall/`;
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

`systemd/artwall.service` is a **template** (`__REPO__`/`__PYTHON__`
placeholders); `install.sh` substitutes the checkout path and the concrete
interpreter (`sys.executable`, not a PATH/mise shim) and writes the result to
`~/.config/systemd/user/`. The service runs `python3 -m artwall` with
`PYTHONPATH` set to the repo — nothing is pip-installed, so the checkout must
stay put. It's a user oneshot run by `artwall.timer`, so it needs
`WAYLAND_DISPLAY`/`SWAYSOCK` imported into the systemd user environment from
Sway (`exec systemctl --user import-environment ...`).
