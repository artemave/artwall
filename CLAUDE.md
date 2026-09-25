# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standard-library-only Python tool that sets a random painting from
[Wikidata](https://www.wikidata.org/) (every `instance of: painting` that has an
image — ~400k) as the **Sway** or **KDE Plasma** (Wayland) desktop wallpaper. The painting is composed (via
ImageMagick) onto a display-sized canvas so it's shown *whole* (no cropping); the
letterbox margins are filled with a soft gradient sampled from the painting's own
colours. The caption (artist/title/date) is never drawn into the wallpaper: the daemon
(what bare `artwall` runs — see below) shows it with a clickable Wikipedia link, a ★ button that adds the painting to a **starred
gallery**, and a refresh button that re-rolls that one display. The default
`collections` is a curated set of clean-scan, open-access museums
(`DEFAULT_COLLECTIONS` — Rijksmuseum, Cleveland, …) so the wallpaper is the
artwork, not a framed-on-the-wall photo; its catalogue **ships pre-fetched**
(`artwall/catalogue/`, regenerate with `make catalogue`) so the first run skips
the rate-limited WDQS. `collections = []` draws from *all* ~400k paintings. A TOML
file at `~/.config/artwall/config.toml` narrows it via a date window + QID filters
(`movements`/`genres`/`artists`/`collections`) and sets
`language`/`font_size`. Bare `artwall` is the **daemon**, launched with the
session; everything else is a **oneshot** — `--once` sets the wallpaper once and
exits. The daemon drives rotation: it runs `artwall --once --throttle` at
startup and every minute (`--throttle` turns all but one run per
`Config.min_interval` into a no-op), and `--once --throttle --min-interval 5` on
monitor hotplug. Its children inherit the session's environment (`SWAYSOCK` on
Sway) — no systemd, no env import. The daemon draws the caption in GTK's UI
font; `font_size` overrides the size. The **core oneshot has no third-party Python
dependencies — keep it that way** (use `urllib`, not `requests`); external CLI
tools (`swaymsg`, `kscreen-doctor`, `gdbus`, `magick`) are fine since we already
shell out. The one exception is `artwall/daemon.py`, which needs PyGObject +
gtk-layer-shell — it's the lone GUI/daemon component, imported by `__main__` only
when no one-shot action was asked for, and quarantined there (omitted from coverage; typed against GTK3 PyGObject-stubs).

## Commands

```bash
python3 -m unittest discover -s tests   # run all tests
python3 -m unittest tests.test_app      # run one module
python3 -m unittest tests.test_app.RunTests.test_happy_path_sets_wallpaper  # one test
make install-dev                        # dev tooling: ruff, mypy, coverage, GTK3 stubs
make catalogue                          # regenerate the shipped first-run catalogue (hits WDQS)
make check                              # all checks: lint + typecheck + 100%-coverage-gated tests
make lint / make typecheck / make test / make coverage  # individual targets (configs: ruff.toml, mypy.ini, .coveragerc)
python3 -m artwall                       # the daemon: captions + rotation (needs PyGObject + gtk-layer-shell). Also
                                         # hosts the starred gallery and maintains data_dir/public/ — both on by
                                         # default; opt out with --no-serve-stars / --no-publish-stars
python3 -m artwall --once                # set the wallpaper once (hits network + swaymsg + magick)
python3 -m artwall --once --throttle     # set once, but no-op if changed < Config.min_interval ago (the daemon's timer)
python3 -m artwall --once --throttle --min-interval 5  # throttle with a 5s window (the daemon's hotplug re-roll)
python3 -m artwall --find impressionism  # look up Wikidata QIDs for the config filters
python3 -m artwall --output DP-1          # re-roll only one display (the caption's refresh button)
python3 -m artwall --star DP-1            # star/unstar that display's painting (the caption's ★ button)
```

## Architecture

The entry point is intentionally thin; all logic lives in importable modules so
it can be tested without network or `swaymsg`.

- `artwall/config.py` — `Config` dataclass holding cache paths, the Wikidata
  endpoints (`sparql_url` for WDQS, `api_url` for the Action API,
  `commons_api_url` for Commons' own Action API, `commons_url`
  for images), `ids_ttl`, the content knobs
  (`date_begin`/`date_end`, `language`, `artists`/`movements`/`genres`/
  `collections` QID lists, `font_size`, `stars_image_width`) and
  `min_interval`.
  `collections` defaults to `DEFAULT_COLLECTIONS` (curated clean-scan museums).
  `caption_file(name)` is where `run()` writes a display's painting record for the
  daemon (`caption-<name>.json`). **Two roots on purpose:** `cache_dir`
  (`~/.cache/artwall`) is disposable — deleting it is a documented safe reset — so
  the stars live under `data_dir` (`~/.local/share/artwall`) instead:
  `stars_file` (`stars.json`), `star_image(key)` (`images/<key>.jpg`) and the
  generated `stars_page` (`stars.html`), which sits *with* the images rather than
  in the cache because it links to them by relative path, keeping the directory
  portable and backup-able; `trash_dir`/`trash_file`/`trash_image(qid)` hold
  unstarred paintings until the trash is emptied. `public_dir` (+ `public_page`,
  `public_image_dir`/`public_image(key)`, `public_thumb_dir`/`public_thumb(key)`,
  `public_image_width`, `public_url`) is the published-site export: **a subdirectory rather than
  `data_dir` itself**, because uploading `data_dir` would publish `stars.json`
  and the whole trash — the record of every painting you ever removed.
  `public_url` is inert: never fetched, never checked, it only puts a link to the
  live site in `render_page()` — *both* its renderings, since the served gallery
  and the archived `stars.html` are alike pages where you look at your own
  collection. `render_public()` deliberately omits it: that page is that address.
  `owner` is likewise cosmetic: it only reaches `stars._title()`, which names it
  beside the ★ ("★ Alex starred 12 paintings" instead of "★ 12 starred
  paintings"). All three renderings agree, since all three call `_title()`.
  The field defaults
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
  title/date (`parse_entity`) and read a `label`; parse the entity search
  (`parse_search`); resolve a pasted Wikipedia image link (`parse_file_link` →
  the `File:…` title, taken from the media-viewer **fragment** in preference to
  the path, because a viewer link's path is the *article* — usually the artist —
  and only the fragment names the image that was clicked; `parse_file_pageid`;
  `parse_artwork_qid`; `is_painting`; and, when structured data names no artwork,
  read the file's `{{Artwork}}` wikitext instead — `parse_artwork_template`, plus
  `file_page_url` for the Commons description page that becomes its link); build
  the sized Commons image URL from a
  filename
  (`image_url` → `Special:FilePath/<file>?width=`); read an image's native pixel
  size from a Commons `imageinfo` response (`parse_image_size`) and check whether
  that size can fill a display without `commands.compose_command`'s
  aspect-preserving `-resize` enlarging — and blurring — it (`fits`: its scale
  factor is `min` of the two side ratios, so it only exceeds 1, upscaling, when
  the image is smaller than the display on *both* sides — matching or exceeding
  it on just one is enough); pull the Wikipedia article
  URL from a `sitelinks/urls` response (`parse_sitelink`) and build the
  always-present Wikidata page URL (`entity_url`, the article fallback). Prefer
  adding source logic here.
  **Dates carry a precision, and it matters:** Wikidata stores a coarse date as a
  *year* plus a precision code — the 13th century is `+1300` with precision 7 — so
  reading the year alone invents an exactness the record never claimed (a work
  dated circa 1250 captioned "1300"; this was a real bug). `_date()` honours
  precision **only when the stored year agrees with it** (a century ending `00`, a
  decade ending `0`) → "13th century", "1860s". Roughly half the catalogue's
  century-tagged works actually hold a specific year (`+1732`); that's a mis-entry
  rather than a claim about the century, so the year is kept. A `circa` qualifier
  (P1480, on the *statement* — which is why `_statement()` exists beside `_claim()`)
  prefixes "c.". A rounded coarse date is never marked circa: "13th century"
  already says approximate.
  **Two services on purpose:** WDQS (`sparql_url`) is outage-prone, so it's
  used *only* for the monthly catalogue; every per-painting fetch goes to the
  stable Action API.
  **A third endpoint for pasted links:** `commons_api_url` is Commons' *own*
  Action API, distinct from Wikidata's `api_url`. A pasted link names a file, and
  only Commons knows which artwork that file shows — its structured data lives on
  `M<pageid>` entities under `statements` (not `claims`).
  `parse_artwork_qid` tries `DEPICTED_ARTWORK` in order: P6243 ("digital
  representation of") is exact, P180 ("depicts") is loose but is **all many real
  scans carry** — the Muqi handscroll this was built for has only P180 — so it
  can't be skipped. What makes leaning on P180 safe is `is_painting()`: P180 on a
  photograph points at whatever is in frame, so the resolved entity is checked for
  `P31 = Q3305213` before anything is starred (this is what turns down a portrait
  photo of the artist, or a motif item like "Red Fuji", which is an *artistic
  theme* rather than a specific work).
  **And a wikitext fallback under that.** Plenty of Commons scans describe their
  painting *only* in the `{{Artwork}}` template on the file page — artist, title,
  date, `object type = painting` — while their structured data holds nothing but
  MIME type and pixel dimensions, and the template's own `|wikidata =` field sits
  empty. Often there is no Wikidata item to point at in the first place (the
  Metcalf this was built for has none). So when `parse_artwork_qid` finds nothing,
  `parse_artwork_template` reads the wikitext instead. It is a strictly worse
  source and is only ever tried second: free text, no QIDs, and `object type`
  standing in for the `P31 = Q3305213` guard — weaker, but it is the only claim
  the file makes about what it shows, and refusing to read it rejects every
  correctly-described painting on this path. Parsing tracks `{{}}` and `[[]]`
  depth rather than splitting on `|`, because the fields nest
  (`{{title|en=…|de=…}}`, `[[Foo|Bar]]`). A *template* date
  (`{{other date|circa|1911}}`) reduces to "" — better an uncaptioned year than a
  wrong one.
- `artwall/selection.py` — **pure** `caption` formatting (artist/title/date) and
  `record()`, the painting dict `run()` writes as `caption-<name>.json` and the
  daemon copies verbatim into the star list (key/artist/title/date/image/url —
  everything needed to caption, link and archive a painting without a refetch).
  **`key` is the painting's identity and it is namespaced**, because paintings
  arrive from two places: `Q<n>` for a Wikidata item (every wallpaper, and any
  pasted link whose file names its artwork in structured data), `M<n>` for a
  Commons page whose artwork lives only in wikitext. The two numbering spaces
  overlap, so a bare int would silently collide — one key naming two different
  paintings, and the gallery unstarring the wrong one. The prefix is what the
  filenames (`images/<key>.jpg`) and every route (`[QM]\d+`) are built on.
- `artwall/stars.py` — the starred gallery, its trash, and the server for both.
  **Pure** `toggle()`/`is_starred()` (matched on key), `render_page(stars,
  interactive, flash, trash_count)` and `render_trash(trashed, flash)` — each one
  self-contained HTML. `star(output)` reads the caption record, toggles it, and
  downloads the painting into `star_image(key)` — writing the archive *before*
  saving the list, so a failed download never leaves a star pointing at a missing
  image. `write_page()` writes the archived `stars.html`; `start_gallery()` binds the
  server the daemon hosts.
  `star_link(config, link)` is the gallery's paste box: it hands the URL to
  `app.resolve_link()` and archives the result. Deliberately **not** a toggle —
  you paste to *add*, so a link you've already hung reports `"already"` and
  changes nothing rather than silently unstarring it. A painting sitting in the
  trash is `restore()`d (`"restored"`) instead of re-downloaded: adding it afresh
  would leave the trash holding the same QID, and restoring *that* later would
  hang a second copy of the same painting.
  **Why a server:** the caption's ★ can only unstar the painting *currently* on a
  display, so the gallery must be able to remove an older one — and a `file://`
  page cannot delete a file. So the gallery renders over a loopback `http.server`
  (`GalleryServer`, bound to port 0) and takes every mutation as a plain form POST
  + 303 — no JavaScript. `_Gallery` answers exactly `/`, `/trash`,
  `/images/<key>.jpg`, `/trash/images/<key>.jpg`, `POST /unstar/<key>`,
  `POST /restore/<key>`, `POST /star`, `POST /trash/empty` and `POST /sync`;
  everything else 404s. `POST /star` is the one route that reads a **request body** (the pasted
  link, form-urlencoded) — every other mutation carries its QID in the path. A
  link that won't resolve comes back as a `Flash`, not an error status: it's
  typed input, so a typo must not replace the gallery with a browser error page.
  **`start_gallery()` is the only way a gallery starts**, and the daemon is its
  only caller — it hosts one on a background thread for its whole lifetime.
  `republish=True` (the default; the daemon's `--publish-stars`) rebuilds the
  published site after every mutation, and once at startup so the first thing
  served isn't a page left over from a collection that has since changed. The
  `runner` is threaded through to `_Gallery` for it — publishing shells out to
  `magick`, so without an injectable runner the served-gallery tests would really
  invoke ImageMagick on a fake JPEG.
  **There is no "replace the previous gallery" step and no PID file.** There used
  to be, because a standalone `artwall --stars` could be run twice and leave two
  servers answering two ports; `stop_previous()`/`is_gallery()`/`_cmdline()`/
  `config.stars_pid` all existed for that and are gone. One gallery is now
  *structural*: only the daemon serves one, and `supersede_running_instances()`
  already guarantees a single daemon. This is the shape to keep — a state made
  unreachable beats a mechanism that detects it.
  **`render_public(stars)` + `publish()` are the third rendering — the one for the
  open internet** (maintained by the daemon's `--publish-stars`, on by default).
  What makes it public isn't the missing buttons
  (`stars.html` has none either), it's *where* and *what*: a self-contained
  `public/` holding only `index.html` + `thumbs/` + `images/`, so what you upload
  can't include `stars.json` or `.trash/`; and a grid that loads web-sized copies
  (`commands.thumbnail_command`, `{size}x{size}>` so it only ever *shrinks*) while
  each tile links to the full archive — a page of 2560px scans is tens of megabytes,
  which is the difference between usable and not on a phone. `_tile`'s `link` arg is
  what splits `<img src>` from `<a href>` for this. It's a **build**: `_stale()`
  skips what's current (else every re-publish is a `magick` per painting) and
  `_prune()` deletes exports whose painting is no longer starred, because a file
  left behind goes on being served at its own URL. `index.html`, not `stars.html`,
  so a static host answers the bare directory URL.
  **The shared `CSS` is mobile-first-ish and the hover rules are gated behind
  `@media (hover: hover)`**: a tap leaves `:hover` stuck on whatever was tapped, so
  any hover-*reveal* (the corner button's opacity, the trash's dimming) would stay
  revealed on one painting for the whole visit. That gate is also where the corner
  button shrinks — it rests at 2.75rem (a finger) and only a pointer device gets the
  2rem version. `-webkit-text-size-adjust: 100%` stops iOS inflating the caption of
  every narrow tile past the heading's size.
  **The heading/flash buttons (★ Add, ⇪ Sync, Undo, Delete forever) are outlines,
  not filled blocks**, so the paintings stay the only solid thing on the page; they
  fill in only on hover, gated in the same `@media (hover: hover)` block as the
  corner button, for the same reason. `--danger` is a `:root` token like `--bg`/
  `--fg`, with its own dark-mode value — the light-mode red is close to invisible
  on a near-black background, and reaching for a brighter one instead would make
  "Delete forever" the one loud thing on an otherwise quiet page.
  **`is_git_repo()` + `sync()` are `public/`'s upload step**, for someone who
  won't use a terminal: `is_git_repo()` is just `(data_dir / ".git").exists()` —
  it detects a repo, never creates or configures one — and turns on the served
  page's **⇪ Sync** button (`git_sync` on `render_page()`, archived page never
  gets it, same reasoning as the trash link). `sync()` is `git add -A`, a commit
  *only* if `git diff --cached --quiet` says something is staged (skips
  "nothing to commit" failing the whole thing), then an unconditional push — a
  no-op push still catches an earlier sync's commit that reached this far but
  not the remote. It relies on the repo already ignoring `.trash/` (shipped by
  [`template/`](template/), the starting point for `data_dir` as a repo) rather
  than filtering paths itself; nothing here would stop a `.trash/` that isn't
  gitignored from being pushed. A push failure comes back as `SyncError`
  (git's own stderr) and is shown as a `Flash`, same as a bad pasted link — a
  non-tech user reads the gallery, not a stack trace. `/sync` is its own branch
  in `do_POST`, returning early rather than falling into the
  write_page()/`publish()` trailer the other routes share, because syncing
  doesn't change the collection — there's nothing for either to catch up on.
  `sync_pending()` is what the button's `disabled` attribute is set from
  (`sync_pending` on `render_page()`, computed on every `/` request from a
  dirty `git status --porcelain` *or* a local commit `@{u}..HEAD` doesn't have
  yet) — an unresolvable `@{u}` (no push has ever reached the remote) counts
  as pending too, rather than as "0 ahead", since a comparison that can't even
  be made is not evidence that nothing needs sending.
- `artwall/commands.py` — pure argv builders for `magick` (the gradient-canvas
  compose, plus `thumbnail_command`, the published site's web-sized copy), the
  desktop queries and wallpaper setters `desktop.py` runs (`swaymsg`,
  `kscreen-doctor`, and `plasma_wallpaper_command` — a Plasma desktop script sent
  over `gdbus`), and the `git` commands
  `sync()`/`sync_pending()` run.
- `artwall/desktop.py` — everything compositor-specific, behind one `Desktop`
  NamedTuple: `outputs()` (each an `Output` carrying name + pixel size + HiDPI
  scale + logical position, which is how the daemon finds its GTK monitor) and
  `wallpaper(name, path)` (the argv that sets one display's wallpaper). Two
  instances: `SWAY` (`swaymsg`) and `PLASMA` (`kscreen-doctor -j`, and a desktop
  script over D-Bus); `detect(environ)` picks one from `SWAYSOCK` /
  `XDG_CURRENT_DESKTOP` and refuses anything else. Plasma addresses a desktop by
  screen index, so its script resolves the connector with `screenForConnector()`.
  **Plasma ignores a wallpaper URL it already shows** — rewriting
  `current-<name>.jpg` in place and setting it again leaves the old painting on
  screen — so `plasma_wallpaper()` appends the file's mtime as `?v=`, making every
  render a new URL for the same file.
- `artwall/app.py` — orchestration. `run(config, rng, runner, desktop, throttle,
  only)` injects `rng`, `runner` and `desktop` (defaulting to `random`,
  `subprocess.run` and `desktop.detect(os.environ)`) so the full flow can be driven deterministically; `only` restricts the run to a
  single named output (the caption's refresh button → `--output`). `search_entities()`
  backs `--find`. For each display it resolves the Wikipedia URL (`_wiki_url`)
  and writes `caption_file(name)` for the daemon.
  `resolve_link(config, link)` backs the gallery's paste box: a pasted Wikipedia
  image URL → Commons file → the artwork's QID → the **same** `selection.record()`
  the wallpaper writes. That equivalence is the whole point — a pasted painting is
  indistinguishable from a shown one, so the gallery, trash and archive stay a
  single code path (`test_the_record_matches_what_run_writes_for_the_same_painting`
  pins it). A file with no QID falls to `_record_from_template()`, which builds
  that identical record out of the Commons `{{Artwork}}` wikitext — same shape,
  same downstream (`test_a_wikitext_record_is_shaped_like_a_wikidata_one` pins
  *that*). Every rejection raises `LinkError`, whose message is written to be read
  by whoever pasted the link, so the gallery can show it verbatim instead of
  mapping exception types to prose.
- `artwall/daemon.py` — what bare `python3 -m artwall` runs, launched with the
  session. It sets its process name to `artwall` (`prctl`, so `/proc/<pid>/comm`),
  because its command line is identical to its one-shot children's; that name is
  how `supersede_running_instances()` finds an older daemon to replace. It shows
  a persistent GTK3 + gtk-layer-shell widget per display — one `BOTTOM`-layer clickable caption per display — each
  followed by a ★ button (`--star <name>`), a gallery button (`xdg-open` on
  `gallery_url()`) and a refresh button that re-rolls that
  display (`--output <name>`) — matched to GTK monitors **by geometry** (GTK exposes the
  monitor model, not the Sway connector name) and reloaded via a `Gio.FileMonitor`
  on the cache dir whenever `run()` rewrites a `caption-<name>.json`. Every
  child goes through `artwall()`, which `Popen`s `python3 -m artwall …` and reaps
  it from a `GLib.timeout`.
  **It drives rotation.** `main()` runs `artwall --once --throttle`
  once at startup and every `ROTATION_CHECK_SECONDS` (60) after, and
  `--once --throttle --min-interval 5` on `monitor-added` — a hotplug re-rolls every
  display, which is what gives the new screen a painting, and the short interval
  folds a dock's several monitors into one run. The throttle stays in tested
  `run()` rather than here, since this module isn't covered. Both buttons go
  through `_spawn()`, which disables the button until its child exits: starring downloads a full-size image, and doing that inline would
  freeze the widget. Nothing rewrites the caption file on a star, so `_toggle_star`
  refreshes its own icon from `stars.json` once the child exits.
  **It's the daemon, so it owns the gallery's lifetime.** `--serve-stars` binds
  `stars.start_gallery()` and runs `serve_forever()` on a daemon thread for the
  process's life — the gallery button then opens a live, editable page; with
  `--no-serve-stars`, `gallery_url()` falls back to the archived `stars.html` as a
  `file://` URI (and `main()` writes it first, so the button is never dead).
  `--publish-stars` covers **both** mutation paths, which is the whole reason it
  belongs here rather than on a oneshot: the gallery's own buttons republish
  in-process (`_Gallery.republish`), and the caption's ★ republishes via
  `_publish_async()` in `_after_star`. Both flags are `BooleanOptionalAction`
  defaulting to `True` — they are what the daemon is *for*, so they exist to be
  negated. **`_publish_async` is a thread, not a child process.** It used to spawn
  `artwall --publish`, which is the only reason that CLI flag existed; `publish()`
  touches no GTK, so a thread does it with no second interpreter and no flag. It
  must stay off the main loop either way — it runs `magick` per new painting, and
  doing that inline would freeze every caption on every screen.
  **The lone module that needs a GUI
  toolkit + a live display + a long-lived process** — kept out of the stdlib-only
  oneshot, omitted from coverage, but type-checked (GTK3 PyGObject-stubs, built
  via `PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3` in `make install-dev`).

Flow in `run()`: if `throttle` and `config.stamp` was touched more recently than
`config.min_interval`, return early (the rotation throttle). Otherwise:
fetch/cache the catalogue (`painting_ids()`: a fresh per-filter-set cache wins;
else on a true first run, seed from the shipped `bundled_ids_file()` if present —
the default filters ship one, so no WDQS hit; else one SPARQL query → all matching
painting QIDs as a CSV of bare ints, cached under `painting-ids-<hash>.json`. If
that WDQS refresh fails (it's outage-prone), fall back to the stale cache — or the
bundle — rather than crashing; the stale mtime is left untouched so the next run
retries and self-heals once WDQS recovers.
`dump_catalogue()` / `make catalogue` regenerates the shipped seed) → query the
active outputs (`desktop.outputs()`) → for each display,
pick a random QID and fetch its image filename + title/date via the Action API
(`wbgetentities`), then a second `wbgetentities` for the creator's name (retry up
to `ATTEMPTS`, same as a QID that has since lost its image, to skip one whose
native resolution — a Commons `imageinfo` call — is smaller than the display on
*both* sides: `commands.compose_command`'s aspect-preserving `-resize` would
enlarge, and blur, it; `wikidata.fits()` is the check), build and
download a width-capped Commons thumbnail, `magick`-compose it onto an
`Output`-sized gradient canvas (the whole painting) at `current-<output>.jpg`,
set it with `desktop.wallpaper()` (on Sway `swaymsg output <name> bg … fill`, a
1:1 blit since the canvas is already the display's size), and write
`caption-<output>.json` (text + Wikipedia URL) for the daemon → touch
`config.stamp`. Selection is plain random — no persisted history — but QIDs
already chosen this run are excluded so each display gets a *different* painting.
The pick/download/compose step is `_render()` (takes the target width/height),
also used by `preview()` (the `--preview` flag), which composes at a
default 1920x1080, writes `preview.jpg`, opens it with `xdg-open`, and leaves the
wallpaper untouched.

`desktop.py`'s live readers (`sway_outputs()`, `plasma_outputs()`) are the
functions excluded from coverage (`# pragma: no cover`) — they need a live
compositor; their pure parsing is split out and tested (`parse_outputs()`,
`parse_kscreen_outputs()`, and the daemon's `parse_font_name()`). `artwall/daemon.py` is excluded wholesale (`.coveragerc`
omit) — it can't run headless. All state is cached under `~/.cache/artwall/`;
deleting it is a safe reset. The one exception is the starred gallery under
`~/.local/share/artwall/` (`stars.json` + `images/` + `stars.html` + `.trash/`) —
durable, self-contained and meant to be backed up, which is exactly why it isn't cache.
Its `public/` subdirectory is the odd one out: durable in location but entirely
derived, so deleting it costs nothing but the daemon's next publish.

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

No installer and no systemd. The user launches `bin/artwall` with the session:
an `exec_always` line in the Sway config, or a `~/.config/autostart/*.desktop`
entry on Plasma (README has both). The daemon does the rest — it rebuilds its surfaces on monitor hotplug via `Gdk.Display`
`monitor-added`/`monitor-removed`, and runs every `python3 -m artwall …` child
(rotation, hotplug, refresh, star); the children inherit the launcher's
`PYTHONPATH`, so a bare `python3 -m artwall` resolves the package. `bin/artwall`
is a small shell launcher that sets `PYTHONPATH` to the repo and execs
`python3 -m artwall "$@"`. A failed
run prints to the daemon's stderr and is skipped; it doesn't touch the stamp, so
the next minute's run retries. On Sway the daemon is a child of Sway, so it and
its children inherit `SWAYSOCK` and `swaymsg` works with no environment import
(`swaymsg` talks to the IPC socket, it does not need `WAYLAND_DISPLAY`). Nothing
is pip-installed, so the checkout must stay put — the launch line points at it.

Rotation is self-throttled via `config.stamp`'s mtime — each oneshot run exits
straight away. The one persistent process of ours is the daemon. It
also runs the gallery's loopback HTTP server on a background thread for
its own lifetime, and publishes on another — both on by default, opt out with
`--no-serve-stars` / `--no-publish-stars` (`bin/artwall` passes `"$@"`
through). There is no standalone gallery command, which is what makes "exactly one
gallery" structural rather than enforced. The
trash outlives it: only the gallery's "Delete forever" button removes a painting.

[`template/`](template/) is not Python and ships nothing to `artwall/` — it's the
starting point for `data_dir` as its own git repo, for someone who wants the ⇪ Sync
button (`stars.is_git_repo()`/`stars.sync()`) without ever opening a terminal: its
`.gitignore` keeps `.trash/` off the remote, and its GitHub Actions workflow
deploys `public/` to GitHub Pages on every push `sync()` makes. Cloning it *is* the
one-time setup; nothing in `artwall` itself creates the repo or configures a
remote.
