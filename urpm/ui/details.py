"""The details panel on the right: everything about the selected package."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from urpm.i18n import _, ngettext  # noqa: E402
from urpm.sources import human_size  # noqa: E402
from urpm.sources.base import dir_size  # noqa: E402
from urpm.ui.widgets import (avatar_class, clear, format_date, label, pill_classes,  # noqa: E402
                             short_label, show_icon)

MAX_FILES_SHOWN = 3000
MAX_NEEDED_BY_SHOWN = 40


class DetailsPanel(Gtk.Stack):
    """Shows one package. The buttons call back into the window, which owns the actions."""

    def __init__(self, on_open, on_update, on_remove, on_copy_command, on_copy_name,
                 on_hold=None):
        super().__init__(css_classes=["details"], width_request=350, hexpand=False,
                         transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.item = None
        self.launcher: list[str] | None = None
        self._token = 0  # bumps on every new package, so late results are ignored

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, margin_start=24,
                        margin_end=24)
        empty.append(label("✨", "big-emoji"))
        empty.append(label(_("Pick a package"), "placeholder-title"))
        empty.append(label(_("Click any row to see its story"), "dim"))
        self.add_named(empty, "empty")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        css_classes=["details-inner"])
        top = Gtk.Box(spacing=14)
        self.icon = Gtk.Image(pixel_size=64, valign=Gtk.Align.START, css_classes=["app-icon"])
        top.append(self.icon)
        self.avatar = label("", "avatar", "big", valign=Gtk.Align.START)
        top.append(self.avatar)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         hexpand=True)
        self.name_label = label("", "detail-title", xalign=0, wrap=True, selectable=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.version_label = label("", "dim", "mono", xalign=0, wrap=True, selectable=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR)
        titles.append(self.name_label)
        titles.append(self.version_label)
        top.append(titles)
        inner.append(top)

        self.badges = Gtk.Box(spacing=6)
        inner.append(self.badges)
        self.summary = label("", xalign=0, wrap=True)
        inner.append(self.summary)

        actions = Gtk.Box(spacing=8, margin_top=4)
        self.open_button = self._button(actions, _("🚀 Open"), "cute",
                                        lambda: on_open(self.launcher))
        self.update_button = self._button(actions, _("✨ Update"), "cute", on_update)
        self.remove_button = self._button(actions, _("🗑️ Remove"), "danger", on_remove)
        inner.append(actions)

        copies = Gtk.Box(spacing=8)
        self._button(copies, _("Copy uninstall command"), "soft", on_copy_command, small=True)
        self._button(copies, _("Copy name"), "soft", on_copy_name, small=True)
        inner.append(copies)
        self.hold_button = self._button(inner, _("📌 Keep this version"), "soft",
                                        lambda: on_hold and on_hold(), small=True)
        self.hold_button.set_halign(Gtk.Align.START)

        self.info = Gtk.Grid(column_spacing=14, row_spacing=6, margin_top=6)
        inner.append(self.info)

        inner.append(label(_("ABOUT"), "section-heading", xalign=0))
        self.spinner = Gtk.Spinner(halign=Gtk.Align.START)
        inner.append(self.spinner)
        self.description = label("", xalign=0, wrap=True, selectable=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR)
        inner.append(self.description)
        self.more_heading = label(_("MORE"), "section-heading", xalign=0)
        inner.append(self.more_heading)
        self.more = Gtk.Grid(column_spacing=14, row_spacing=6)
        inner.append(self.more)

        self.needed_heading = label(_("NEEDED BY"), "section-heading", xalign=0)
        inner.append(self.needed_heading)
        self.needed_by = label("", xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        inner.append(self.needed_by)

        self.permissions_heading = label(_("PERMISSIONS"), "section-heading", xalign=0)
        inner.append(self.permissions_heading)
        self.permissions_summary = label("", xalign=0, wrap=True)
        inner.append(self.permissions_summary)
        self.permissions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.append(self.permissions)
        self.data_row = Gtk.Box(spacing=8)
        self.data_label = label("", "dim", "small", xalign=0, wrap=True, hexpand=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.data_row.append(self.data_label)
        self.data_button = self._button(self.data_row, _("Open folder"), "soft",
                                        self._open_data_folder, small=True)
        self.data_button.set_valign(Gtk.Align.CENTER)
        inner.append(self.data_row)
        self.data_folder = None

        inner.append(label(_("FILES"), "section-heading", xalign=0))
        self.files_button = self._button(inner, _("Show files"), "soft", self.load_files,
                                         small=True)
        self.files_button.set_halign(Gtk.Align.START)
        self.files_buffer = Gtk.TextBuffer()
        files_view = Gtk.TextView(buffer=self.files_buffer, editable=False, monospace=True,
                                  cursor_visible=False, css_classes=["log"])
        self.files_scroller = Gtk.ScrolledWindow(child=files_view, min_content_height=240,
                                                 css_classes=["log-scroller"], visible=False)
        inner.append(self.files_scroller)

        self.add_named(Gtk.ScrolledWindow(child=inner, vexpand=True,
                                          hscrollbar_policy=Gtk.PolicyType.NEVER), "package")

    @staticmethod
    def _button(box, text, css, callback, small=False) -> Gtk.Button:
        button = Gtk.Button(label=text, css_classes=[css, "small-button"] if small else [css])
        button.connect("clicked", lambda _b: callback())
        box.append(button)
        return button

    # ---------------------------------------------------------------- showing a package

    def show_package(self, item) -> None:
        self.item = item
        self._token += 1
        if item is None:
            self.set_visible_child_name("empty")
            return
        pkg, src = item.pkg, item.source
        self.set_visible_child_name("package")

        has_icon = show_icon(self.icon, pkg.icon)
        self.icon.set_visible(has_icon)
        self.avatar.set_visible(not has_icon)
        self.avatar.set_label(pkg.name[:1].upper())
        self.avatar.set_css_classes(["avatar", "big", avatar_class(pkg.name)])
        self.name_label.set_label(pkg.name)
        self.version_label.set_label(f"{pkg.version}  →  {pkg.update}" if pkg.update
                                     else pkg.version)
        self.summary.set_label(pkg.summary)
        self.summary.set_visible(bool(pkg.summary))

        clear(self.badges)
        self.badges.append(label(f"{src.emoji} {short_label(src)}", *pill_classes(src)))
        if src.tracks_explicit:
            self.badges.append(label(_("💖 yours"), "pill", "pill-mine") if pkg.explicit
                               else label(_("🔗 dependency"), "pill", "pill-dep"))
        if pkg.held:
            self.badges.append(label(_("📌 held"), "pill", "pill-held"))
        if pkg.update and pkg.security:
            self.badges.append(label(_("🛡 security update"), "pill", "pill-security"))
        elif pkg.update:
            self.badges.append(label(_("✨ update"), "pill", "pill-update"))

        rows = [(_("Size"), human_size(pkg.size)), (_("Section"), pkg.section),
                (_("Updated"), format_date(pkg.installed))]
        if pkg.ident != pkg.name:
            rows.append((_("ID"), pkg.ident))
        rows = [(k, v) for k, v in rows if v and v != "—"]
        self._fill_grid(self.info, rows)
        if pkg.homepage.startswith(("http://", "https://")):
            link = Gtk.LinkButton(uri=pkg.homepage, label=_("Homepage ↗"), halign=Gtk.Align.START)
            self.info.attach(label(_("Web"), "key", xalign=0), 0, len(rows), 1, 1)
            self.info.attach(link, 1, len(rows), 1, 1)

        remove = src.remove_action(pkg)
        update = None if pkg.held else src.update_action(pkg)  # held: updates are skipped
        self.remove_button.set_visible(remove is not None)
        self.update_button.set_visible(update is not None)
        hold = src.unhold_action(pkg) if pkg.held else src.hold_action(pkg)
        self.hold_button.set_visible(hold is not None)
        self.hold_button.set_label(_("📌 Allow updates again") if pkg.held
                                   else _("📌 Keep this version"))
        self.hold_button.set_tooltip_text(hold.display() if hold else None)
        self.update_button.set_tooltip_text(update.display() if update else None)
        self.remove_button.set_tooltip_text(remove.display() if remove else None)
        self.launcher = None
        self.open_button.set_visible(False)  # shown once we know there's something to launch

        self.description.set_label("")
        self.description.set_visible(False)
        self.more_heading.set_visible(False)
        clear(self.more)
        self.needed_heading.set_visible(False)
        self.needed_by.set_visible(False)
        self._show_permissions(None, None, 0)
        self.files_scroller.set_visible(False)
        self.files_buffer.set_text("")
        self.files_button.set_label(_("Show files"))
        self.files_button.set_sensitive(True)
        self.spinner.set_visible(True)
        self.spinner.start()

        token = self._token

        def worker():
            try:
                details, error = src.details(pkg), None
            except Exception as exc:
                details, error = None, str(exc)
            try:
                launcher = src.launch_argv(pkg)
            except Exception:
                launcher = None
            try:
                needed_by = src.required_by(pkg)
            except Exception:
                needed_by = None
            try:
                permissions = src.permissions(pkg)
            except Exception:
                permissions = None
            try:
                folder = src.data_folder(pkg)
                size = dir_size(folder) if folder else 0
            except Exception:
                folder, size = None, 0
            GLib.idle_add(self._on_details_loaded, token, details, error, launcher, needed_by,
                          permissions, folder, size)

        threading.Thread(target=worker, daemon=True).start()

    def _on_details_loaded(self, token, details, error, launcher, needed_by=None,
                           permissions=None, folder=None, size=0) -> bool:
        if token != self._token:
            return GLib.SOURCE_REMOVE  # the user already picked something else
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.description.set_label(_("Couldn't load details: {error}").format(error=error)
                                   if error else details.description
                                   or _("No description provided."))
        self.description.set_visible(True)
        if details and details.fields:
            self.more_heading.set_visible(True)
            self._fill_grid(self.more, details.fields)
        self.launcher = launcher
        self.open_button.set_visible(bool(launcher))
        text = describe_needed_by(needed_by, self.item.pkg.explicit if self.item else True)
        self.needed_heading.set_visible(text is not None)
        self.needed_by.set_visible(text is not None)
        self.needed_by.set_label(text or "")
        self._show_permissions(permissions, folder, size)
        return GLib.SOURCE_REMOVE

    def _show_permissions(self, permissions, folder, size) -> None:
        clear(self.permissions)
        shown = permissions is not None
        self.permissions_heading.set_visible(shown or bool(folder))
        self.permissions_summary.set_visible(shown)
        if shown:
            risky = sum(1 for p in permissions if p.risky)
            self.permissions_summary.set_label(
                ngettext("⚠️ {n} permission reaches outside the sandbox.",
                         "⚠️ {n} permissions reach outside the sandbox.", risky).format(n=risky)
                if risky else _("🔒 Nicely sandboxed: nothing reaches outside it.")
                if permissions else _("🔒 No special permissions."))
            for perm in permissions:
                classes = ("warning",) if perm.risky else ()
                self.permissions.append(label(f"{perm.emoji}  {perm.text}", *classes, xalign=0,
                                              wrap=True))
            if permissions:
                self.permissions.append(label(_("You can change these with Flatseal or "
                                                "`flatpak override`."), "dim", "small", xalign=0,
                                              wrap=True, margin_top=4))
        self.data_folder = folder
        self.data_row.set_visible(bool(folder))
        if folder:
            self.data_label.set_label(_("📁 Keeps {size} of your data in {folder}").format(
                size=human_size(size) if size else "0 B",
                folder=folder.replace(os.path.expanduser("~"), "~", 1)))

    def _open_data_folder(self) -> None:
        if self.data_folder:
            from urpm.ui.dialogs import launch
            launch(["gio", "open", self.data_folder])

    @staticmethod
    def _fill_grid(grid: Gtk.Grid, rows) -> None:
        clear(grid)
        for i, (key, value) in enumerate(rows):
            grid.attach(label(key, "key", xalign=0, valign=Gtk.Align.START), 0, i, 1, 1)
            grid.attach(label(value, xalign=0, wrap=True, selectable=True, hexpand=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR), 1, i, 1, 1)

    # ---------------------------------------------------------------- files

    def load_files(self) -> None:
        item = self.item
        if not item:
            return
        self.files_button.set_sensitive(False)
        self.files_button.set_label(_("Loading files…"))
        token = self._token

        def worker():
            try:
                files, error = item.source.files(item.pkg), None
            except Exception as exc:
                files, error = None, str(exc)
            GLib.idle_add(self._on_files_loaded, token, files, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_files_loaded(self, token, files, error) -> bool:
        if token != self._token:
            return GLib.SOURCE_REMOVE
        if error or files is None:
            self.files_button.set_label(_("Couldn't list files: {error}").format(error=error)
                                        if error else _("This source doesn't track files"))
            return GLib.SOURCE_REMOVE
        self.files_button.set_label(ngettext("{n} file", "{n} files", len(files)).format(
            n=f"{len(files):,}"))
        text = "\n".join(files[:MAX_FILES_SHOWN])
        if len(files) > MAX_FILES_SHOWN:
            text += "\n" + _("… and {n} more").format(n=f"{len(files) - MAX_FILES_SHOWN:,}")
        self.files_buffer.set_text(text)
        self.files_scroller.set_visible(True)
        return GLib.SOURCE_REMOVE


def describe_needed_by(names: list[str] | None, explicit: bool) -> str | None:
    """'Why is this installed?' in a sentence, or None when the source can't tell."""
    if names is None:
        return None
    if not names:
        if explicit:
            return _("Nothing else installed needs this.")
        return _("Nothing installed needs this any more, so it can probably be removed "
                 "(see Cleanup).")
    shown = ", ".join(names[:MAX_NEEDED_BY_SHOWN])
    if len(names) > MAX_NEEDED_BY_SHOWN:
        shown += " " + _("… and {n} more").format(n=len(names) - MAX_NEEDED_BY_SHOWN)
    return ngettext("{n} installed package: {names}", "{n} installed packages: {names}",
                    len(names)).format(n=len(names), names=shown)
