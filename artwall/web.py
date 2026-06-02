from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Wikimedia asks for a descriptive User-Agent with contact info; a bare default
# one gets 403/429'd.
USER_AGENT = "artwall/1.0 (https://github.com/artemave/artwall)"


def _request(
    url: str, params: dict[str, str] | None, accept: str | None = None
) -> urllib.request.Request:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": USER_AGENT}
    if accept:  # WDQS picks its result format from Accept, not a query param
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def get_json(url: str, params: dict[str, str] | None = None, accept: str | None = None) -> Any:
    with urllib.request.urlopen(_request(url, params, accept), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(url: str, params: dict[str, str] | None = None, accept: str | None = None) -> str:
    with urllib.request.urlopen(_request(url, params, accept), timeout=90) as r:
        body: bytes = r.read()
        return body.decode("utf-8")


def download(url: str, path: Path) -> None:
    # urllib follows the Commons FilePath -> upload.wikimedia.org redirect for us.
    with urllib.request.urlopen(_request(url, None), timeout=90) as r:
        path.write_bytes(r.read())
