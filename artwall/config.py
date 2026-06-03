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
    collections: list[str] = field(default_factory=list)
    font_size: int = 22  # caption point size
    # caption placement: which corner, and the inset from the screen edges in
    # pixels (absolute, so it sits the same fixed distance from the edge on every
    # display). corner is one of top-left/top-right/bottom-left/bottom-right.
    caption_corner: str = "bottom-right"
    caption_pad_x: int = 24
    caption_pad_y: int = 64
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

    def ids_file(self, query: str) -> Path:
        """Catalogue cache path, keyed by the query so changing a filter refetches."""
        digest = hashlib.md5(query.encode()).hexdigest()[:12]
        return self.cache_dir / f"painting-ids-{digest}.json"

    @property
    def preview_image(self) -> Path:
        return self.cache_dir / "preview.jpg"

    def output_image(self, name: str) -> Path:
        return self.cache_dir / f"current-{name}.jpg"

    @property
    def stamp(self) -> Path:
        """Marker file whose mtime records the last wallpaper change."""
        return self.cache_dir / "last_change"
