import unittest
from pathlib import Path

from artwall import commands


class Commands(unittest.TestCase):
    def test_compose_command(self):
        argv = commands.compose_command(
            Path("/tmp/current.jpg"), "Monet — Water Lilies 1916", 2560, 1440
        )
        self.assertEqual(argv[0], "magick")
        self.assertEqual(argv[-1], "/tmp/current.jpg")  # written in place
        self.assertEqual(argv.count("/tmp/current.jpg"), 3)  # two reads + one write
        self.assertIn("2560x1440!", argv)  # gradient canvas at the display size
        self.assertIn("2560x1440", argv)  # painting fitted within it
        self.assertIn("-composite", argv)
        self.assertIn("-annotate", argv)
        self.assertIn(" Monet — Water Lilies 1916 ", argv)

    def test_wallpaper_command(self):
        self.assertEqual(
            commands.wallpaper_command("DP-1", Path("/tmp/current-DP-1.jpg")),
            ["swaymsg", "output", "DP-1", "bg", "/tmp/current-DP-1.jpg", "fill"],
        )

    def test_outputs_command(self):
        self.assertEqual(
            commands.outputs_command(),
            ["swaymsg", "-t", "get_outputs", "-r"],
        )

    def test_open_command(self):
        self.assertEqual(
            commands.open_command(Path("/tmp/preview.jpg")),
            ["xdg-open", "/tmp/preview.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
