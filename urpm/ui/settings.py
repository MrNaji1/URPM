"""The Settings window. Today it has one section, Appearance, with the theme picker."""

from __future__ import annotations

from gi.repository import Gtk

from urpm.ui.theme import THEMES, ThemeDef


def label(text: str = "", *classes: str, **props) -> Gtk.Label:
    return Gtk.Label(label=text, css_classes=list(classes), **props)


def theme_preview(theme: ThemeDef) -> Gtk.Widget:
    """A tiny mock-up of the window drawn in the theme's own colours."""
    def swatch(key: str, *classes: str, **props) -> Gtk.Box:
        return Gtk.Box(css_classes=[f"swatch-{theme.id}-{key}", *classes], **props)

    preview = swatch("bg", "theme-preview")
    preview.set_orientation(Gtk.Orientation.VERTICAL)
    preview.set_spacing(6)
    card = swatch("surface", "mini-card", orientation=Gtk.Orientation.VERTICAL, spacing=5)
    card.set_margin_top(0)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin_start=8,
                    margin_top=8, margin_bottom=8)
    inner.append(swatch("fg", "mini-text", halign=Gtk.Align.START))
    inner.append(swatch("accent", "mini-accent", halign=Gtk.Align.START))
    card.append(inner)
    preview.append(card)
    return preview


class SettingsWindow(Gtk.Window):
    def __init__(self, parent: Gtk.Window, theme, on_theme_changed=None):
        super().__init__(transient_for=parent, modal=True, title="Settings",
                         css_classes=["urpm", "dialog-window"], default_width=640,
                         resizable=False)
        self._theme = theme
        self._on_theme_changed = on_theme_changed
        self._cards: dict[str, Gtk.Button] = {}

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       css_classes=["dialog-outer"])
        body.append(label("APPEARANCE", "settings-heading", xalign=0))
        body.append(label("Theme", "placeholder-title", xalign=0))
        body.append(label("Pick how URPM looks. Your choice is saved.", "dim", xalign=0))

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        for i, item in enumerate(THEMES.values()):
            grid.attach(self._card(item), i % 3, i // 3, 1, 1)
        body.append(grid)

        close = Gtk.Button(label="Done", css_classes=["cute"], halign=Gtk.Align.END,
                           margin_top=6)
        close.connect("clicked", lambda _b: self.close())
        body.append(close)
        self.set_child(body)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(lambda *_: self.close() or True)))
        self.add_controller(escape)
        self._sync()

    def _card(self, item: ThemeDef) -> Gtk.Button:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(theme_preview(item))
        title = Gtk.Box(spacing=6)
        title.append(label(f"{item.emoji} {item.name}", "theme-name", xalign=0, hexpand=True))
        check = label("✓", "theme-check")
        title.append(check)
        box.append(title)
        box.append(label(item.description, "dim", "small", xalign=0, wrap=True,
                         max_width_chars=18))
        button = Gtk.Button(child=box, css_classes=["theme-card"],
                            tooltip_text=f"Use the {item.name} theme")
        button.check = check
        button.connect("clicked", lambda _b: self.choose(item.id))
        self._cards[item.id] = button
        return button

    def choose(self, theme_id: str) -> None:
        if theme_id != self._theme.current.id:
            self._theme.set(theme_id)
            if self._on_theme_changed:
                self._on_theme_changed()
        self._sync()

    def _sync(self) -> None:
        for theme_id, button in self._cards.items():
            active = theme_id == self._theme.current.id
            button.check.set_visible(active)
            if active:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")
