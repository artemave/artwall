# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
the [Metropolitan Museum of Art's open collection](https://metmuseum.github.io/).
Each run picks random paintings and sets a **different** one on each connected
display. Every painting is shown *whole* — never cropped — centered on a soft
gradient (sampled from its own colours) that fills the margins, with a small
caption (artist, title, date) in the bottom-right corner. Pure Python standard
library — no third-party dependencies.

Requires Python 3.10+, `swaymsg` (Sway), and ImageMagick 7 (`magick`, for the
caption).

## Usage

Run it from this checkout — there's nothing to install. Add to your Sway config
(`~/.config/sway/config`), pointing at where you cloned it, to set a wallpaper at
startup and re-roll on window focus (throttled to once every 30 min):

```
exec /path/to/artwall/bin/artwall
exec swaymsg -t subscribe -m '["window"]' | while read -r _; do /path/to/artwall/bin/artwall --min-interval 1800; done
```

`--min-interval` makes a frequent trigger a no-op until that many seconds have
passed, so the wallpaper rotates while you're active and pauses while you're
away. Running as a child of Sway, it inherits `SWAYSOCK` automatically.

To drive it by hand:

```bash
python3 -m artwall            # set the wallpaper once
python3 -m artwall --preview  # open a captioned painting without changing the wallpaper
```

State lives under `~/.cache/artwall/`; deleting it is a safe full reset.

## Development

Run from a checkout without installing:

```bash
python3 -m artwall
```

Tests use the standard-library `unittest` runner — no mocks:

```bash
python3 -m unittest discover -s tests        # everything
python3 -m unittest tests.test_selection     # one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper
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
