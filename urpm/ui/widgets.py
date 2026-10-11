"""Small helpers shared by the window, panels and dialogs."""

from __future__ import annotations

import time
import zlib

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

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
