# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
the [Metropolitan Museum of Art's open collection](https://metmuseum.github.io/).
Each run picks an artwork that hasn't been shown recently, downloads it, burns a
small caption (artist, title, date) into the bottom-right corner, and sets it as
the background on every output. Pure Python standard library — no third-party
dependencies.

Requires Python 3.10+, `swaymsg` (Sway), and ImageMagick 7 (`magick`, for the
caption).

## Install

```bash
./install.sh
```

This generates systemd **user** units (in `~/.config/systemd/user/`) that run
artwall straight from this checkout, and enables a timer that changes the
wallpaper every 30 minutes. Nothing is installed elsewhere, so keep this
directory in place — to uninstall, disable the timer and delete the units.

Because it runs as a user service, it needs `WAYLAND_DISPLAY` and `SWAYSOCK` in
the systemd user environment. Add this to `~/.config/sway/config`:

```
exec systemctl --user import-environment WAYLAND_DISPLAY SWAYSOCK
```

## Usage

```bash
systemctl --user start artwall.service      # change the wallpaper now
systemctl --user list-timers artwall.timer  # see the next scheduled run
journalctl --user -u artwall.service        # logs
python3 -m artwall                          # run directly, outside systemd
python3 -m artwall --preview                # open a captioned painting in your
                                            # image viewer without changing anything
```

Change the cadence by editing `OnUnitActiveSec` in
`~/.config/systemd/user/artwall.timer`, then `systemctl --user daemon-reload`.

State lives under `~/.cache/artwall/` (cached IDs, metadata, history, and the
current image). Deleting it is a safe full reset.

## Development

Run from a checkout without installing:

```bash
python3 -m artwall
```

Tests use the standard-library `unittest` runner — no mocks:

```bash
python3 -m unittest discover -s tests        # everything
python3 -m unittest tests.test_selection     # one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper_and_records_history
```

Install the dev tooling, then run every check (lint, typecheck, coverage gate)
with one command:

```bash
make install-dev   # pip install -r requirements-dev.txt
make check         # ruff + mypy + tests under the 100% coverage gate
```

Individual targets are also available: `make lint`, `make typecheck`,
`make test`, `make coverage`.

Coverage is kept at 100% on everything except the entry-point shim, enforced by
`fail_under` in `.coveragerc`. The package is fully type-annotated and checked
under mypy `strict`.

Logic is split out of the entry point so it stays testable: pure decision logic
in `selection.py`/`commands.py`, the HTTP client in `met.py`, orchestration in
`app.py`. Tests exercise the HTTP layer against a real loopback `http.server`,
and drive `run()` with a seeded `random.Random` and a recording runner that
captures the `swaymsg` argv instead of launching it. See `CLAUDE.md` for the
full module breakdown.
