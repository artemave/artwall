from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class Config:
    """Where state lives and which endpoints to hit.

    Defaults target live Wikidata and the real cache directory. Tests construct a
    Config pointing at a temp dir and a local HTTP server, so no network or
    mocking is required.
    """

    cache_dir: Path = DEFAULT_CACHE
    sparql_url: str = SPARQL_URL
    api_url: str = API_URL
    commons_url: str = COMMONS_URL
    ids_ttl: int = IDS_TTL
    min_interval: float = MIN_INTERVAL

    @property
    def ids_file(self) -> Path:
        return self.cache_dir / "painting-ids.json"

    @property
    def preview_image(self) -> Path:
        return self.cache_dir / "preview.jpg"

    def output_image(self, name: str) -> Path:
        return self.cache_dir / f"current-{name}.jpg"

    @property
    def stamp(self) -> Path:
        """Marker file whose mtime records the last wallpaper change."""
        return self.cache_dir / "last_change"
