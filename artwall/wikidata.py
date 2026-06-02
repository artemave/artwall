"""Pure Wikidata/Wikimedia logic — SPARQL/entity JSON in, parsed data out.

No IO: `app` runs the catalogue query (WDQS) and per-painting lookups (Action
API) via `web`, and the resulting image URLs are downloaded from Wikimedia
Commons. Kept here so the source-specific bits stay unit-testable.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

PAINTING = "wd:Q3305213"  # the Wikidata item for "painting"


def catalogue_query() -> str:
    """SPARQL for every painting that has an image, as bare numeric QIDs."""
    return (
        'SELECT (STRAFTER(STR(?p), "entity/Q") AS ?qid) WHERE { '
        f"?p wdt:P31 {PAINTING} ; wdt:P18 [] . "
        "}"
    )


def parse_catalogue(csv_text: str) -> list[int]:
    """QID numbers from the catalogue CSV (first line is the `qid` header)."""
    return [int(line) for line in csv_text.splitlines()[1:] if line]


def _claim(entity: dict[str, Any], prop: str) -> Any:
    """The value of `entity`'s first `prop` statement, or None.

    None covers the property being absent and `somevalue`/`novalue` snaks (e.g.
    an anonymous creator or an unknown date), which carry no `datavalue`.
    """
    statements = entity["claims"].get(prop)
    if not statements:
        return None
    snak = statements[0]["mainsnak"]
    datavalue = snak.get("datavalue")
    return datavalue["value"] if datavalue else None


def parse_entity(result: dict[str, Any], qid: int, language: str) -> dict[str, str] | None:
    """Normalise one painting's Action-API entity, or None if it has no image.

    A QID cached weeks ago may since have lost its image, so we re-pick rather
    than fail. Returns the image *filename*, the creator's QID (to resolve a name
    from), the title and the year.
    """
    entity = result["entities"][f"Q{qid}"]
    image = _claim(entity, "P18")
    if not image:
        return None
    creator = _claim(entity, "P170")
    date = _claim(entity, "P571")
    return {
        "image": str(image),
        "creator_qid": creator["id"] if creator else "",
        "title": label(result, f"Q{qid}", language),
        "date": _year(date["time"]) if date else "",
    }


def label(result: dict[str, Any], entity_id: str, language: str) -> str:
    """An entity's label in `language` from an Action-API response (or "")."""
    labels = result["entities"][entity_id]["labels"]
    return str(labels.get(language, {}).get("value", ""))


def image_url(commons_url: str, filename: str, width: int) -> str:
    """A width-capped Commons thumbnail URL — originals can be 100+ MB."""
    return f"{commons_url}{urllib.parse.quote(filename)}?width={width}"


def _year(inception: str) -> str:
    """Year out of a Wikidata time like `+1503-00-00T00:00:00Z` (BC: `-0500-…`)."""
    bc = inception.startswith("-")
    year = int(inception.lstrip("+-").split("-", 1)[0])
    return f"{year} BC" if bc else str(year)
