import os
import tempfile
import unittest
from pathlib import Path

from artwall import config
from artwall.config import Config


class ConfigFilePath(unittest.TestCase):
    def setUp(self):
        self._xdg = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        if self._xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._xdg

    def test_honours_xdg_config_home(self):
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg"
        self.assertEqual(config.config_file(), Path("/tmp/xdg/artwall/config.toml"))

    def test_falls_back_to_dot_config(self):
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.assertEqual(config.config_file(), Path.home() / ".config" / "artwall" / "config.toml")


class LoadConfig(unittest.TestCase):
    def write(self, text):
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text(text)
        return path

    def test_defaults_when_file_missing(self):
        cfg = Config.load(Path("/no/such/config.toml"))
        self.assertIsNone(cfg.date_begin)
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.movements, [])
        self.assertEqual(cfg.collections, config.DEFAULT_COLLECTIONS)  # clean-scan museums
        self.assertIsNone(cfg.font_size)  # default: use the system font size
        self.assertEqual(cfg.caption_mode, "interactive")  # default: interactive overlay
        self.assertEqual(cfg.min_interval, config.MIN_INTERVAL)

    def test_overrides_every_knob(self):
        cfg = Config.load(
            self.write(
                'date_begin = 1850\ndate_end = 1900\nlanguage = "fr"\n'
                'movements = ["Q40415"]\ngenres = ["Q191163"]\nartists = ["Q296"]\n'
                'collections = ["Q19675"]\nfont_size = 30\nmin_interval = 600\n'
            )
        )
        self.assertEqual(cfg.date_begin, 1850)
        self.assertEqual(cfg.date_end, 1900)
        self.assertEqual(cfg.language, "fr")
        self.assertEqual(cfg.movements, ["Q40415"])
        self.assertEqual(cfg.genres, ["Q191163"])
        self.assertEqual(cfg.artists, ["Q296"])
        self.assertEqual(cfg.collections, ["Q19675"])
        self.assertEqual(cfg.font_size, 30)
        self.assertEqual(cfg.min_interval, 600)

    def test_partial_override_keeps_other_defaults(self):
        cfg = Config.load(self.write("font_size = 40\n"))
        self.assertEqual(cfg.font_size, 40)
        self.assertEqual(cfg.language, "en")  # untouched

    def test_unknown_key_fails_loudly(self):
        with self.assertRaises(TypeError):
            Config.load(self.write("date_start = 1600\n"))  # typo for date_begin


class IdsFile(unittest.TestCase):
    def test_path_keyed_by_query(self):
        cfg = Config(cache_dir=Path("/tmp/cache"))
        a = cfg.ids_file("SELECT a")
        b = cfg.ids_file("SELECT b")
        self.assertNotEqual(a, b)  # different filters -> different cache file
        self.assertEqual(cfg.ids_file("SELECT a"), a)  # stable for the same query
        self.assertEqual(a.parent, Path("/tmp/cache"))
        self.assertTrue(a.name.startswith("painting-ids-"))


class Filters(unittest.TestCase):
    def test_collects_qid_knobs(self):
        cfg = Config(movements=["Q40415"], genres=["Q191163"], collections=[])
        self.assertEqual(
            cfg.filters,
            {"artists": [], "movements": ["Q40415"], "genres": ["Q191163"], "collections": []},
        )


if __name__ == "__main__":
    unittest.main()
