# artwall

Rotate your [Sway](https://swaywm.org/) or [KDE Plasma](https://kde.org/plasma-desktop/)
wallpaper through random paintings from [Wikidata](https://www.wikidata.org/),
with a clickable caption and a gallery of the ones you star.



https://github.com/user-attachments/assets/2f3c4325-da6f-43f9-b121-dbcc80158574



## Install

Needs Python 3.11+, Sway or KDE Plasma 6 (Wayland), ImageMagick, PyGObject and
gtk-layer-shell:

```bash
sudo dnf install ImageMagick gtk-layer-shell python3-gobject       # Fedora
sudo apt install imagemagick gir1.2-gtklayershell-0.1 python3-gi   # Debian/Ubuntu
sudo pacman -S imagemagick gtk-layer-shell python-gobject          # Arch
```

Clone the repo and start `bin/artwall` with your session. Nothing to install.

**Sway** — in `~/.config/sway/config`:

```
exec_always /path/to/artwall/bin/artwall
```

**KDE Plasma** — `~/.config/autostart/artwall.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=artwall
Exec=/path/to/artwall/bin/artwall
```

## Features

- **Rotation** — a new painting at login, every 30 minutes (`min_interval`), and
  when a monitor is plugged in. Each display gets its own, shown whole on a
  background blended from its colours.
- **Caption** — artist, title and date in a corner of each display. Click it for
  the Wikipedia article; **↻** re-rolls that display; **★** stars the painting;
  the grid button opens the gallery.
- **Gallery** — every starred painting, archived locally in
  `~/.local/share/artwall/`. Unstarring moves a painting to the trash, where you
  can restore it or delete it for good.
- **Add your own** — paste a Wikipedia or Commons image link into the gallery to
  add any painting you come across.
- **Publishing** — `~/.local/share/artwall/public/` is a static copy of the
  gallery, ready to upload anywhere. If `~/.local/share/artwall` is a git repo, a
  **⇪ Sync** button commits and pushes it; [`template/`](template/) sets that up
  to deploy to GitHub Pages.

Opt out of the gallery server or the published copy with `--no-serve-stars` /
`--no-publish-stars`.

## Commands

```bash
bin/artwall                 # run for the session (see Install)
bin/artwall --once          # set a new wallpaper and exit
bin/artwall --preview       # open a random painting without changing the wallpaper
bin/artwall --find monet    # look up Wikidata QIDs for the config
```

## Configuration

Every key in `~/.config/artwall/config.toml` is optional
(see [`config.example.toml`](config.example.toml)):

```toml
date_begin = 1850          # inception-year window (negative = BC)
date_end = 1900
movements = ["Q40415"]     # Impressionism
genres = ["Q191163"]       # landscape art
artists = ["Q296"]         # Claude Monet
collections = ["Q190804"]  # museums; [] for all ~400k paintings
language = "en"            # caption language
font_size = 11             # caption size; default is the desktop's UI font size
caption_corner = "bottom-right"
caption_pad_x = 24         # caption inset, in pixels
caption_pad_y = 64
min_interval = 1800        # seconds between paintings
owner = "Alex"             # gallery heading: "★ Alex starred 12 paintings"
public_url = "https://…"   # link to your published gallery
```

Values within a filter are OR'd, filters are AND'd. The default `collections` is
a set of museums with clean, frameless scans. Find QIDs with `--find`:

```console
$ bin/artwall --find impressionism
Q40415   Impressionism - 19th-century art movement
```

The first run after changing a filter queries the Wikidata Query Service, which
is often rate-limited — if it fails, try again in a minute.

## Development

```bash
make install-dev   # ruff, mypy, coverage, GTK stubs
make check         # lint + typecheck + tests (100% coverage gate)
```

See [`CLAUDE.md`](CLAUDE.md) for the architecture.
