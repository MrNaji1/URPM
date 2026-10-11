"""Themes, the stylesheet and the user's preferences.

A theme is a palette: a dict of colour names. The palette is injected as
@define-color lines ahead of style.css, so switching themes is just reloading one
CSS provider with a different palette. To add a theme, add an entry to THEMES.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib, Gtk

from urpm.i18n import N_

STYLE = Path(__file__).with_name("style.css")
CONFIG = Path(GLib.get_user_config_dir()) / "urpm" / "settings.json"


@dataclass
class ThemeDef:
    id: str
    name: str
    emoji: str
    description: str
    dark: bool                 # tells GTK to use dark variants of stock widgets
    colors: dict[str, str] = field(default_factory=dict)


# Shared pill / badge colours for the light theme.
_LIGHT_PILLS = {
    "blue_bg": "#dcefff", "blue_fg": "#2a6497", "lilac_bg": "#e9e1ff", "lilac_fg": "#5b45a8",
    "peach_bg": "#ffe6d6", "peach_fg": "#a4501f", "rose_bg": "#ffdfe4", "rose_fg": "#a3303f",
    "mint_bg": "#d9f5ea", "mint_fg": "#1f7a5a", "sky_bg": "#d6f3fb", "sky_fg": "#17657a",
    "lemon_bg": "#fff3c4", "lemon_fg": "#80620a", "gray_bg": "#eeeaf0", "gray_fg": "#5e5566",
    "update_bg": "#fff0c2", "update_fg": "#8a5a00", "danger": "#e5484d", "danger_fg": "#ffffff",
    "warning": "#b8580a",
    "avatar0_bg": "#ffd6e7", "avatar0_fg": "#a02d63", "avatar1_bg": "#e3dbff", "avatar1_fg": "#5439a8",
    "avatar2_bg": "#d4f3e5", "avatar2_fg": "#1f7453", "avatar3_bg": "#ffe5cc", "avatar3_fg": "#9a5316",
    "avatar4_bg": "#d7ecff", "avatar4_fg": "#2a6497", "avatar5_bg": "#fff3b8", "avatar5_fg": "#85680a",
}
_DARK_PILLS = {
    "blue_bg": "#1f3a52", "blue_fg": "#a8d4ff", "lilac_bg": "#342a55", "lilac_fg": "#cdbfff",
    "peach_bg": "#4a2e1f", "peach_fg": "#ffc59e", "rose_bg": "#4d2229", "rose_fg": "#ffb3bf",
    "mint_bg": "#1b4336", "mint_fg": "#a6ebcf", "sky_bg": "#173f4a", "sky_fg": "#9fe3f5",
    "lemon_bg": "#453a12", "lemon_fg": "#ffe08a", "gray_bg": "#342d3b", "gray_fg": "#cbbfd4",
    "update_bg": "#4a3a10", "update_fg": "#ffd66b", "danger": "#ff6b70", "danger_fg": "#2a0d0f",
    "warning": "#ffb366",
    "avatar0_bg": "#4a2a3a", "avatar0_fg": "#ffb3d1", "avatar1_bg": "#352c55", "avatar1_fg": "#cdbfff",
    "avatar2_bg": "#1f4034", "avatar2_fg": "#a6ebcf", "avatar3_bg": "#4a3220", "avatar3_fg": "#ffc59e",
    "avatar4_bg": "#1f3a52", "avatar4_fg": "#a8d4ff", "avatar5_bg": "#45391a", "avatar5_fg": "#ffe08a",
}

THEMES: dict[str, ThemeDef] = {}


def _register(theme: ThemeDef) -> ThemeDef:
    THEMES[theme.id] = theme
    return theme


COTTON_CANDY = _register(ThemeDef(
    "cotton-candy", "Cotton Candy", "🍬", N_("Soft pink and pastel. The original look."), dark=False,
    colors={
        "bg": "#fdf6fa", "surface": "#ffffff", "sidebar": "#f7edf6", "header": "#fbeff6",
        "fg": "#3d2f45", "dim": "#8c7b96", "border": "#efdfee", "chip": "#f4e6f1",
        "hover": "#fbeef6", "selected": "#fbd9ea", "accent": "#e86aa0", "accent_fg": "#ffffff",
        "track": "#f3e3ef", "shadow": "#b07a9e",
        "card_pink": "#ffe9f3", "card_lilac": "#efe8ff", "card_mint": "#e3f7ee",
        "card_peach": "#fff0e3",
        **_LIGHT_PILLS,
    }))

MIDNIGHT = _register(ThemeDef(
    "midnight", "Midnight", "🌙", N_("Dark plum with pink highlights. Easy on the eyes."), dark=True,
    colors={
        "bg": "#1d1922", "surface": "#26212d", "sidebar": "#211c28", "header": "#231e2a",
        "fg": "#f3eaf7", "dim": "#a998b4", "border": "#362e40", "chip": "#332b3c",
        "hover": "#2f2838", "selected": "#4a2c42", "accent": "#f28dbb", "accent_fg": "#2a1522",
        "track": "#3a3044", "shadow": "#000000",
        "card_pink": "#3a2533", "card_lilac": "#2e2942", "card_mint": "#20352d",
        "card_peach": "#3b2d24",
        **_DARK_PILLS,
    }))

GOTHAM = _register(ThemeDef(
    "gotham", "Gotham", "🦇", N_("Near-black with bat-signal yellow. For the dark knight."), dark=True,
    colors={
        "bg": "#0b0c10", "surface": "#15171c", "sidebar": "#0e1014", "header": "#0e1014",
        "fg": "#e9e7df", "dim": "#8e9099", "border": "#23262e", "chip": "#1d2027",
        "hover": "#1a1d24", "selected": "#3a3310", "accent": "#f5c518", "accent_fg": "#121212",
        "track": "#262a32", "shadow": "#000000",
        "card_pink": "#1c1a14", "card_lilac": "#17191f", "card_mint": "#151c19",
        "card_peach": "#1f1b11",
        **_DARK_PILLS,
        # yellow takes over the highlights
        "update_bg": "#3a3310", "update_fg": "#f5c518", "lemon_bg": "#3a3310", "lemon_fg": "#f5c518",
        "gray_bg": "#1d2027", "gray_fg": "#b9bcc5", "warning": "#f5c518",
        "danger": "#e5484d", "danger_fg": "#ffffff",
        "avatar0_bg": "#2a2a2e", "avatar0_fg": "#f5c518", "avatar1_bg": "#1f2430", "avatar1_fg": "#9fb7ff",
        "avatar2_bg": "#1c2a26", "avatar2_fg": "#8fe3c5", "avatar3_bg": "#2e2416", "avatar3_fg": "#f5c518",
        "avatar4_bg": "#23232a", "avatar4_fg": "#d7d9e0", "avatar5_bg": "#2a2716", "avatar5_fg": "#ffe08a",
    }))

MATCHA = _register(ThemeDef(
    "matcha", "Matcha", "🍵", N_("Calm sage green on soft white. Clean and minimal."), dark=False,
    colors={
        "bg": "#f4f7f1", "surface": "#ffffff", "sidebar": "#ecf1e7", "header": "#f0f4ec",
        "fg": "#22302a", "dim": "#6c7b72", "border": "#dde6d7", "chip": "#e6ede0",
        "hover": "#eef3ea", "selected": "#d5e9cf", "accent": "#3f7f4c", "accent_fg": "#ffffff",
        "track": "#e0e8da", "shadow": "#5f7d66",
        "card_pink": "#e6f2e1", "card_lilac": "#e8eef4", "card_mint": "#e0f1ec",
        "card_peach": "#f5f0e1",
        **_LIGHT_PILLS,
        "update_bg": "#f3ecd0", "update_fg": "#7a5c0c", "danger": "#d64545",
        "avatar0_bg": "#dcefd6", "avatar0_fg": "#2f6b3b", "avatar1_bg": "#e2e9f2", "avatar1_fg": "#3b5878",
        "avatar2_bg": "#d6eee7", "avatar2_fg": "#1f6b58", "avatar3_bg": "#f1e9d4", "avatar3_fg": "#7a5c1c",
        "avatar4_bg": "#e6ece2", "avatar4_fg": "#4d5d52", "avatar5_bg": "#eef0d2", "avatar5_fg": "#5f6418",
    }))

AURORA = _register(ThemeDef(
    "aurora", "Aurora", "🌌", N_("Deep navy with northern-lights teal and violet."), dark=True,
    colors={
        "bg": "#0e1120", "surface": "#161a2d", "sidebar": "#111527", "header": "#111527",
        "fg": "#e5e8f6", "dim": "#8890b5", "border": "#252b47", "chip": "#1e2440",
        "hover": "#1b2040", "selected": "#153b45", "accent": "#4fd1c5", "accent_fg": "#06201e",
        "track": "#252b47", "shadow": "#000000",
        "card_pink": "#1f1b38", "card_lilac": "#191f3c", "card_mint": "#112e30",
        "card_peach": "#261d36",
        **_DARK_PILLS,
        "update_bg": "#123a3a", "update_fg": "#6ee7d9", "gray_bg": "#1e2440", "gray_fg": "#b8bedc",
        "warning": "#f6b26b", "danger": "#f0606a", "danger_fg": "#200a0c",
        "avatar0_bg": "#13353a", "avatar0_fg": "#6ee7d9", "avatar1_bg": "#2a2350", "avatar1_fg": "#c4b5fd",
        "avatar2_bg": "#183049", "avatar2_fg": "#93c5fd", "avatar3_bg": "#33224a", "avatar3_fg": "#f0abfc",
        "avatar4_bg": "#1e2440", "avatar4_fg": "#cbd2f0", "avatar5_bg": "#16383a", "avatar5_fg": "#a7f3d0",
    }))

DEFAULT_LIGHT, DEFAULT_DARK = COTTON_CANDY.id, MIDNIGHT.id


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


def resolve_theme_id(prefs: dict, system_dark: bool) -> str:
    """Pick the theme to start with: saved choice, old light/dark setting, or the system."""
    chosen = prefs.get("theme")
    if chosen in THEMES:
        return chosen
    if "dark" in prefs:  # settings from URPM 1.x only had a light/dark switch
        return DEFAULT_DARK if prefs["dark"] else DEFAULT_LIGHT
    return DEFAULT_DARK if system_dark else DEFAULT_LIGHT


def build_css(theme: ThemeDef) -> str:
    """The full stylesheet for one theme: palette, swatch classes, then style.css."""
    colors = "".join(f"@define-color urpm_{k} {v};\n" for k, v in theme.colors.items())
    # Swatches let the theme picker show every theme's colours at once.
    swatches = "".join(
        f".swatch-{t.id}-{key} {{ background: {t.colors[key]}; }}\n"
        for t in THEMES.values() for key in ("bg", "surface", "accent", "fg"))
    return colors + swatches + STYLE.read_text()


class Theme:
    def __init__(self, display):
        self.current = THEMES[resolve_theme_id(load_prefs(), system_prefers_dark())]
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        # Build on GTK's own Adwaita so URPM looks the same on every desktop theme.
        Gtk.Settings.get_default().props.gtk_theme_name = "Adwaita"
        self.apply()

    @property
    def dark(self) -> bool:
        return self.current.dark

    def apply(self) -> None:
        css = build_css(self.current)
        if hasattr(self.provider, "load_from_string"):  # GTK 4.12+
            self.provider.load_from_string(css)
        else:
            try:
                self.provider.load_from_data(css, -1)
            except TypeError:  # older PyGObject / GTK 4.6-4.8 take bytes only
                self.provider.load_from_data(css.encode())
        Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = self.current.dark

    def set(self, theme_id: str) -> None:
        self.current = THEMES[theme_id]
        self.apply()
        prefs = load_prefs()
        prefs.pop("dark", None)
        prefs["theme"] = theme_id
        save_prefs(prefs)

    def toggle(self) -> None:
        """Quick switch between the default light and dark themes."""
        self.set(DEFAULT_LIGHT if self.current.dark else DEFAULT_DARK)
