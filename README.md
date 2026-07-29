# artwall

Rotate your [Sway](https://swaywm.org/) wallpaper through random paintings from
[Wikidata](https://www.wikidata.org/). The caption (artist, title, date) is shown
by default as an interactive overlay with a clickable link to the painting's
Wikipedia page — and a **star** button that keeps the ones you like.



https://github.com/user-attachments/assets/2f3c4325-da6f-43f9-b121-dbcc80158574





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
exec 'while :; do swaymsg -t subscribe -m "[\"window\"]" | while read -r _; do /path/to/artwall/bin/artwall --throttle; done; sleep 1; done'
# re-roll on monitor hotplug too, so a newly-connected screen gets a wallpaper:
exec 'while :; do swaymsg -t subscribe -m "[\"output\"]" | while read -r _; do /path/to/artwall/bin/artwall --throttle --min-interval 5; done; sleep 1; done'
# only for the default "interactive" caption mode - the interactive caption overlay:
exec_always /path/to/artwall/bin/artwall-overlay
```

> The overlay also hosts your [gallery](#starring-paintings) and keeps the
> [published copy](#publishing-the-gallery) in step, both on by default. Opt out
> with `--no-serve-stars` / `--no-publish-stars`.

> The subscription lines must be **single-quoted as a whole**. Sway's config
> parser splits an `exec` line on `;`, so an unquoted `… | while read …; do …; done`
> is rejected at startup (`Unknown/invalid command 'do'`) and rotation silently
> never starts. Wrapping the pipeline in `'…'` hands it to one `sh -c` intact (the
> inner `"[\"window\"]"` is the event-type JSON with its quotes escaped). The
> `while :; … sleep 1; done` supervisor resubscribes if `swaymsg` ever exits. Note
> these are `exec`, not `exec_always`, so editing them needs a fresh Sway session
> to take effect — `swaymsg reload` does not re-run `exec`.

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

State lives under `~/.cache/artwall/`; deleting it is a safe full reset. Your
[starred paintings](#starring-paintings) are the one thing kept outside it, in
`~/.local/share/artwall/`.

## Starring paintings

In the default `interactive` caption mode, each caption has a **★ button**.
Click it and the painting is added to your gallery. The **gallery button** beside
it opens the collection.

The overlay hosts that gallery itself, for as long as your session lasts, on a
port the OS picks. There is nothing to start and nothing to remember to stop —
it's just always there, behind the button.

You get a masonry of every painting you've kept, newest first. Click a painting
to open it full size; click its title to read the Wikipedia article in a new tab.
Each has a **★ in its corner that removes it** from the gallery, and an **Undo**
appears when you do.

That unstar button is why the gallery is a little loopback web server rather than
a file the button opens — a page loaded from `file://` can't delete anything. It
binds `127.0.0.1`, serves only your archived paintings, and needs no JavaScript.

**There is exactly one gallery, structurally.** The overlay is the only thing that
serves one, and it already replaces any previous instance of itself on launch, so
two servers answering two ports is not a state this can reach. (It used to be:
there was a standalone `artwall --stars` you could run twice, and a PID file and a
SIGTERM dance to stop you. Folding the gallery into the daemon deleted all of it.)

With `--no-serve-stars` nothing listens on a port, and the gallery button opens
the archived, read-only `stars.html` instead — the same page without the buttons.

### Adding a painting you found yourself

The wallpaper only ever offers you one painting at a time, but reading about an
artist usually turns up others. The gallery has a **paste box** for those: open
the painting's image on Wikipedia, copy the link, paste it in, and it's hung
alongside the rest.

Both link shapes work — the one you get from clicking an image in an article:

```
https://en.wikipedia.org/wiki/Muqi#/media/File:Mu-ch'i_001.jpg
```

and the file page itself, on Wikipedia or Commons:

```
https://en.wikipedia.org/wiki/File:Bertholet_Fl%C3%A9mal_-_Heliodorus_Driven_from_the_Temple.jpg
```

The link identifies an *image*, so artwall asks Wikimedia Commons which artwork
that scan reproduces, and takes the artist, title and date from Wikidata — the
same place the wallpaper's caption comes from. A pasted painting is therefore
indistinguishable from one you starred off the wallpaper: same record, same
archived image, same trash.

Pasting a painting that's already hung says so and changes nothing (it's an
*add* box, not a toggle); pasting one that's in the trash restores it.

A link can be turned down for four reasons, each said plainly on the page:

| | |
|---|---|
| the link names no image | you copied the article link, not the image's |
| not on Wikimedia Commons | in-copyright art is hosted on Wikipedia itself, and can't be archived |
| not linked to a painting on Wikidata | a scan nobody has connected to its artwork yet |
| linked to something that isn't a painting | e.g. a photo of the artist, or a motif rather than a specific work |

That last check is deliberate: artwall collects paintings (`instance of:
painting`), so prints, drawings and photographs are declined even when the link
resolves perfectly.

### The trash

**Unstarring never deletes anything.** The painting moves to the trash, keeping
the position it held. The Undo offer is a flash — it appears once, right after
the removal, and a reload clears it — but the painting stays recoverable long
after that: the gallery header links to **Trash (n paintings)**, where each one
has a Restore button that puts it back exactly where it was, original bytes and
all.

The trash survives reboots. The only irreversible act in artwall is the
**Delete n paintings forever** button on the trash page.

> The overlay's ★ works differently: it's a toggle, not a delete. Clicking it a
> second time unstars the painting outright (clicking again re-downloads it).
> Only the gallery's ★ uses the trash.

Everything lives together in `~/.local/share/artwall/`:

```
~/.local/share/artwall/
├── stars.json        the list: artist, title, date, link
├── stars.html        the gallery, button-less, for browsing a backup
├── images/
│   ├── Q20192051.jpg the paintings themselves, archived at 2560px
│   └── …
├── public/           the publishable static site (--publish-stars)
└── .trash/           unstarred paintings, restorable until you empty it
    ├── trash.json    each one's record and the place it held
    └── Q17324036.jpg
```

Note that `.trash/` is backed up along with everything else, and full-size
paintings are several megabytes each. Empty it when you're sure.

Starring **downloads the painting** (not just a link to it) and the gallery
references those files by *relative* path, so the directory is self-contained:
back it up, sync it, or copy it to another machine, and `stars.html` still opens
in any browser — offline, with the images intact. It sits outside `~/.cache/`
precisely so that wiping the cache can't take your collection with it.

That archived `stars.html` is rewritten when the overlay starts and on every
unstar, restore or delete. It's the same gallery *without* the buttons or the
trash link, since a page opened from `file://` has no server to post them to.

Set `stars_image_width` in the config to archive at a different size (default
`2560`; Commons originals can run past 100 MB, which is why it's capped).

### Publishing the gallery

`stars.html` is for *you* — it lives next to `stars.json` and `.trash/`, so you
can't upload the directory without publishing the record of every painting you
ever removed. So the overlay maintains a separate copy that you *can* upload:

```
~/.local/share/artwall/public/
├── index.html        the gallery, read-only: a list, nothing that acts on it
├── thumbs/           web-sized copies — what the page actually loads
│   └── Q20192051.jpg
└── images/           the full-size archives each painting links to
    └── Q20192051.jpg
```

That's a self-contained static site — no server side, no JavaScript, every path
relative, so it works from a bare static host, a GitHub Pages repo, or a
subdirectory of one. Copy the directory up and you're done.

The split matters on a phone: a page of 2560px museum scans is tens of megabytes,
so the grid loads the thumbnails (`public_image_width`, default `1200` — enough
for a phone's single column at 3x) and only a tap pulls the full scan. The page
itself is responsive, dark-mode aware and has no hover-only affordances.

It's rebuilt when the overlay starts and after **every** way a painting can enter
or leave the collection — starring one from a caption, and unstarring, restoring
or pasting one in the gallery. Only what changed is rebuilt, and a painting you
unstarred is *removed* from the site, since otherwise its image would go on being
served at its own URL long after it stopped appearing on the page.

Two threads can want to publish at once (a gallery request and a star), so builds
are serialised; one would otherwise delete a painting the other had just written.

Set `public_url` in the config to wherever you upload it and your own gallery —
both the served page and the archived `stars.html` — gets a link to the live site
beside the heading, so the address is somewhere you'll find it. It's purely a note
to yourself: nothing here uploads anything, and the value is never fetched or
checked. The published page itself never shows it, since it *is* that address.

If you never publish anything, `--no-publish-stars` skips all of it — worth doing,
since `public/` holds a second copy of every full-size archive.

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
stars_image_width = 2560   # width to archive a starred painting at
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
  its Wikidata page), followed by a **★ button** that adds the painting to your
  [gallery](#starring-paintings), a **gallery button** that opens the whole
  collection, and a **refresh button** that re-rolls the
  wallpaper on just that display; nothing is burned into the wallpaper. Because
  it's a Wayland layer-shell surface sitting *just above the wallpaper*, it's
  visible and clickable wherever the desktop is exposed. It needs PyGObject +
  gtk-layer-shell, and it must be running - add the `exec` line from
  [Usage](#usage). It updates automatically on each rotation.
- **`text`** - the caption is **burned into the wallpaper** in the chosen corner
  using the system font (scaled per display). No overlay, no extra dependencies,
  nothing to launch - but not clickable, and with nothing to click there's no way
  to star a painting.

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
