"""Pure Wikidata/Wikimedia logic — SPARQL query strings in, parsed data out.

No IO: `app` runs these queries via `web` and the resulting image URLs are
downloaded from Wikimedia Commons. Kept here so the source-specific bits stay
unit-testable.
"""
from __future__ import annotations

import re
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

# The Commons *wikitext* fallback, for files that describe their artwork only in
# an `{{Artwork}}` template and never had it copied into structured data (the
# template's own `|wikidata =` field is what would have linked one, and on plenty
# of real files it is simply blank). This is a strictly worse source than SDC —
# free text, no QIDs, no guarantees — so `parse_artwork_qid` is always tried
# first and this only runs when it finds nothing.
ARTWORK_TEMPLATES = ("artwork", "painting")

# Templates that wrap a plain name: `{{Creator:Willard Leroy Metcalf}}`. The
# trailing colon is part of the page title, which is why they're matched
# separately from the pipe-separated templates below.
NAMED_TEMPLATES = ("creator:", "institution:")

# `{{title|en=Cornish Hills|de=Die Hügel von Cornish}}` — a per-language value.
TITLE_TEMPLATE = "title"

# `object type = painting` is the wikitext stand-in for `P31 = Q3305213`. It is
# free text rather than a QID, so it's a weaker guard than `is_painting()` — but
# it is the only claim the file makes about what it shows, and refusing to read
# it would reject every correctly-described painting on this path.
OBJECT_TYPE = "object type"
PAINTING_TYPE = "painting"

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


def _split_params(body: str) -> list[str]:
    """A template body's top-level `|`-separated parts.

    Nested templates and wiki links carry pipes of their own (`{{title|en=…}}`,
    `[[Foo|Bar]]`), so a plain `str.split("|")` would shred them — the depth of
    both bracket kinds has to be tracked.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = index = 0
    while index < len(body):
        pair = body[index : index + 2]
        if pair in ("{{", "[["):
            depth += 1
        elif pair in ("}}", "]]"):
            depth -= 1
        else:
            if body[index] == "|" and depth == 0:
                parts.append("".join(current))
                current = []
            else:
                current.append(body[index])
            index += 1
            continue
        current.append(pair)
        index += 2
    parts.append("".join(current))
    return parts


def _template_body(wikitext: str, names: tuple[str, ...]) -> str | None:
    """Everything between `{{Name` and its matching `}}`, for the first name that
    occurs, or None. Brace-matched rather than regexed, since these templates nest."""
    lowered = wikitext.lower()
    for name in names:
        start = lowered.find("{{" + name)
        if start == -1:
            continue
        after = start + 2 + len(name)
        # `{{Artworks}}` is not `{{Artwork}}`. A name ending in ":" is a page-title
        # prefix (`{{Creator:Someone}}`), where a following letter is the point.
        if not name.endswith(":") and after < len(wikitext) and wikitext[after].isalnum():
            continue
        depth = 0
        index = start
        while index < len(wikitext) - 1:
            pair = wikitext[index : index + 2]
            if pair == "{{":
                depth += 1
            elif pair == "}}":
                depth -= 1
                if depth == 0:
                    return wikitext[after:index]
            else:
                index += 1
                continue
            index += 2
    return None


def _params(body: str) -> dict[str, str]:
    """`name = value` pairs from a template body, names lowercased and despaced.

    Positional (unnamed) parameters are dropped: every field this reads is named.
    """
    params = {}
    for part in _split_params(body):
        name, sep, value = part.partition("=")
        if sep:
            params[" ".join(name.split()).lower()] = value.strip()
    return params


def _plain(wikitext: str) -> str:
    """Wikitext reduced to display text: links unwrapped, leftover markup dropped."""
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", wikitext)  # [[Foo|Bar]] -> Bar
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)  # [[Foo]] -> Foo
    text = re.sub(r"\[[^\s\]]+\s+([^\]]*)\]", r"\1", text)  # [http://x Label] -> Label
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)  # an unrecognised template says nothing
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def _value(wikitext: str, language: str) -> str:
    """One `{{Artwork}}` field as display text.

    `{{title|en=…|de=…}}` picks the caption language; `{{Creator:Name}}` and
    friends unwrap to the name; anything else falls through to plain text.
    """
    body = _template_body(wikitext, (TITLE_TEMPLATE,))
    if body is not None:
        params = _params(body)
        if params:
            return _plain(params.get(language) or next(iter(params.values())))
        return _plain(_split_params(body)[-1])  # {{title|Cornish Hills}}
    body = _template_body(wikitext, NAMED_TEMPLATES)
    return _plain(body if body is not None else wikitext)


def parse_artwork_template(
    wikitext: str, language: str
) -> dict[str, str] | None:
    """Artist/title/date from a Commons file's `{{Artwork}}` template.

    None if the page has no such template, or if it doesn't say the object is a
    painting — that check stands in for `is_painting()` on this path, and without
    it a pasted photograph would be archived as art.

    Only a plain-text `date` is read. Commons also writes dates as templates
    (`{{other date|circa|1911}}`), which reduce to "" here and leave the painting
    captioned without a year rather than with a wrong one.
    """
    body = _template_body(wikitext, ARTWORK_TEMPLATES)
    if body is None:
        return None
    params = _params(body)
    if PAINTING_TYPE not in _plain(params.get(OBJECT_TYPE, "")).lower():
        return None
    return {
        "artist": _value(params.get("artist", ""), language),
        "title": _value(params.get("title", ""), language),
        "date": _value(params.get("date", ""), language),
    }


def file_page_url(commons_file_url: str, title: str) -> str:
    """The Commons description page for a file title — the article link for a
    painting that has no Wikidata item, and so no Wikipedia article either.

    The `File:` colon is left unescaped — it's a namespace separator in the page
    title, and percent-encoding it gives a URL Commons doesn't resolve."""
    return commons_file_url + urllib.parse.quote(title.replace(" ", "_"), safe=":")


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
