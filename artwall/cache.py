from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def fresh(path: Path, ttl: float) -> bool:
    """True if path exists and was modified less than ttl seconds ago."""
    return path.exists() and time.time() - path.stat().st_mtime < ttl


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2))
