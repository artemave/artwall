import json
import os
import tempfile
import unittest
from pathlib import Path

from artwall import desktop
from artwall.desktop import Output


def kscreen(*outputs):
    return json.dumps({"outputs": list(outputs)})


def kscreen_output(
    name, width, height, scale=1.0, rotation=1, pos=(0, 0), enabled=True, connected=True
):
    return {
        "name": name,
        "pos": {"x": pos[0], "y": pos[1]},
        "enabled": enabled,
        "connected": connected,
        "scale": scale,
        "rotation": rotation,
        "currentModeId": "2",
        "modes": [
            {"id": "1", "size": {"width": 640, "height": 480}},
            {"id": "2", "size": {"width": width, "height": height}},
        ],
    }


class ParseOutputs(unittest.TestCase):
    def test_returns_active_outputs_with_size_and_scale(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "scale": 1.0, "rect": {"x": 0, "y": 0}, "current_mode": {"width": 2560, "height": 1440}},  # noqa: E501
                {"name": "eDP-1", "active": True, "scale": 2.0, "rect": {"x": 2560, "y": 0}, "current_mode": {"width": 3840, "height": 2160}},  # noqa: E501
            ]
        )
        self.assertEqual(
            desktop.parse_outputs(raw),
            [Output("DP-1", 2560, 1440, 1.0, 0, 0), Output("eDP-1", 3840, 2160, 2.0, 2560, 0)],
        )

    def test_skips_inactive_outputs(self):
        raw = json.dumps(
            [
                {"name": "DP-1", "active": True, "scale": 1.0, "rect": {"x": 0, "y": 0}, "current_mode": {"width": 1920, "height": 1080}},  # noqa: E501
                {"name": "DP-2", "active": False, "scale": 1.0, "rect": {"x": 0, "y": 0}, "current_mode": {"width": 1920, "height": 1080}},  # noqa: E501
            ]
        )
        self.assertEqual(desktop.parse_outputs(raw), [Output("DP-1", 1920, 1080, 1.0)])


class ParseKscreenOutputs(unittest.TestCase):
    def test_returns_enabled_outputs_at_their_current_mode(self):
        raw = kscreen(
            kscreen_output("eDP-1", 2560, 1440, scale=1.75),
            kscreen_output("DP-1", 1920, 1080, pos=(1463, 0)),
        )
        self.assertEqual(
            desktop.parse_kscreen_outputs(raw),
            [Output("eDP-1", 2560, 1440, 1.75, 0, 0), Output("DP-1", 1920, 1080, 1.0, 1463, 0)],
        )

    def test_skips_disabled_and_disconnected_outputs(self):
        raw = kscreen(
            kscreen_output("DP-1", 1920, 1080),
            kscreen_output("DP-2", 1920, 1080, enabled=False),
            kscreen_output("DP-3", 1920, 1080, connected=False),
        )
        self.assertEqual(desktop.parse_kscreen_outputs(raw), [Output("DP-1", 1920, 1080)])

    def test_a_display_on_its_side_swaps_width_and_height(self):
        raw = kscreen(
            kscreen_output("DP-1", 2560, 1440, rotation=2),
            kscreen_output("DP-2", 2560, 1440, rotation=4),
            kscreen_output("DP-3", 2560, 1440, rotation=8),
        )
        self.assertEqual(
            desktop.parse_kscreen_outputs(raw),
            [Output("DP-1", 1440, 2560), Output("DP-2", 2560, 1440), Output("DP-3", 1440, 2560)],
        )


class FontTests(unittest.TestCase):
    def test_parse_font_name_splits_family_and_size(self):
        self.assertEqual(desktop.parse_font_name("Adwaita Sans 11"), ("Adwaita Sans", 11))

    def test_parse_kde_font_reads_family_and_size_from_a_qfont(self):
        self.assertEqual(
            desktop.parse_kde_font("Noto Sans,12,-1,5,400,0,0,0,0,0,0,0,0,0,0,1,,0,0"),
            ("Noto Sans", 12),
        )

    def test_parse_kde_font_rounds_a_fractional_size(self):
        self.assertEqual(desktop.parse_kde_font("Inter,10.5,-1,5,50,0,0,0,0,0"), ("Inter", 10))
        self.assertEqual(desktop.parse_kde_font("Inter,11.5,-1,5,50,0,0,0,0,0"), ("Inter", 12))


class Detect(unittest.TestCase):
    def test_sway(self):
        self.assertIs(desktop.detect({"SWAYSOCK": "/run/sway.sock"}), desktop.SWAY)

    def test_plasma(self):
        self.assertIs(desktop.detect({"XDG_CURRENT_DESKTOP": "KDE"}), desktop.PLASMA)

    def test_plasma_among_several_desktop_names(self):
        self.assertIs(desktop.detect({"XDG_CURRENT_DESKTOP": "foo:KDE"}), desktop.PLASMA)

    def test_anything_else_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported desktop"):
            desktop.detect({"XDG_CURRENT_DESKTOP": "GNOME"})


class PlasmaWallpaper(unittest.TestCase):
    def test_a_rewritten_image_gets_a_new_url(self):
        image = Path(tempfile.mkdtemp()) / "current-DP-1.jpg"
        image.write_bytes(b"first")
        os.utime(image, ns=(1, 1_000))
        first = desktop.plasma_wallpaper("DP-1", image)
        image.write_bytes(b"second")
        os.utime(image, ns=(1, 2_000))
        second = desktop.plasma_wallpaper("DP-1", image)

        self.assertIn(f'"{image.as_uri()}?v=1000"', first[-1])
        self.assertIn(f'"{image.as_uri()}?v=2000"', second[-1])


if __name__ == "__main__":
    unittest.main()
