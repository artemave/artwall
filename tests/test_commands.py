import unittest
from pathlib import Path

from artwall import commands


class Commands(unittest.TestCase):
    def test_annotate_command(self):
        argv = commands.annotate_command(Path("/tmp/current.jpg"), "Monet — Water Lilies 1916")
        self.assertEqual(argv[0], "magick")
        self.assertEqual(argv[1], "/tmp/current.jpg")  # read
        self.assertEqual(argv[-1], "/tmp/current.jpg")  # written in place
        self.assertIn("-annotate", argv)
        self.assertIn(" Monet — Water Lilies 1916 ", argv)

    def test_wallpaper_command(self):
        self.assertEqual(
            commands.wallpaper_command(Path("/tmp/current.jpg")),
            ["swaymsg", "output", "*", "bg", "/tmp/current.jpg", "fill"],
        )

    def test_open_command(self):
        self.assertEqual(
            commands.open_command(Path("/tmp/preview.jpg")),
            ["xdg-open", "/tmp/preview.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
