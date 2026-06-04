from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "artwall"
# WDQS (query service) is only used for the catalogue — it's prone to outages, so
# keep it off the per-painting hot path. Per-painting data comes from the stable
# Action API, and images from Commons.
SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"
COMMONS_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/"
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
    catalogue_dir: Path = CATALOGUE_DIR  # packaged first-run catalogue seed
    sparql_url: str = SPARQL_URL
    api_url: str = API_URL
    commons_url: str = COMMONS_URL
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
    # caption point size; None = use the desktop's system font size (scaled per
    # display). Set it to override with an explicit point size.
    font_size: int | None = None
    # caption placement: which corner, and the inset from the screen edges in
    # pixels (absolute, so it sits the same fixed distance from the edge on every
    # display). corner is one of top-left/top-right/bottom-left/bottom-right.
    caption_corner: str = "bottom-right"
    caption_pad_x: int = 24
    caption_pad_y: int = 64
    # how the caption is shown: "interactive" = an interactive overlay (a separate
    # `python3 -m artwall.overlay` process) with a clickable Wikipedia link and a
    # refresh button, and nothing burned into the wallpaper; "text" = burn the
    # caption in, no overlay.
    caption_mode: str = "interactive"
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
    def stamp(self) -> Path:
        """Marker file whose mtime records the last wallpaper change."""
        return self.cache_dir / "last_change"
