import unittest

from artwall import wikidata


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
                "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1503-00-00T00:00:00Z"}}}}],
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
                "P571": [{"mainsnak": {"datavalue": {"value": {"time": "-0500-00-00T00:00:00Z"}}}}],
            },
            {},
        )
        self.assertEqual(wikidata.parse_entity(result, 3, "en")["date"], "500 BC")


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


if __name__ == "__main__":
    unittest.main()
