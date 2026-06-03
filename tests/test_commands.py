import unittest
from pathlib import Path

from artwall import commands


class Commands(unittest.TestCase):
    def test_compose_command(self):
        argv = commands.compose_command(
            Path("/tmp/current.jpg"), "Monet — Water Lilies 1916", 2560, 1440, font_size=30
        )
        self.assertEqual(argv[0], "magick")
        self.assertEqual(argv[-1], "/tmp/current.jpg")  # written in place
        self.assertEqual(argv.count("/tmp/current.jpg"), 3)  # two reads + one write
        self.assertIn("2560x1440!", argv)  # gradient canvas at the display size
        self.assertIn("2560x1440", argv)  # painting fitted within it
        self.assertIn("-composite", argv)
        self.assertEqual(argv[argv.index("-pointsize") + 1], "30")  # configured font size
        self.assertIn("SouthEast", argv)  # default corner -> SouthEast gravity
        self.assertEqual(argv[argv.index("-annotate") + 1], "+24+64")  # default pixel inset
        self.assertIn("\u00a0Monet — Water Lilies 1916 ", argv)  # nbsp pad both sides

    def test_compose_command_corner_and_padding(self):
        argv = commands.compose_command(
            Path("/tmp/current.jpg"), "x", 1000, 1000, corner="top-left", pad_x=40, pad_y=80
        )
        self.assertIn("NorthWest", argv)  # top-left -> NorthWest gravity
        self.assertEqual(argv[argv.index("-annotate") + 1], "+40+80")  # absolute pixel inset

    def test_compose_command_font(self):
        with_font = commands.compose_command(
            Path("/tmp/x.jpg"), "x", 100, 100, font="/fonts/Test.ttf"
        )
        self.assertEqual(with_font[with_font.index("-font") + 1], "/fonts/Test.ttf")
        # default: no -font, so magick uses its built-in default
        without = commands.compose_command(Path("/tmp/x.jpg"), "x", 100, 100)
        self.assertNotIn("-font", without)

    def test_compose_command_without_caption(self):
        # link-overlay mode: text=None -> compose the painting with no caption
        argv = commands.compose_command(Path("/tmp/current.jpg"), None, 1920, 1080)
        self.assertIn("-composite", argv)  # painting still composed onto the canvas
        self.assertNotIn("-annotate", argv)  # but no burned caption
        self.assertNotIn("-undercolor", argv)
        self.assertEqual(argv[-1], "/tmp/current.jpg")  # still written in place

    def test_compose_command_unknown_corner_fails_loudly(self):
        with self.assertRaises(KeyError):
            commands.compose_command(Path("/tmp/x.jpg"), "x", 100, 100, corner="middle")

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
