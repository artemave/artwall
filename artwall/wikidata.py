"""Pure Wikidata/Wikimedia logic — SPARQL query strings in, parsed data out.

No IO: `app` runs these queries via `web` and the resulting image URLs are
downloaded from Wikimedia Commons. Kept here so the source-specific bits stay
unit-testable.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

PAINTING = "wd:Q3305213"  # the Wikidata item for "painting"

# Config knob -> the Wikidata property it filters on. Values within one knob are
# OR'd; separate knobs are AND'd (Impressionist landscapes = movements ∩ genres).
FILTER_PROPERTIES = {
    "artists": "P170",
    "movements": "P135",
    "genres": "P136",
    "collections": "P195",
}


def catalogue_query(
    filters: dict[str, list[str]] | None = None,
    date_begin: int | None = None,
    date_end: int | None = None,
) -> str:
    """SPARQL for every painting that has an image (and matches the filters), as
    bare numeric QIDs.

    With a date bound, restrict to works whose inception (P571) year falls in
    range — which also drops undated works, the price of a date filter.
    """
    clauses = ""
    for knob, qids in (filters or {}).items():
        if not qids:
            continue
        values = " ".join(f"wd:{q}" for q in qids)
        clauses += f"VALUES ?{knob} {{ {values} }} ?p wdt:{FILTER_PROPERTIES[knob]} ?{knob} . "
    if date_begin is not None or date_end is not None:
        lo = date_begin if date_begin is not None else -9999
        hi = date_end if date_end is not None else 9999
        clauses += f"?p wdt:P571 ?date . FILTER(YEAR(?date) >= {lo} && YEAR(?date) <= {hi}) "
    return (
        'SELECT (STRAFTER(STR(?p), "entity/Q") AS ?qid) WHERE { '
        f"?p wdt:P31 {PAINTING} ; wdt:P18 [] . {clauses}"
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


def parse_sitelink(result: dict[str, Any], entity_id: str, language: str) -> str | None:
    """The `language` Wikipedia article URL for an entity (painting or creator),
    from a `wbgetentities props=sitelinks/urls` response, or None if it has none."""
    sitelinks = result["entities"][entity_id].get("sitelinks", {})
    site = sitelinks.get(f"{language}wiki")
    return site["url"] if site else None


def entity_url(qid: int) -> str:
    """The (always-present) Wikidata page for a painting — the article fallback."""
    return f"https://www.wikidata.org/wiki/Q{qid}"


def parse_search(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(QID, label, description) rows from a wbsearchentities response."""
    return [
        (r["id"], r.get("label", ""), r.get("description", "")) for r in result["search"]
    ]


def image_url(commons_url: str, filename: str, width: int) -> str:
    """A width-capped Commons thumbnail URL — originals can be 100+ MB."""
    return f"{commons_url}{urllib.parse.quote(filename)}?width={width}"


def _year(inception: str) -> str:
    """Year out of a Wikidata time like `+1503-00-00T00:00:00Z` (BC: `-0500-…`)."""
    bc = inception.startswith("-")
    year = int(inception.lstrip("+-").split("-", 1)[0])
    return f"{year} BC" if bc else str(year)
