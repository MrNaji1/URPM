"""The Settings window: Appearance (theme picker) and Updates (background checks)."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from urpm import i18n, schedule  # noqa: E402
from urpm.i18n import _  # noqa: E402
from urpm.ui import theme as theme_prefs  # noqa: E402
from urpm.ui.theme import THEMES, ThemeDef  # noqa: E402
from urpm.ui.widgets import label  # noqa: E402


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
        super().__init__(transient_for=parent, modal=True, title=_("Settings"),
                         css_classes=["urpm", "dialog-window"], default_width=640,
                         resizable=False)
        self._theme = theme
        self._on_theme_changed = on_theme_changed
        self._cards: dict[str, Gtk.Button] = {}

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       css_classes=["dialog-outer"])
        body.append(label(_("APPEARANCE"), "settings-heading", xalign=0))
        body.append(label(_("Theme"), "placeholder-title", xalign=0))
        body.append(label(_("Pick how URPM looks. Your choice is saved."), "dim", xalign=0))

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        for i, item in enumerate(THEMES.values()):
            grid.attach(self._card(item), i % 3, i // 3, 1, 1)
        body.append(grid)
        body.append(self._language_section())
        body.append(self._updates_section())

        close = Gtk.Button(label=_("Done"), css_classes=["cute"], halign=Gtk.Align.END,
                           margin_top=6)
        close.connect("clicked", lambda _b: self.close())
        body.append(close)
        self.set_child(body)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(lambda *_args: self.close() or True)))
        self.add_controller(escape)
        self._sync()

    def _language_section(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8)
        section.append(label(_("LANGUAGE"), "settings-heading", xalign=0))
        card = Gtk.Box(spacing=12, css_classes=["toggle-card"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label(_("Language"), "title", xalign=0))
        self.language_note = label(_("Used the next time URPM starts."), "dim", "small",
                                   xalign=0, wrap=True, max_width_chars=50)
        text.append(self.language_note)
        card.append(text)
        self.language_codes = ["", *i18n.available()]
        system = i18n.NATIVE_NAMES.get(i18n.match(i18n.system_languages(), i18n.available()),
                                       "English")
        names = [_("System ({language})").format(language=system)] + [
            i18n.NATIVE_NAMES.get(code, code) for code in self.language_codes[1:]]
        saved = theme_prefs.load_prefs().get("language", "")
        self.language_dropdown = Gtk.DropDown.new_from_strings(names)
        self.language_dropdown.set_valign(Gtk.Align.CENTER)
        if saved in self.language_codes:
            self.language_dropdown.set_selected(self.language_codes.index(saved))
        self.language_dropdown.update_property([Gtk.AccessibleProperty.LABEL], [_("Language")])
        self.language_dropdown.connect("notify::selected", self._on_language_changed)
        card.append(self.language_dropdown)
        section.append(card)
        return section

    def _on_language_changed(self, dropdown, _pspec) -> None:
        code = self.language_codes[dropdown.get_selected()]
        prefs = theme_prefs.load_prefs()
        prefs["language"] = code
        theme_prefs.save_prefs(prefs)
        self.language_note.set_label(_("✅ Saved. Restart URPM to use it."))

    def _updates_section(self) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8)
        section.append(label(_("UPDATES"), "settings-heading", xalign=0))
        card = Gtk.Box(spacing=12, css_classes=["toggle-card"])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(label(_("Check for updates in the background"), "title", xalign=0))
        text.append(label(_("Every 6 hours, a quick check that shows a notification when new "
                            "updates appear. URPM itself doesn't stay open."), "dim", "small",
                          xalign=0, wrap=True, max_width_chars=60))
        card.append(text)
        self.background_switch = Gtk.Switch(valign=Gtk.Align.CENTER,
                                            active=schedule.is_enabled())
        self.background_switch.update_property([Gtk.AccessibleProperty.LABEL],
                                               [_("Check for updates in the background")])
        self._switch_handler = self.background_switch.connect("notify::active",
                                                              self._on_background_toggled)
        card.append(self.background_switch)
        section.append(card)
        self.background_status = label("", "dim", "small", xalign=0, wrap=True, visible=False)
        section.append(self.background_status)
        return section

    def _on_background_toggled(self, switch, _pspec) -> None:
        enable = switch.get_active()
        switch.set_sensitive(False)

        def worker():
            try:
                how = schedule.enable() if enable else (schedule.disable() or "off")
                error = None
            except Exception as exc:  # e.g. systemctl refused
                how, error = None, str(exc)
            GLib.idle_add(self._background_done, enable, how, error)

        threading.Thread(target=worker, daemon=True).start()

    def _background_done(self, enable: bool, how: str | None, error: str | None) -> bool:
        switch = self.background_switch
        switch.set_sensitive(True)
        if error:
            with switch.handler_block(self._switch_handler):  # put it back, quietly
                switch.set_active(not enable)
            message = _("😿 Couldn't change it: {error}").format(error=error)
        elif how == "systemd":
            message = _("✅ On: checks 15 minutes after you log in, then every 6 hours.")
        elif how == "autostart":
            message = _("✅ On: checks 10 minutes after you log in.")
        else:
            message = _("Background checks are off.")
        self.background_status.set_label(message)
        self.background_status.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _card(self, item: ThemeDef) -> Gtk.Button:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(theme_preview(item))
        title = Gtk.Box(spacing=6)
        title.append(label(f"{item.emoji} {item.name}", "theme-name", xalign=0, hexpand=True))
        check = label("✓", "theme-check")
        title.append(check)
        box.append(title)
        box.append(label(_(item.description), "dim", "small", xalign=0, wrap=True,
                         max_width_chars=18))
        button = Gtk.Button(child=box, css_classes=["theme-card"],
                            tooltip_text=_("Use the {name} theme").format(name=item.name))
        button.check = check
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [_("{name} theme").format(name=item.name)])
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
