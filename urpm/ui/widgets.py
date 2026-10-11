"""Small helpers shared by the window, panels and dialogs."""

from __future__ import annotations

import os
import time
import zlib

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from urpm.sources import Source  # noqa: E402

# Pill colour per source (see the pill-* rules in style.css).
SOURCE_COLORS = {
    "apt": "blue", "rpm": "rose", "pacman": "sky", "xbps": "mint", "apk": "sky",
    "portage": "lilac", "eopkg": "lemon", "flatpak": "lilac", "snap": "peach",
    "appimage": "lemon", "nix": "sky", "brew": "peach", "pip": "blue", "pipx": "mint",
    "npm": "rose", "cargo": "peach",
}


def label(text: str = "", *classes: str, **props) -> Gtk.Label:
    return Gtk.Label(label=text, css_classes=list(classes), **props)


def clear(box: Gtk.Widget) -> None:
    while child := box.get_first_child():
        box.remove(child)


def accessible_name(widget: Gtk.Widget, name: str) -> None:
    """Give an icon-only control a name that screen readers can announce."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [name])


def avatar_class(name: str) -> str:
    return f"avatar-{zlib.crc32(name.encode()) % 6}"


def pill_classes(source: Source) -> list[str]:
    return ["pill", f"pill-{SOURCE_COLORS.get(source.id, 'gray')}"]


def short_label(source: Source) -> str:
    return source.label.split(" (")[0]


def format_date(ts: float | None) -> str:
    return time.strftime("%b %d, %Y", time.localtime(ts)) if ts else "—"


_textures: dict[str, Gdk.Texture | None] = {}


def show_icon(image: Gtk.Image, icon: str) -> bool:
    """Show a package icon (a theme icon name or an image file) in `image`.

    Returns False when there's nothing to show, so the caller can fall back to the letter
    avatar. File icons are loaded once and cached; theme lookups are cheap.
    """
    if not icon:
        return False
    if os.path.isabs(icon):
        if icon not in _textures:
            try:
                _textures[icon] = Gdk.Texture.new_from_filename(icon)
            except GLib.Error:
                _textures[icon] = None
        texture = _textures[icon]
        if texture is None:
            return False
        image.set_from_paintable(texture)
        return True
    display = image.get_display() or Gdk.Display.get_default()
    if display is None or not Gtk.IconTheme.get_for_display(display).has_icon(icon):
        return False
    image.set_from_icon_name(icon)
    return True
