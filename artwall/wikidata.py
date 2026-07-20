"""Pure Wikidata/Wikimedia logic — SPARQL query strings in, parsed data out.

No IO: `app` runs these queries via `web` and the resulting image URLs are
downloaded from Wikimedia Commons. Kept here so the source-specific bits stay
unit-testable.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

PAINTING_QID = "Q3305213"  # the Wikidata item for "painting"
PAINTING = f"wd:{PAINTING_QID}"

# Commons structured-data properties linking a scan to the artwork it shows, best
# first: P6243 "digital representation of" says exactly that, while P180 "depicts"
# is looser — a photograph of a cathedral depicts the cathedral. Plenty of real
# files carry only P180, so it can't be skipped; what makes it safe is that the
# resolved entity is checked with `is_painting()` before anything is starred.
DEPICTED_ARTWORK = ("P6243", "P180")

# Both link shapes Wikipedia hands out for an image carry the file here:
#   .../wiki/Muqi#/media/File:Mu-ch'i_001.jpg   (the media viewer, in the fragment)
#   .../wiki/File:Bertholet_Fl%C3%A9mal_-_….jpg (the file page, in the path)
FILE_PREFIX = "File:"

# Wikidata time precision codes, and the span each one rounds to. Anything at or
# above 9 (year, month, day) is exact enough to print the year as-is.
MILLENNIUM, CENTURY, DECADE = 6, 7, 8
COARSE = {MILLENNIUM: 1000, CENTURY: 100, DECADE: 10}

# P1480 "sourcing circumstances" = Q5727902 "circa": the painting is dated about
# then. Lives on the statement as a qualifier, not in its value.
CIRCA_QUALIFIER = "P1480"
CIRCA = "Q5727902"

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


def _statement(entity: dict[str, Any], prop: str) -> dict[str, Any] | None:
    """`entity`'s first `prop` statement, or None. The statement rather than its
    value, for the callers that need its qualifiers too (see `_date`)."""
    statements = entity["claims"].get(prop)
    return statements[0] if statements else None


def _claim(entity: dict[str, Any], prop: str) -> Any:
    """The value of `entity`'s first `prop` statement, or None.

    None covers the property being absent and `somevalue`/`novalue` snaks (e.g.
    an anonymous creator or an unknown date), which carry no `datavalue`.
    """
    statement = _statement(entity, prop)
    if statement is None:
        return None
    datavalue = statement["mainsnak"].get("datavalue")
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
    return {
        "image": str(image),
        "creator_qid": creator["id"] if creator else "",
        "title": label(result, f"Q{qid}", language),
        "date": _date(_statement(entity, "P571")),
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


def parse_file_link(link: str) -> str | None:
    """The Commons file title out of a pasted Wikipedia/Commons image link.

    The media viewer keeps the file in the fragment and the file page keeps it in
    the path, so both are searched — fragment first, since a media-viewer link's
    path is the *article* ("/wiki/Muqi"), which is not what was clicked. Returns
    None for a link that names no file at all.
    """
    parsed = urllib.parse.urlsplit(link)
    for part in (parsed.fragment, parsed.path):
        _, found, tail = part.partition(FILE_PREFIX)
        if found and tail:
            return FILE_PREFIX + urllib.parse.unquote(tail)
    return None


def parse_file_pageid(result: dict[str, Any]) -> int | None:
    """The Commons page id for a file title, or None if Commons has no such file.

    A file can be local to a language Wikipedia instead (in-copyright art usually
    is), in which case it is missing here and has no structured data to follow.
    """
    page = next(iter(result["query"]["pages"].values()))
    return None if "missing" in page else int(page["pageid"])


def parse_artwork_qid(result: dict[str, Any], media_id: str) -> int | None:
    """The QID of the artwork a Commons file shows, or None if it names none.

    Commons keeps structured data on its own entities (`M<pageid>`), under
    `statements` rather than the `claims` Wikidata uses. `DEPICTED_ARTWORK` is
    tried in order, so an exact "digital representation of" beats a loose "depicts".
    """
    statements = result["entities"][media_id].get("statements", {})
    for prop in DEPICTED_ARTWORK:
        for statement in statements.get(prop, []):
            datavalue = statement["mainsnak"].get("datavalue")
            if datavalue:
                return int(datavalue["value"]["numeric-id"])
    return None


def is_painting(result: dict[str, Any], entity_id: str) -> bool:
    """Whether an entity is `instance of: painting` — the guard on a pasted link.

    P31 can carry several values (a painting that is also a self-portrait), so
    every statement is checked, not just the first.
    """
    claims = result["entities"][entity_id]["claims"].get("P31", [])
    return any(
        (statement["mainsnak"].get("datavalue") or {}).get("value", {}).get("id") == PAINTING_QID
        for statement in claims
    )


def image_url(commons_url: str, filename: str, width: int) -> str:
    """A width-capped Commons thumbnail URL — originals can be 100+ MB."""
    return f"{commons_url}{urllib.parse.quote(filename)}?width={width}"


def _ordinal(n: int) -> str:
    """1 -> "1st", 13 -> "13th", 21 -> "21st"."""
    if 10 <= n % 100 <= 20:  # 11th–13th break the pattern the tens don't
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _date(statement: dict[str, Any] | None) -> str:
    """A painting's inception as display text, honouring Wikidata's precision.

    Wikidata stores a coarse date as a *year* plus a precision code — the 13th
    century is `+1300` with precision 7 — so reading the year alone invents an
    exactness the record never claimed ("1300" for a work dated circa 1250).

    Precision is honoured **only when the stored year agrees with it**: a century
    ending in `00`, a decade ending in `0`. Much real data is tagged century while
    holding a specific year (`+1732`), which is a mis-entry rather than a claim
    about the 18th century — there the year is plainly the better information, so
    it is kept. A `circa` qualifier (P1480) prefixes "c.".
    """
    if statement is None:
        return ""
    datavalue = statement["mainsnak"].get("datavalue")
    if not datavalue:  # `somevalue`: dated, but the date is unknown
        return ""

    time, precision = datavalue["value"]["time"], datavalue["value"]["precision"]
    bc = time.startswith("-")
    year = int(time.lstrip("+-").split("-", 1)[0])
    suffix = " BC" if bc else ""

    if precision in COARSE and year > 0 and year % COARSE[precision] == 0:
        if precision == DECADE:
            return f"{year}s{suffix}"
        unit = "century" if precision == CENTURY else "millennium"
        return f"{_ordinal(year // COARSE[precision])} {unit}{suffix}"

    circa = any(
        qualifier["datavalue"]["value"]["id"] == CIRCA
        for qualifier in (statement.get("qualifiers") or {}).get(CIRCA_QUALIFIER, [])
        if qualifier.get("datavalue")
    )
    return f"c. {year}{suffix}" if circa else f"{year}{suffix}"
