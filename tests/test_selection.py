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


if __name__ == "__main__":
    unittest.main()
