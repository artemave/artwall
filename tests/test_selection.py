import unittest

from artwall import selection


class Caption(unittest.TestCase):
    def test_full_caption(self):
        painting = {"artist": "Monet", "title": "Water Lilies", "date": "1916"}
        self.assertEqual(selection.caption(painting), "Monet — Water Lilies 1916")

    def test_defaults_for_missing_fields(self):
        self.assertEqual(selection.caption({}), "Unknown artist — Untitled")

    def test_strips_trailing_space_when_no_date(self):
        painting = {"artist": "Monet", "title": "Water Lilies", "date": ""}
        self.assertEqual(selection.caption(painting), "Monet — Water Lilies")


class Record(unittest.TestCase):
    def test_keeps_what_the_overlay_and_the_gallery_need(self):
        painting = {
            "artist": "Monet",
            "title": "Water Lilies",
            "date": "1916",
            "image": "Water Lilies.jpg",
            "creator_qid": "Q296",  # resolved already; the record has no use for it
        }
        self.assertEqual(
            selection.record("Q1234", painting, "https://en.wikipedia.org/wiki/Water_Lilies"),
            {
                "key": "Q1234",
                "artist": "Monet",
                "title": "Water Lilies",
                "date": "1916",
                "image": "Water Lilies.jpg",
                "url": "https://en.wikipedia.org/wiki/Water_Lilies",
            },
        )

    def test_a_record_captions_itself(self):
        # the overlay draws the caption straight from the record it reads off disk
        painting = {"artist": "Monet", "title": "Water Lilies", "date": "1916", "image": "x.jpg"}
        self.assertEqual(
            selection.caption(selection.record("Q1", painting, "")), "Monet — Water Lilies 1916"
        )


if __name__ == "__main__":
    unittest.main()
