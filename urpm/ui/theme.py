"""Colours, the stylesheet and the light/dark switch.

The palette is injected as @define-color lines ahead of style.css, so switching
themes is just reloading one provider with a different palette.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from gi.repository import GLib, Gtk

STYLE = Path(__file__).with_name("style.css")
CONFIG = Path(GLib.get_user_config_dir()) / "urpm" / "settings.json"

LIGHT = {
    "bg": "#fdf6fa", "surface": "#ffffff", "sidebar": "#f7edf6", "header": "#fbeff6",
    "fg": "#3d2f45", "dim": "#8c7b96", "border": "#efdfee", "chip": "#f4e6f1",
    "hover": "#fbeef6", "selected": "#fbd9ea", "accent": "#e86aa0", "accent_fg": "#ffffff",
    "track": "#f3e3ef", "shadow": "#b07a9e",
    "blue_bg": "#dcefff", "blue_fg": "#2a6497", "lilac_bg": "#e9e1ff", "lilac_fg": "#5b45a8",
    "peach_bg": "#ffe6d6", "peach_fg": "#a4501f", "rose_bg": "#ffdfe4", "rose_fg": "#a3303f",
    "mint_bg": "#d9f5ea", "mint_fg": "#1f7a5a", "sky_bg": "#d6f3fb", "sky_fg": "#17657a",
    "lemon_bg": "#fff3c4", "lemon_fg": "#80620a", "gray_bg": "#eeeaf0", "gray_fg": "#5e5566",
    "update_bg": "#fff0c2", "update_fg": "#8a5a00", "danger": "#e5484d", "danger_fg": "#ffffff",
    "warning": "#b8580a",
    "card_pink": "#ffe9f3", "card_lilac": "#efe8ff", "card_mint": "#e3f7ee", "card_peach": "#fff0e3",
}
DARK = {
    "bg": "#1d1922", "surface": "#26212d", "sidebar": "#211c28", "header": "#231e2a",
    "fg": "#f3eaf7", "dim": "#a998b4", "border": "#362e40", "chip": "#332b3c",
    "hover": "#2f2838", "selected": "#4a2c42", "accent": "#f28dbb", "accent_fg": "#2a1522",
    "track": "#3a3044", "shadow": "#000000",
    "blue_bg": "#1f3a52", "blue_fg": "#a8d4ff", "lilac_bg": "#342a55", "lilac_fg": "#cdbfff",
    "peach_bg": "#4a2e1f", "peach_fg": "#ffc59e", "rose_bg": "#4d2229", "rose_fg": "#ffb3bf",
    "mint_bg": "#1b4336", "mint_fg": "#a6ebcf", "sky_bg": "#173f4a", "sky_fg": "#9fe3f5",
    "lemon_bg": "#453a12", "lemon_fg": "#ffe08a", "gray_bg": "#342d3b", "gray_fg": "#cbbfd4",
    "update_bg": "#4a3a10", "update_fg": "#ffd66b", "danger": "#ff6b70", "danger_fg": "#2a0d0f",
    "warning": "#ffb366",
    "card_pink": "#3a2533", "card_lilac": "#2e2942", "card_mint": "#20352d", "card_peach": "#3b2d24",
}


def load_prefs() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict) -> None:
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(prefs, indent=2))
    except OSError:
        pass


def system_prefers_dark() -> bool:
    settings = Gtk.Settings.get_default()
    theme = os.environ.get("GTK_THEME") or settings.props.gtk_theme_name or ""
    return settings.props.gtk_application_prefer_dark_theme or "dark" in theme.lower()


class Theme:
    def __init__(self, display):
        prefs = load_prefs()
        self.dark = prefs.get("dark", system_prefers_dark())
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        # Build on GTK's own Adwaita so URPM looks the same on every desktop theme.
        Gtk.Settings.get_default().props.gtk_theme_name = "Adwaita"
        self.apply()

    def apply(self) -> None:
        palette = DARK if self.dark else LIGHT
        colors = "".join(f"@define-color urpm_{k} {v};\n" for k, v in palette.items())
        css = colors + STYLE.read_text()
        if hasattr(self.provider, "load_from_string"):  # GTK 4.12+
            self.provider.load_from_string(css)
        else:
            try:
                self.provider.load_from_data(css, -1)
            except TypeError:  # older PyGObject / GTK 4.6-4.8 take bytes only
                self.provider.load_from_data(css.encode())
        Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = self.dark

    def toggle(self) -> None:
        self.dark = not self.dark
        self.apply()
        save_prefs({**load_prefs(), "dark": self.dark})
