from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "artwall"
# Starred paintings live here, *not* under the cache: wiping `~/.cache/artwall`
# is documented as a safe reset, and it must not take your stars with it. The
# whole directory — list, images and the generated gallery, which links to the
# images by *relative* path — is self-contained, so it can be backed up or
# copied to another machine and still open in a browser.
DEFAULT_DATA = Path.home() / ".local" / "share" / "artwall"
# Subdirectory of `data_dir` holding the starred paintings themselves. Also the
# `<img src>` prefix in the generated gallery, hence a bare name, not a path.
STARS_IMAGE_DIR = "images"
# Where an unstarred painting waits until you empty the trash. Durable, like the
# gallery itself — a removal is only final once you say so — and dot-prefixed so
# it stays out of the way of the directory you actually look at.
STARS_TRASH_DIR = ".trash"
# The publishable copy of the gallery (`--publish-stars`): a self-contained site
# under its own subdirectory of `data_dir`, so uploading it can't sweep up
# `stars.json` or `.trash/` along with the paintings. Everything inside it is
# derived — deleting it and republishing loses nothing.
PUBLIC_DIR = "public"
# Web-sized copies inside the published site. The full-size archives are exported
# next to them under `STARS_IMAGE_DIR`, and each tile links to one; the grid never
# loads them, because a page of 2560px scans is tens of megabytes on a phone.
PUBLIC_THUMB_DIR = "thumbs"
# WDQS (query service) is only used for the catalogue — it's prone to outages, so
# keep it off the per-painting hot path. Per-painting data comes from the stable
# Action API, and images from Commons.
SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"
COMMONS_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/"
# Commons' own Action API. Separate from `API_URL` (Wikidata's): resolving a
# pasted image link starts at the *file*, and only Commons knows which Wikidata
# item that file depicts (its structured data).
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
# Commons file *description* pages. The article link for a painting that reached
# the gallery through the wikitext fallback: it has no Wikidata item, so there is
# no Wikipedia article and no `entity_url()` to fall back to either.
COMMONS_FILE_URL = "https://commons.wikimedia.org/wiki/"
IDS_TTL = 30 * 24 * 60 * 60  # the painting catalogue rarely changes; refetch monthly

# Throttle for event-driven runs: with --throttle, a run is a no-op if the last
# change happened fewer than this many seconds ago.
MIN_INTERVAL = 30 * 60

# Default collections to draw from: large, open-access museums known for clean,
# frameless scans, so the wallpaper is the artwork itself — not a photo of a
# framed painting on a gallery wall. Set `collections = []` to draw from *all*
# paintings instead. (Find more QIDs with `--find`.)
DEFAULT_COLLECTIONS = [
    "Q190804",  # Rijksmuseum (Amsterdam)
    "Q842858",  # Nationalmuseum (Sweden)
    "Q671384",  # Statens Museum for Kunst / SMK (Denmark)
    "Q214867",  # National Gallery of Art (Washington)
    "Q239303",  # Art Institute of Chicago
    "Q731126",  # J. Paul Getty Museum
    "Q657415",  # Cleveland Museum of Art
    "Q49133",   # Museum of Fine Arts, Boston
]

# Pre-fetched catalogue shipped with the package, so the *first* run works without
# querying the rate-limited WDQS. Keyed by the same hash as the cache; regenerate
# with `make catalogue` when the default filter-set changes.
CATALOGUE_DIR = Path(__file__).parent / "catalogue"


def config_file() -> Path:
    """User config location, honouring `$XDG_CONFIG_HOME` (default `~/.config`)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "artwall" / "config.toml"


@dataclass
class Config:
    """What to draw from and where state lives.

    Defaults draw from *every* painting on Wikidata that has an image. The
    content knobs (date window + the QID filters) narrow that; `load()` overlays
    the user's TOML. Tests construct a Config pointing at a temp dir and a local
    HTTP server, so no network or mocking is required.
    """

    cache_dir: Path = DEFAULT_CACHE
    data_dir: Path = DEFAULT_DATA  # durable state (the star list), never auto-purged
    catalogue_dir: Path = CATALOGUE_DIR  # packaged first-run catalogue seed
    sparql_url: str = SPARQL_URL
    api_url: str = API_URL
    commons_url: str = COMMONS_URL
    commons_api_url: str = COMMONS_API_URL
    commons_file_url: str = COMMONS_FILE_URL
    ids_ttl: int = IDS_TTL
    # content filters (all optional). dates are inception years (negative = BC);
    # the rest are lists of Wikidata QIDs — find them with `--find` or wikidata.org.
    date_begin: int | None = None
    date_end: int | None = None
    language: str = "en"  # caption/label language
    artists: list[str] = field(default_factory=list)
    movements: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=lambda: list(DEFAULT_COLLECTIONS))
    # the overlay caption's point size; None = the desktop's own UI font size.
    font_size: int | None = None
    # overlay placement: which corner, and the inset in device pixels from the
    # screen edges — or from the edge of any panel or bar there. corner is one of
    # top-left/top-right/bottom-left/bottom-right.
    caption_corner: str = "bottom-right"
    caption_pad_x: int = 24
    caption_pad_y: int = 64
    # Width to archive a starred painting at. Big enough to keep and re-use,
    # small enough not to pull a Commons original (those run to 100+ MB).
    stars_image_width: int = 2560
    # Longest side of a published site's grid image. Covers a phone's single
    # ~390pt column at 3x without shipping the archive itself, which is what makes
    # the published page usable on a cellular connection.
    public_image_width: int = 1200
    # Where you upload `public_dir` to, if you do. Purely a note to yourself:
    # nothing here uploads anything, so this is never checked or fetched — setting
    # it only puts a link to the live site in your own gallery, so the address is
    # somewhere you'll find it. Empty means no link is shown.
    public_url: str = ""
    # Whose collection this is, shown in the gallery's heading ("Alex starred 12
    # paintings") in place of the generic "★ 12 starred paintings" — every
    # rendering (served, archived, published) agrees, since they share
    # `stars._title()`. Empty means the generic heading.
    owner: str = ""
    min_interval: float = MIN_INTERVAL

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Build a Config from the user's TOML, falling back to the defaults.

        Keys are passed straight to the constructor, so a typo'd key fails loudly
        rather than being silently ignored.
        """
        path = path or config_file()
        if not path.exists():
            return cls()
        with path.open("rb") as f:
            return cls(**tomllib.load(f))

    @property
    def filters(self) -> dict[str, list[str]]:
        return {
            "artists": self.artists,
            "movements": self.movements,
            "genres": self.genres,
            "collections": self.collections,
        }

    def ids_filename(self, query: str) -> str:
        """Catalogue filename, keyed by the query so changing a filter refetches."""
        digest = hashlib.md5(query.encode()).hexdigest()[:12]
        return f"painting-ids-{digest}.json"

    def ids_file(self, query: str) -> Path:
        """Catalogue cache path (under `cache_dir`)."""
        return self.cache_dir / self.ids_filename(query)

    def bundled_ids_file(self, query: str) -> Path:
        """Packaged catalogue path for the same query — the first-run seed."""
        return self.catalogue_dir / self.ids_filename(query)

    @property
    def preview_image(self) -> Path:
        return self.cache_dir / "preview.jpg"

    def output_image(self, name: str) -> Path:
        return self.cache_dir / f"current-{name}.jpg"

    def caption_file(self, name: str) -> Path:
        """Where `run()` writes a display's caption + link for the overlay to read."""
        return self.cache_dir / f"caption-{name}.json"

    @property
    def stars_file(self) -> Path:
        """The starred paintings, oldest first. Under `data_dir`, so clearing the
        cache doesn't discard them."""
        return self.data_dir / "stars.json"

    @property
    def stars_page(self) -> Path:
        """The generated gallery. Derived from `stars_file`, but it sits with the
        images it links to so the directory stays portable."""
        return self.data_dir / "stars.html"

    def star_image(self, key: str) -> Path:
        """The archived painting for a star key (`Q<n>` or `M<n>` — see
        `selection.record`). Always `.jpg`: Commons renders a JPEG thumbnail for
        anything it downscales, and browsers sniff the rest."""
        return self.data_dir / STARS_IMAGE_DIR / f"{key}.jpg"

    @property
    def public_dir(self) -> Path:
        """The publishable static site. A directory of its own, not
        `data_dir` itself, because what you upload must not include `stars.json`
        or the trash — and because everything in here is regenerable."""
        return self.data_dir / PUBLIC_DIR

    @property
    def public_page(self) -> Path:
        """`index.html`, not `stars.html`: a static host serves it for the bare
        directory URL, which is what a published gallery's link should be."""
        return self.public_dir / "index.html"

    @property
    def public_image_dir(self) -> Path:
        """Full-size paintings inside the published site — same relative layout as
        `star_image()`, so the page's `<a href>` reads identically either side."""
        return self.public_dir / STARS_IMAGE_DIR

    @property
    def public_thumb_dir(self) -> Path:
        """The web-sized copies the published grid actually loads."""
        return self.public_dir / PUBLIC_THUMB_DIR

    def public_image(self, key: str) -> Path:
        return self.public_image_dir / f"{key}.jpg"

    def public_thumb(self, key: str) -> Path:
        return self.public_thumb_dir / f"{key}.jpg"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / STARS_TRASH_DIR

    @property
    def trash_file(self) -> Path:
        """The trashed paintings, each with the position it held in `stars_file`.
        Beside their images, so emptying the trash is one `rmtree`."""
        return self.trash_dir / "trash.json"

    def trash_image(self, key: str) -> Path:
        """Where `star_image(key)` is parked when unstarred, so restoring returns the
        exact bytes rather than re-downloading them. Same filesystem, so it's a rename."""
        return self.trash_dir / f"{key}.jpg"

    @property
    def stamp(self) -> Path:
        """Marker file whose mtime records the last wallpaper change."""
        return self.cache_dir / "last_change"

    @property
    def lock(self) -> Path:
        """Lock file serialising runs: window-focus and output events fire
        independent triggers (and a run's own `swaymsg … bg` emits output events),
        so without a lock they overlap and rotate several times in a row."""
        return self.cache_dir / "lock"
