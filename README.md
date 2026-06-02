# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
[Wikidata](https://www.wikidata.org/) — every painting in the knowledge base
that has an image (~400k of them, from museums worldwide). Each run picks random
paintings and sets a **different** one on each connected display. Every painting
is shown *whole* — never cropped — centered on a soft gradient (sampled from its
own colours) that fills the margins, with a small caption (artist, title, date)
in the bottom-right corner. Pure Python standard library — no third-party
dependencies.

Requires Python 3.10+, `swaymsg` (Sway), and ImageMagick 7 (`magick`, for the
caption).

## Usage

Run it from this checkout — there's nothing to install. Add to your Sway config
(`~/.config/sway/config`), pointing at where you cloned it, to set a wallpaper at
startup and re-roll on window focus (throttled to once every 30 min):

```
exec /path/to/artwall/bin/artwall
exec swaymsg -t subscribe -m '["window"]' | while read -r _; do /path/to/artwall/bin/artwall --throttle; done
```

`--throttle` makes a frequent trigger a no-op until `Config.min_interval`
seconds (default 30 min) have passed since the last change, so the wallpaper
rotates while you're active and pauses while you're away. Running as a child of
Sway, artwall inherits `SWAYSOCK` automatically.

To drive it by hand, from the checkout:

```bash
./bin/artwall            # set the wallpaper once
./bin/artwall --preview  # open a captioned painting without changing the wallpaper
```

State lives under `~/.cache/artwall/`; deleting it is a safe full reset.

## Development

Run it with `./bin/artwall` (see [Usage](#usage)); there's nothing to install.

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

Logic is split out of the entry point so it stays testable: pure builders in
`wikidata.py` (the catalogue SPARQL + entity/result parsing), `selection.py` and
`commands.py`, the HTTP client in `web.py`, orchestration in `app.py`. Tests
exercise the HTTP layer against a real loopback `http.server` (a fake Wikidata),
and drive `run()` with a seeded `random.Random` and a recording runner that
captures the `swaymsg` argv instead of launching it. See `CLAUDE.md` for the
full module breakdown.
