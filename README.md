# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
[Wikidata](https://www.wikidata.org/) — every painting in the knowledge base
that has an image (~400k of them, from museums worldwide). Each run picks random
paintings and sets a **different** one on each connected display. Every painting
is shown *whole* — never cropped — centered on a soft gradient (sampled from its
own colours) that fills the margins, with a small caption (artist, title, date)
in the bottom-right corner. By default it draws from *all* paintings; a small
TOML file narrows that by date, movement, genre, artist or collection (see
[Configuration](#configuration)).

Requires Python 3.11+, `swaymsg` (Sway), and ImageMagick 7 (`magick`, for the
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
rotates while you're active and pauses while you're away.

To drive it by hand, from the checkout:

```bash
./bin/artwall              # set the wallpaper once
./bin/artwall --preview    # open a captioned painting without changing the wallpaper
./bin/artwall --find monet # look up Wikidata QIDs for the config (see below)
```

State lives under `~/.cache/artwall/`; deleting it is a safe full reset.

## Configuration

Everything works out of the box (all paintings). To narrow it, drop a TOML file
at `~/.config/artwall/config.toml` (honours `$XDG_CONFIG_HOME`). Every key is
optional and overrides the built-in default:

```toml
date_begin = 1850          # inception-year window (negative = BC)
date_end = 1900
movements = ["Q40415"]     # Impressionism
genres = ["Q191163"]       # landscape art
artists = ["Q296"]         # Claude Monet
collections = ["Q190804"]  # Rijksmuseum
language = "en"            # caption / label language
font_size = 22             # caption point size
min_interval = 1800        # --throttle interval, in seconds
```

Within a knob the values are OR'd (`Monet or Van Gogh`); across knobs they're
AND'd (Impressionist *and* a landscape). Copy
[`config.example.toml`](config.example.toml) as a starting point. A typo'd key
fails loudly rather than being silently ignored. Changing a filter transparently
refetches the catalogue (it's cached per filter-set).

### Choosing filters

The four filters reference Wikidata items by QID. Browse the options on
Wikipedia, then turn the name you picked into a QID with `--find`:

- **movements** — [list of art movements](https://en.wikipedia.org/wiki/List_of_art_movements)
  (e.g. [Impressionism](https://en.wikipedia.org/wiki/Impressionism) = `Q40415`)
- **genres** — open-ended, with no single list page; common ones are
  [portrait](https://en.wikipedia.org/wiki/Portrait_painting),
  [landscape](https://en.wikipedia.org/wiki/Landscape_painting) (`Q191163`),
  [still life](https://en.wikipedia.org/wiki/Still_life) (`Q170571`),
  [history painting](https://en.wikipedia.org/wiki/History_painting),
  [genre scenes](https://en.wikipedia.org/wiki/Genre_art), marine, nude,
  vanitas, … — `--find` any genre name
- **artists** — any painter ([list of painters](https://en.wikipedia.org/wiki/List_of_painters_by_name),
  e.g. [Claude Monet](https://en.wikipedia.org/wiki/Claude_Monet) = `Q296`)
- **collections** — any museum ([list of art museums](https://en.wikipedia.org/wiki/List_of_art_museums))

Wikipedia pages don't show QIDs, so once you've picked a name, look it up without
leaving the terminal:

```console
$ ./bin/artwall --find impressionism
Q40415   Impressionism — 19th-century art movement
Q1475680 impressionism — movement in literature
...
```

Copy the matching QID into the config. (A QID is also the last part of a
[wikidata.org](https://www.wikidata.org/) item URL, reachable from any Wikipedia
article via **Tools → Wikidata item**.)

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
`wikidata.py` (SPARQL queries + result parsing), `selection.py` and
`commands.py`, the HTTP client in `web.py`, orchestration in `app.py`. Tests
exercise the HTTP layer against a real loopback `http.server` (a fake Wikidata),
and drive `run()` with a seeded `random.Random` and a recording runner that
captures the `swaymsg` argv instead of launching it. See `CLAUDE.md` for the
full module breakdown.
