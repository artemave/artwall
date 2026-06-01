import unittest

from artwall import selection


class AvailableIds(unittest.TestCase):
    def test_filters_out_seen_ids(self):
        available, history = selection.available_ids([1, 2, 3], [2])
        self.assertEqual(available, [1, 3])
        self.assertEqual(history, [2])

    def test_resets_when_everything_seen(self):
        available, history = selection.available_ids([1, 2], [1, 2])
        self.assertEqual(available, [1, 2])
        self.assertEqual(history, [])


class PickImageUrl(unittest.TestCase):
    def test_prefers_primary_image(self):
        meta = {"primaryImage": "big.jpg", "primaryImageSmall": "small.jpg"}
        self.assertEqual(selection.pick_image_url(meta), "big.jpg")

    def test_falls_back_to_small(self):
        self.assertEqual(selection.pick_image_url({"primaryImageSmall": "s.jpg"}), "s.jpg")

    def test_none_when_no_image(self):
        self.assertIsNone(selection.pick_image_url({"primaryImage": ""}))
        self.assertIsNone(selection.pick_image_url({}))


class Caption(unittest.TestCase):
    def test_full_caption(self):
        meta = {"artistDisplayName": "Monet", "title": "Water Lilies", "objectDate": "1916"}
        self.assertEqual(selection.caption(meta), "Monet — Water Lilies 1916")

    def test_defaults_for_missing_fields(self):
        self.assertEqual(selection.caption({}), "Unknown artist — Untitled")

    def test_strips_trailing_space_when_no_date(self):
        meta = {"artistDisplayName": "Monet", "title": "Water Lilies"}
        self.assertEqual(selection.caption(meta), "Monet — Water Lilies")


class TrimHistory(unittest.TestCase):
    def test_appends(self):
        self.assertEqual(selection.trim_history([1, 2], 3, 500), [1, 2, 3])

    def test_trims_to_limit(self):
        self.assertEqual(selection.trim_history([1, 2, 3], 4, 2), [3, 4])


if __name__ == "__main__":
    unittest.main()
