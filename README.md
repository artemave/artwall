# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
[Wikidata](https://www.wikidata.org/). The caption (artist, title, date) is shown
by default as an interactive overlay with a clickable link to the painting's
Wikipedia page.




https://github.com/user-attachments/assets/975cf82c-e2e9-4ad9-9cdf-5cd3741c5002





## Requirements

- Python 3.11+
- Sway
- PyGObject + gtk-layer-shell (GTK 3) - only for the default `interactive` caption overlay

Install the external tools (you already have Sway and Python). PyGObject and GTK 3
are usually present on a desktop install - the commands list them anyway, so the
ones you actually tend to be missing are **ImageMagick** and **gtk-layer-shell**:

```bash
sudo dnf install ImageMagick gtk-layer-shell python3-gobject       # Fedora
sudo apt install imagemagick gir1.2-gtklayershell-0.1 python3-gi   # Debian/Ubuntu
sudo pacman -S imagemagick gtk-layer-shell python-gobject          # Arch
```

## Usage

Run it from this checkout - there's nothing to install. Add to your Sway config
(`~/.config/sway/config`), pointing at where you cloned it, to set a wallpaper at
startup and re-roll on window focus (throttled to once every 30 min):

```
exec /path/to/artwall/bin/artwall
exec swaymsg -t subscribe -m '["window"]' | while read -r _; do /path/to/artwall/bin/artwall --throttle; done
# re-roll on monitor hotplug too, so a newly-connected screen gets a wallpaper:
exec swaymsg -t subscribe -m '["output"]' | while read -r _; do /path/to/artwall/bin/artwall --throttle --min-interval 5; done
# only for the default "interactive" caption mode - the interactive caption overlay:
exec_always /path/to/artwall/bin/artwall-overlay
```

`--throttle` makes a frequent trigger a no-op until `Config.min_interval`
seconds (default 30 min) have passed since the last change, so the wallpaper
rotates while you're active and pauses while you're away. `--min-interval`
overrides that interval: the output subscription uses a short 5 s so a single
hotplug (which fires several output events) re-rolls just once, while window
events keep the long interval. Any run sets a painting on *every* connected
display, so a hotplug-triggered run also gives the new screen one.

To drive it by hand, from the checkout:

```bash
./bin/artwall              # set the wallpaper once
./bin/artwall --preview    # open a captioned painting without changing the wallpaper
./bin/artwall --find monet # look up Wikidata QIDs for the config (see below)
```

State lives under `~/.cache/artwall/`; deleting it is a safe full reset.

## Configuration

Out of the box it draws from a curated set of clean-scan museums (see
[Default collections](#default-collections)). To change that, drop a TOML file at
`~/.config/artwall/config.toml` (honours `$XDG_CONFIG_HOME`). Every key is optional
and overrides the built-in default:

```toml
date_begin = 1850          # inception-year window (negative = BC)
date_end = 1900
movements = ["Q40415"]     # Impressionism
genres = ["Q191163"]       # landscape art
artists = ["Q296"]         # Claude Monet
collections = ["Q190804"]  # override the default set (or [] for all ~400k paintings)
language = "en"            # caption / label language
font_size = 11             # caption point size; omit to use the system font size
caption_corner = "bottom-right"  # top-left / top-right / bottom-left / bottom-right
caption_pad_x = 24         # caption inset from the side edge, in pixels
caption_pad_y = 64         # caption inset from the top/bottom edge, in pixels
caption_mode = "interactive"  # "interactive" = overlay; "text" = burned into the wallpaper
min_interval = 1800        # --throttle interval, in seconds
```

Within a knob the values are OR'd (`Monet or Van Gogh`); across knobs they're
AND'd (Impressionist *and* a landscape). Copy
[`config.example.toml`](config.example.toml) as a starting point. Changing a filter transparently
refetches the catalogue (it's cached per filter-set).

> **Heads-up on the catalogue fetch.** The catalogue comes from the Wikidata
> Query Service (WDQS), which **rate-limits aggressively**. So the *first* run after you change a filter can fail or hang for a bit -
> especially if you're iterating on filters quickly (each change is a fresh
> query). This is transient: just run it again in a minute. Once a filter-set's
> catalogue is cached it isn't queried again for ~30 days, and every per-painting
> fetch goes to the stable Action API - so day-to-day rotation never touches WDQS.

### Default collections

By default artwall draws from a curated set of large, open-access museums chosen
for **clean, frameless scans** - so the wallpaper is the artwork itself, not a
photo of a framed painting on a gallery wall: the Rijksmuseum, Nationalmuseum
(Sweden), SMK (Denmark), National Gallery of Art (Washington), Art Institute of
Chicago, the Getty, the Cleveland Museum of Art, and the Museum of Fine Arts,
Boston.

To draw from **all ~400k paintings** instead (more variety, but you'll get the
occasional framed-on-the-wall photo), set `collections = []`. To use *different*
museums, list their QIDs (find them with `--find`).

The catalogue for the default set ships **pre-fetched** with artwall, so the very
first run works without touching WDQS at all - handy since it's often rate-limited
right when you log in. (If you change `collections`, that new set is fetched on
first use, per the note above.) Maintainers regenerate the shipped catalogue with
`make catalogue` when the default set changes.

### Caption modes

`caption_mode` chooses how the caption is shown:

- **`interactive`** (default) - an **interactive overlay**: a small, persistent
  widget (`bin/artwall-overlay`, launched from your Sway config) that shows the
  caption as a clickable link to the painting's Wikipedia article (falling back to
  its Wikidata page), followed by a **refresh button** that re-rolls the wallpaper
  on just that display; nothing is burned into the wallpaper. Because it's a
  Wayland layer-shell surface sitting *just above the wallpaper*, it's visible
  and clickable wherever the desktop is exposed. It needs PyGObject +
  gtk-layer-shell, and it must be running - add the `exec` line from
  [Usage](#usage). It updates automatically on each rotation.
- **`text`** - the caption is **burned into the wallpaper** in the chosen corner
  using the system font (scaled per display). No overlay, no extra dependencies,
  nothing to launch - but not clickable.

`--preview` always burns the caption in, regardless of mode, since it's a single
self-contained image.

### Choosing filters

The four filters reference Wikidata items by QID. Browse the options on
Wikipedia, then turn the name you picked into a QID with `--find`:

- **movements** - [list of art movements](https://en.wikipedia.org/wiki/List_of_art_movements)
  (e.g. [Impressionism](https://en.wikipedia.org/wiki/Impressionism) = `Q40415`)
- **genres** - open-ended, with no single list page; common ones are
  [portrait](https://en.wikipedia.org/wiki/Portrait_painting),
  [landscape](https://en.wikipedia.org/wiki/Landscape_painting) (`Q191163`),
  [still life](https://en.wikipedia.org/wiki/Still_life) (`Q170571`),
  [history painting](https://en.wikipedia.org/wiki/History_painting),
  [genre scenes](https://en.wikipedia.org/wiki/Genre_art), marine, nude,
  vanitas, … - `--find` any genre name
- **artists** - any painter ([list of painters](https://en.wikipedia.org/wiki/List_of_painters_by_name),
  e.g. [Claude Monet](https://en.wikipedia.org/wiki/Claude_Monet) = `Q296`)
- **collections** - any museum ([list of art museums](https://en.wikipedia.org/wiki/List_of_art_museums))

Wikipedia pages don't show QIDs, so once you've picked a name, look it up without
leaving the terminal:

```console
$ ./bin/artwall --find impressionism
Q40415   Impressionism - 19th-century art movement
Q1475680 impressionism - movement in literature
...
```

Copy the matching QID into the config. (A QID is also the last part of a
[wikidata.org](https://www.wikidata.org/) item URL, reachable from any Wikipedia
article via **Tools → Wikidata item**.)

## Development

Run it with `./bin/artwall` (see [Usage](#usage)); there's nothing to install.

Tests use the standard-library `unittest` runner - no mocks:

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
