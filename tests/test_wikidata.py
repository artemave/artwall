import unittest

from artwall import wikidata

# Wikidata always sends a precision alongside a time; 9 means "to the year".
YEAR_PRECISION = 9


def _time(time, precision=YEAR_PRECISION):
    return {"time": time, "precision": precision}


class CatalogueQuery(unittest.TestCase):
    def test_bare_query_has_painting_and_image(self):
        q = wikidata.catalogue_query()
        self.assertIn("wd:Q3305213", q)  # painting
        self.assertIn("wdt:P18", q)  # has image
        self.assertNotIn("FILTER", q)
        self.assertNotIn("VALUES", q)

    def test_date_window_adds_year_filter(self):
        q = wikidata.catalogue_query(date_begin=1500, date_end=1800)
        self.assertIn("wdt:P571", q)
        self.assertIn("YEAR(?date) >= 1500", q)
        self.assertIn("YEAR(?date) <= 1800", q)

    def test_open_ended_date_uses_sentinels(self):
        q = wikidata.catalogue_query(date_begin=1900)
        self.assertIn(">= 1900", q)
        self.assertIn("<= 9999", q)

    def test_filters_become_values_clauses(self):
        q = wikidata.catalogue_query(filters={"movements": ["Q40415"], "genres": ["Q191163"]})
        self.assertIn("wdt:P135", q)  # movement
        self.assertIn("wdt:P136", q)  # genre
        self.assertIn("wd:Q40415", q)
        self.assertIn("wd:Q191163", q)

    def test_empty_filter_lists_are_skipped(self):
        q = wikidata.catalogue_query(filters={"artists": [], "movements": ["Q37853"]})
        self.assertNotIn("P170", q)  # no artist clause
        self.assertIn("wd:Q37853", q)


class ParseCatalogue(unittest.TestCase):
    def test_skips_header_and_blanks(self):
        self.assertEqual(wikidata.parse_catalogue("qid\n101\n102\n\n"), [101, 102])


class ParseEntity(unittest.TestCase):
    def entity(self, qid, claims, labels):
        return {"entities": {f"Q{qid}": {"claims": claims, "labels": labels}}}

    def test_extracts_image_creator_title_year(self):
        result = self.entity(
            12418,
            {
                "P18": [{"mainsnak": {"datavalue": {"value": "Mona Lisa.jpg"}}}],
                "P170": [{"mainsnak": {"datavalue": {"value": {"id": "Q762"}}}}],
                "P571": [{"mainsnak": {"datavalue": {"value": _time("+1503-00-00T00:00:00Z")}}}],
            },
            {"en": {"value": "Mona Lisa"}},
        )
        self.assertEqual(
            wikidata.parse_entity(result, 12418, "en"),
            {"image": "Mona Lisa.jpg", "creator_qid": "Q762", "title": "Mona Lisa", "date": "1503"},
        )

    def test_none_when_no_image(self):
        result = self.entity(1, {}, {"en": {"value": "x"}})
        self.assertIsNone(wikidata.parse_entity(result, 1, "en"))

    def test_anonymous_undated_and_unlabelled(self):
        # P170/P571 present but as `somevalue` (no datavalue); label missing.
        result = self.entity(
            2,
            {
                "P18": [{"mainsnak": {"datavalue": {"value": "a.jpg"}}}],
                "P170": [{"mainsnak": {"snaktype": "somevalue"}}],
                "P571": [{"mainsnak": {"snaktype": "somevalue"}}],
            },
            {},
        )
        self.assertEqual(
            wikidata.parse_entity(result, 2, "en"),
            {"image": "a.jpg", "creator_qid": "", "title": "", "date": ""},
        )

    def test_bc_year(self):
        result = self.entity(
            3,
            {
                "P18": [{"mainsnak": {"datavalue": {"value": "i.jpg"}}}],
                "P571": [{"mainsnak": {"datavalue": {"value": _time("-0500-00-00T00:00:00Z")}}}],
            },
            {},
        )
        self.assertEqual(wikidata.parse_entity(result, 3, "en")["date"], "500 BC")


class DatePrecision(unittest.TestCase):
    """Wikidata stores a coarse date as a year + a precision code, so reading the
    year alone invents an exactness the record never claimed."""

    def date(self, time, precision, circa=False):
        statement = {"mainsnak": {"datavalue": {"value": _time(time, precision)}}}
        if circa:
            statement["qualifiers"] = {
                "P1480": [{"datavalue": {"value": {"id": wikidata.CIRCA}}}]
            }
        entity = {
            "entities": {
                "Q1": {
                    "claims": {
                        "P18": [{"mainsnak": {"datavalue": {"value": "a.jpg"}}}],
                        "P571": [statement],
                    },
                    "labels": {},
                }
            }
        }
        return wikidata.parse_entity(entity, 1, "en")["date"]

    def test_a_year_is_shown_as_the_year(self):
        self.assertEqual(self.date("+1660-00-00T00:00:00Z", 9), "1660")

    def test_a_century_becomes_a_century(self):
        # the reported bug: a 13th-century handscroll read as the year 1300
        self.assertEqual(self.date("+1300-00-00T00:00:00Z", 7), "13th century")

    def test_a_decade_becomes_a_decade(self):
        self.assertEqual(self.date("+1860-00-00T00:00:00Z", 8), "1860s")

    def test_a_millennium_becomes_a_millennium(self):
        self.assertEqual(self.date("+2000-00-00T00:00:00Z", 6), "2nd millennium")

    def test_ordinals_of_every_shape(self):
        self.assertEqual(self.date("+0100-00-00T00:00:00Z", 7), "1st century")
        self.assertEqual(self.date("+0200-00-00T00:00:00Z", 7), "2nd century")
        self.assertEqual(self.date("+0300-00-00T00:00:00Z", 7), "3rd century")
        self.assertEqual(self.date("+0400-00-00T00:00:00Z", 7), "4th century")
        self.assertEqual(self.date("+1100-00-00T00:00:00Z", 7), "11th century")
        self.assertEqual(self.date("+2100-00-00T00:00:00Z", 7), "21st century")

    def test_a_coarse_precision_that_the_year_contradicts_keeps_the_year(self):
        # ~half the catalogue's century-tagged works hold a specific year like
        # this: a mis-entry, not a claim about the whole century
        self.assertEqual(self.date("+1732-01-01T00:00:00Z", 7), "1732")
        self.assertEqual(self.date("+1695-01-01T00:00:00Z", 6), "1695")
        self.assertEqual(self.date("+1865-00-00T00:00:00Z", 8), "1865")

    def test_circa_is_marked(self):
        self.assertEqual(self.date("+1660-00-00T00:00:00Z", 9, circa=True), "c. 1660")

    def test_a_rounded_coarse_date_is_named_not_marked_circa(self):
        # "13th century" already says it's approximate; "c. 13th century" is noise
        self.assertEqual(self.date("+1300-00-00T00:00:00Z", 7, circa=True), "13th century")

    def test_bc_dates_keep_their_era(self):
        self.assertEqual(self.date("-0500-00-00T00:00:00Z", 9), "500 BC")
        self.assertEqual(self.date("-0500-00-00T00:00:00Z", 7), "5th century BC")

    def test_a_more_precise_date_still_shows_the_year(self):
        self.assertEqual(self.date("+1660-05-01T00:00:00Z", 11), "1660")

    def test_year_zero_is_never_dressed_up_as_an_ordinal(self):
        self.assertEqual(self.date("+0000-00-00T00:00:00Z", 7), "0")


class Label(unittest.TestCase):
    def test_returns_language_label(self):
        result = {"entities": {"Q762": {"labels": {"en": {"value": "Leonardo da Vinci"}}}}}
        self.assertEqual(wikidata.label(result, "Q762", "en"), "Leonardo da Vinci")

    def test_empty_when_language_missing(self):
        result = {"entities": {"Q1": {"labels": {}}}}
        self.assertEqual(wikidata.label(result, "Q1", "en"), "")


class ParseSearch(unittest.TestCase):
    def test_returns_id_label_description_rows(self):
        result = {"search": [{"id": "Q40415", "label": "Impressionism", "description": "movement"}]}
        self.assertEqual(wikidata.parse_search(result), [("Q40415", "Impressionism", "movement")])

    def test_tolerates_missing_label_or_description(self):
        result = {"search": [{"id": "Q1"}]}
        self.assertEqual(wikidata.parse_search(result), [("Q1", "", "")])


class ImageUrl(unittest.TestCase):
    def test_builds_encoded_commons_thumbnail(self):
        url = wikidata.image_url("https://commons/Special:FilePath/", "Mona Lisa.jpg", 2560)
        self.assertEqual(url, "https://commons/Special:FilePath/Mona%20Lisa.jpg?width=2560")


class Sitelink(unittest.TestCase):
    def _result(self, sitelinks):
        return {"entities": {"Q42": {"sitelinks": sitelinks}}}

    def test_returns_article_url_for_language(self):
        result = self._result({"enwiki": {"url": "https://en.wikipedia.org/wiki/Foo"}})
        self.assertEqual(
            wikidata.parse_sitelink(result, "Q42", "en"), "https://en.wikipedia.org/wiki/Foo"
        )

    def test_none_when_no_article_in_language(self):
        result = self._result({"frwiki": {"url": "https://fr.wikipedia.org/wiki/Foo"}})
        self.assertIsNone(wikidata.parse_sitelink(result, "Q42", "en"))

    def test_none_when_no_sitelinks_at_all(self):
        self.assertIsNone(wikidata.parse_sitelink({"entities": {"Q42": {}}}, "Q42", "en"))


class EntityUrl(unittest.TestCase):
    def test_builds_wikidata_page(self):
        self.assertEqual(wikidata.entity_url(42), "https://www.wikidata.org/wiki/Q42")


class ParseFileLink(unittest.TestCase):
    def test_reads_the_file_out_of_a_media_viewer_fragment(self):
        # clicking an image on an artist's article: the path is the *artist*, and
        # only the fragment names what was actually clicked
        link = "https://en.wikipedia.org/wiki/Muqi#/media/File:Mu-ch'i_001.jpg"
        self.assertEqual(wikidata.parse_file_link(link), "File:Mu-ch'i_001.jpg")

    def test_reads_the_file_out_of_a_file_page_url(self):
        link = "https://en.wikipedia.org/wiki/File:Bertholet.jpg"
        self.assertEqual(wikidata.parse_file_link(link), "File:Bertholet.jpg")

    def test_percent_escapes_are_decoded(self):
        link = "https://en.wikipedia.org/wiki/File:Bertholet_Fl%C3%A9mal_-_Heliodorus.jpg"
        self.assertEqual(
            wikidata.parse_file_link(link), "File:Bertholet_Flémal_-_Heliodorus.jpg"
        )

    def test_a_commons_file_page_works_too(self):
        link = "https://commons.wikimedia.org/wiki/File:Six_Persimmons.jpg"
        self.assertEqual(wikidata.parse_file_link(link), "File:Six_Persimmons.jpg")

    def test_the_fragment_wins_over_the_path(self):
        # a file page you then clicked into the viewer on: the fragment is current
        link = "https://en.wikipedia.org/wiki/File:First.jpg#/media/File:Second.jpg"
        self.assertEqual(wikidata.parse_file_link(link), "File:Second.jpg")

    def test_none_for_an_article_link_naming_no_file(self):
        self.assertIsNone(wikidata.parse_file_link("https://en.wikipedia.org/wiki/Muqi"))

    def test_none_for_a_bare_file_prefix_with_no_name(self):
        self.assertIsNone(wikidata.parse_file_link("https://en.wikipedia.org/wiki/File:"))


class ParseFilePageid(unittest.TestCase):
    def test_reads_the_page_id(self):
        result = {"query": {"pages": {"1902557": {"pageid": 1902557}}}}
        self.assertEqual(wikidata.parse_file_pageid(result), 1902557)

    def test_none_when_commons_has_no_such_file(self):
        # in-copyright art lives on a language wiki instead, never on Commons
        result = {"query": {"pages": {"-1": {"missing": ""}}}}
        self.assertIsNone(wikidata.parse_file_pageid(result))


class ParseArtworkQid(unittest.TestCase):
    def _result(self, statements):
        return {"entities": {"M42": {"statements": statements}}}

    def test_reads_the_depicted_artwork(self):
        result = self._result(
            {"P6243": [{"mainsnak": {"datavalue": {"value": {"numeric-id": 107045345}}}}]}
        )
        self.assertEqual(wikidata.parse_artwork_qid(result, "M42"), 107045345)

    def test_falls_back_to_depicts(self):
        # plenty of real scans carry only P180 — the first link the feature was
        # built for (a Muqi handscroll) is one of them
        result = self._result(
            {"P180": [{"mainsnak": {"datavalue": {"value": {"numeric-id": 107045345}}}}]}
        )
        self.assertEqual(wikidata.parse_artwork_qid(result, "M42"), 107045345)

    def test_prefers_the_exact_property_over_depicts(self):
        result = self._result(
            {
                "P180": [{"mainsnak": {"datavalue": {"value": {"numeric-id": 222}}}}],
                "P6243": [{"mainsnak": {"datavalue": {"value": {"numeric-id": 111}}}}],
            }
        )
        self.assertEqual(wikidata.parse_artwork_qid(result, "M42"), 111)

    def test_skips_a_valueless_statement_for_a_later_one(self):
        result = self._result(
            {
                "P6243": [{"mainsnak": {}}],
                "P180": [{"mainsnak": {"datavalue": {"value": {"numeric-id": 222}}}}],
            }
        )
        self.assertEqual(wikidata.parse_artwork_qid(result, "M42"), 222)

    def test_none_when_the_file_links_to_no_artwork(self):
        self.assertIsNone(wikidata.parse_artwork_qid(self._result({"P1163": []}), "M42"))

    def test_none_when_the_file_has_no_structured_data_at_all(self):
        self.assertIsNone(wikidata.parse_artwork_qid({"entities": {"M42": {}}}, "M42"))

    def test_none_for_a_somevalue_snak(self):
        result = self._result({"P6243": [{"mainsnak": {}}]})
        self.assertIsNone(wikidata.parse_artwork_qid(result, "M42"))


class IsPainting(unittest.TestCase):
    def _result(self, claims):
        return {"entities": {"Q42": {"claims": claims}}}

    def _instance(self, qid):
        return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}

    def test_true_for_a_painting(self):
        result = self._result({"P31": [self._instance(wikidata.PAINTING_QID)]})
        self.assertTrue(wikidata.is_painting(result, "Q42"))

    def test_true_when_painting_is_not_the_first_instance_of(self):
        # a self-portrait is both; the painting statement may come second
        result = self._result(
            {"P31": [self._instance("Q192110"), self._instance(wikidata.PAINTING_QID)]}
        )
        self.assertTrue(wikidata.is_painting(result, "Q42"))

    def test_false_for_a_person(self):
        self.assertFalse(wikidata.is_painting(self._result({"P31": [self._instance("Q5")]}), "Q42"))

    def test_false_when_there_is_no_instance_of_at_all(self):
        self.assertFalse(wikidata.is_painting(self._result({}), "Q42"))

    def test_false_for_a_novalue_snak(self):
        self.assertFalse(wikidata.is_painting(self._result({"P31": [{"mainsnak": {}}]}), "Q42"))


if __name__ == "__main__":
    unittest.main()
