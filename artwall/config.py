from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CACHE = Path.home() / ".cache" / "artwall"
SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1/search"
OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
IDS_TTL = 7 * 24 * 60 * 60
HISTORY_LIMIT = 500


@dataclass
class Config:
    """Where state lives and which endpoints to hit.

    Defaults target the live Met API and the real cache directory. Tests
    construct a Config pointing at a temp dir and a local HTTP server, so no
    network or mocking is required.
    """

    cache_dir: Path = DEFAULT_CACHE
    search_url: str = SEARCH_URL
    object_url: str = OBJECT_URL
    ids_ttl: int = IDS_TTL
    history_limit: int = HISTORY_LIMIT

    @property
    def ids_file(self) -> Path:
        return self.cache_dir / "met_painting_ids.json"

    @property
    def history_file(self) -> Path:
        return self.cache_dir / "history.json"

    @property
    def current_image(self) -> Path:
        return self.cache_dir / "current.jpg"

    @property
    def preview_image(self) -> Path:
        return self.cache_dir / "preview.jpg"
