"""Theme definitions, choosing a theme at startup, and the generated stylesheet.

No windows are opened here; only the GTK module is imported.
"""

import re
import unittest

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from urpm.ui import theme
except (ImportError, ValueError):
    theme = None

STYLE_COLORS = re.compile(r"@urpm_(\w+)")


@unittest.skipIf(theme is None, "needs PyGObject with GTK 4")
class ThemeTests(unittest.TestCase):
    def test_every_theme_defines_every_colour_the_stylesheet_uses(self):
        used = set(STYLE_COLORS.findall(theme.STYLE.read_text()))
        self.assertGreater(len(used), 20)
        for item in theme.THEMES.values():
            missing = used - set(item.colors)
            self.assertFalse(missing, f"{item.name} is missing {sorted(missing)}")

    def test_colours_are_valid_hex(self):
        for item in theme.THEMES.values():
            for key, value in item.colors.items():
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", f"{item.id}.{key}")

    def test_builtin_themes(self):
        self.assertEqual(list(theme.THEMES), ["cotton-candy", "midnight", "gotham"])
        gotham = theme.THEMES["gotham"]
        self.assertTrue(gotham.dark)
        self.assertEqual(gotham.colors["accent"], "#f5c518")  # bat-signal yellow
        self.assertLess(int(gotham.colors["bg"][1:3], 16), 0x20)  # near-black

    def test_startup_theme(self):
        resolve = theme.resolve_theme_id
        self.assertEqual(resolve({"theme": "gotham"}, system_dark=False), "gotham")
        self.assertEqual(resolve({"theme": "gone"}, system_dark=False), "cotton-candy")
        # Settings saved by URPM 1.x only had "dark": true/false.
        self.assertEqual(resolve({"dark": True}, system_dark=False), "midnight")
        self.assertEqual(resolve({"dark": False}, system_dark=True), "cotton-candy")
        self.assertEqual(resolve({}, system_dark=True), "midnight")
        self.assertEqual(resolve({}, system_dark=False), "cotton-candy")

    def test_css_contains_palette_and_swatches(self):
        css = theme.build_css(theme.THEMES["gotham"])
        self.assertIn("@define-color urpm_accent #f5c518;", css)
        for item in theme.THEMES.values():  # the picker previews every theme
            self.assertIn(f".swatch-{item.id}-accent", css)
        self.assertNotIn("#ffd6e7", css.split("/* URPM stylesheet")[1])  # no hardcoded avatars


if __name__ == "__main__":
    unittest.main()
