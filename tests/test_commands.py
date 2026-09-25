import unittest
from pathlib import Path

from artwall import commands


class Commands(unittest.TestCase):
    def test_compose_command(self):
        argv = commands.compose_command(Path("/tmp/current.jpg"), 2560, 1440)
        self.assertEqual(argv[0], "magick")
        self.assertEqual(argv[-1], "/tmp/current.jpg")  # written in place
        self.assertEqual(argv.count("/tmp/current.jpg"), 3)  # two reads + one write
        self.assertIn("2560x1440!", argv)  # gradient canvas at the display size
        self.assertIn("2560x1440", argv)  # painting fitted within it
        self.assertIn("-composite", argv)

    def test_thumbnail_command(self):
        argv = commands.thumbnail_command(Path("/a/Q1.jpg"), Path("/b/Q1.jpg"), 1200)
        self.assertEqual(argv[:2], ["magick", "/a/Q1.jpg"])
        self.assertEqual(argv[-1], "/b/Q1.jpg")  # a new file; the archive is untouched
        # `>` is what stops a scan smaller than the box being upscaled into a
        # blurrier, *larger* file than the one it came from
        self.assertEqual(argv[argv.index("-resize") + 1], "1200x1200>")
        self.assertIn("-strip", argv)  # nothing on a public site needs the EXIF
        self.assertEqual(argv[argv.index("-interlace") + 1], "Plane")  # progressive

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

    def test_kscreen_outputs_command(self):
        self.assertEqual(commands.kscreen_outputs_command(), ["kscreen-doctor", "-j"])

    def test_plasma_wallpaper_command_evaluates_a_desktop_script(self):
        argv = commands.plasma_wallpaper_command("DP-1", Path("/tmp/current-DP-1.jpg"), 42)

        self.assertEqual(
            argv[:-1],
            [
                "gdbus", "call", "--session",
                "--dest", "org.kde.plasmashell",
                "--object-path", "/PlasmaShell",
                "--method", "org.kde.PlasmaShell.evaluateScript",
            ],
        )
        script = argv[-1]
        self.assertIn('screenForConnector("DP-1")', script)
        self.assertIn('d.writeConfig("Image", "file:///tmp/current-DP-1.jpg?v=42")', script)

    def test_plasma_wallpaper_command_quotes_its_values_as_js_strings(self):
        script = commands.plasma_wallpaper_command('a"b', Path("/tmp/x y.jpg"), 1)[-1]

        self.assertIn(r'screenForConnector("a\"b")', script)
        self.assertIn('"file:///tmp/x%20y.jpg?v=1"', script)

    def test_open_command(self):
        self.assertEqual(
            commands.open_command(Path("/tmp/preview.jpg")),
            ["xdg-open", "/tmp/preview.jpg"],
        )


if __name__ == "__main__":
    unittest.main()
