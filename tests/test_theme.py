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
        self.assertEqual(list(theme.THEMES),
                         ["cotton-candy", "midnight", "gotham", "matcha", "aurora"])
        self.assertFalse(theme.THEMES["matcha"].dark)
        self.assertTrue(theme.THEMES["aurora"].dark)
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

    def test_text_is_readable(self):
        """Text and button labels keep enough contrast with their backgrounds (WCAG)."""
        def luminance(hex_color):
            rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        def contrast(a, b):
            hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        # Body text gets the strict 7:1 / 4.5:1; bold button labels and secondary text 3:1.
        pairs = [("fg", "bg", 7), ("fg", "surface", 7), ("dim", "surface", 3),
                 ("update_fg", "update_bg", 4.5), ("accent_fg", "accent", 3),
                 ("danger_fg", "danger", 3)]
        for item in theme.THEMES.values():
            for fg, bg, minimum in pairs:
                ratio = round(contrast(item.colors[fg], item.colors[bg]), 1)
                self.assertGreaterEqual(ratio, minimum,
                                        f"{item.name}: {fg} on {bg} is only {ratio:.1f}:1")

    def test_css_contains_palette_and_swatches(self):
        css = theme.build_css(theme.THEMES["gotham"])
        self.assertIn("@define-color urpm_accent #f5c518;", css)
        for item in theme.THEMES.values():  # the picker previews every theme
            self.assertIn(f".swatch-{item.id}-accent", css)
        self.assertNotIn("#ffd6e7", css.split("/* URPM stylesheet")[1])  # no hardcoded avatars


if __name__ == "__main__":
    unittest.main()
