"""The main URPM window: sidebar, stat cards, package table, details panel."""

from __future__ import annotations

import math
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from urpm.sources import (KIND_LABELS, Package, Source, available_sources,  # noqa: E402
                          human_size)
from urpm.export import render_export  # noqa: E402
from urpm.ui.dialogs import (CommandDialog, ConfirmRemoveDialog, can_run,  # noqa: E402
                             choose_export_file, launch)

ALL, UPDATES, RECENT = "all", "updates", "recent"
RECENT_DAYS = 30
MAX_FILES_SHOWN = 3000

# Pill colour per source (see the pill-* rules in style.css).
SOURCE_COLORS = {
    "apt": "blue", "rpm": "rose", "pacman": "sky", "xbps": "mint", "apk": "sky",
    "portage": "lilac", "eopkg": "lemon", "flatpak": "lilac", "snap": "peach",
    "appimage": "lemon", "nix": "sky", "brew": "peach", "pip": "blue", "pipx": "mint",
    "npm": "rose", "cargo": "peach",
}


class PackageItem(GObject.Object):
    __gtype_name__ = "UrpmPackageItem"

    def __init__(self, pkg: Package, source: Source):
        super().__init__()
        self.pkg = pkg
        self.source = source
        self.haystack = f"{pkg.name} {pkg.ident} {pkg.summary}".lower()


def avatar_class(name: str) -> str:
    return f"avatar-{zlib.crc32(name.encode()) % 6}"


def pill_classes(source: Source) -> list[str]:
    return ["pill", f"pill-{SOURCE_COLORS.get(source.id, 'gray')}"]


def short_label(source: Source) -> str:
    return source.label.split(" (")[0]


def format_date(ts: float | None) -> str:
    return time.strftime("%b %d, %Y", time.localtime(ts)) if ts else "—"


def label(text: str = "", *classes: str, **props) -> Gtk.Label:
    return Gtk.Label(label=text, css_classes=list(classes), **props)


def ordering(a, b) -> Gtk.Ordering:
    if a < b:
        return Gtk.Ordering.SMALLER
    if a > b:
        return Gtk.Ordering.LARGER
    return Gtk.Ordering.EQUAL


def clear(box: Gtk.Widget) -> None:
    while child := box.get_first_child():
        box.remove(child)


class UrpmWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="URPM", css_classes=["urpm"])
        self.set_default_size(1360, 840)

        self.sources: list[Source] = []
        self.items: list[PackageItem] = []
        self.view = ALL
        self.only_explicit = False
        self.terms: list[str] = []
        self.max_size = 1
        self._details_token = 0
        self._toast_timeout = 0
        self._loading = False
        self._checking_updates = False
        self._generation = 0  # bumps on every reload, so stale update checks are ignored
        self._launcher = None

        self._build_header()
        self._build_body()
        self._install_actions()
        self.reload()

    # ------------------------------------------------------------------ layout

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()

        brand = Gtk.Box(spacing=8, margin_start=6)
        icon = Gtk.Image.new_from_icon_name("urpm")
        icon.set_pixel_size(26)
        brand.append(icon)
        brand.append(label("URPM", "brand"))
        header.pack_start(brand)

        self.search = Gtk.SearchEntry(placeholder_text="Search your packages…", width_chars=40)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("stop-search", lambda e: e.set_text(""))
        self.search.set_key_capture_widget(self)
        header.set_title_widget(self.search)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Check for updates", "win.check-updates")
        section.append("Export list…", "win.export")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Keyboard shortcuts", "win.shortcuts")
        section.append("About URPM", "app.about")
        section.append("Quit", "app.quit")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))

        self.theme_button = Gtk.Button(tooltip_text="Switch light / dark")
        self.theme_button.connect("clicked", self._on_toggle_theme)
        header.pack_end(self.theme_button)
        self._sync_theme_icon()

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text="Reload packages (Ctrl+R)",
                                         action_name="win.refresh")
        header.pack_end(self.refresh_button)

        self.updates_spinner = Gtk.Spinner(tooltip_text="Checking for updates…", visible=False)
        header.pack_end(self.updates_spinner)
        self.set_titlebar(header)

    def _build_body(self) -> None:
        root = Gtk.Box()
        root.append(self._build_sidebar())
        root.append(self._build_main())
        root.append(self._build_details())

        overlay = Gtk.Overlay(child=root)
        self.toast_label = label("", "toast", wrap=True, max_width_chars=70)
        self.toast = Gtk.Revealer(child=self.toast_label, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.END, margin_bottom=28, can_target=False,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      css_classes=["sidebar"], width_request=236)

        self.source_list = Gtk.ListBox(css_classes=["source-list"],
                                       selection_mode=Gtk.SelectionMode.SINGLE)
        self.source_list.connect("row-selected", self._on_view_selected)
        scroller = Gtk.ScrolledWindow(child=self.source_list, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(scroller)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["toggle-card"],
                       margin_top=8)
        row = Gtk.Box(spacing=10)
        row.append(label("Just mine 💖", "title", xalign=0, hexpand=True))
        self.explicit_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.explicit_switch.connect("notify::active", self._on_explicit_toggled)
        row.append(self.explicit_switch)
        card.append(row)
        card.append(label("Hide packages that only came along as dependencies",
                          "dim", "small", xalign=0, wrap=True, max_width_chars=24))
        box.append(card)
        return box

    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)

        self.stats = Gtk.Box(spacing=14, homogeneous=True, css_classes=["stats"])
        self.stat_labels = {}
        for key, emoji, caption, color in (
            ("count", "📦", "packages", "pink"),
            ("size", "💾", "on disk", "lilac"),
            ("mine", "💖", "installed by you", "mint"),
            ("updates", "✨", "updates available", "peach"),
        ):
            card = Gtk.Box(spacing=12, css_classes=["stat-card", color])
            card.append(label(emoji, "stat-emoji"))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            value = label("…", "stat-value", xalign=0, ellipsize=Pango.EllipsizeMode.END)
            cap = label(caption, "stat-caption", xalign=0, ellipsize=Pango.EllipsizeMode.END)
            text.append(value)
            text.append(cap)
            card.append(text)
            self.stats.append(card)
            self.stat_labels[key] = (value, cap)
        main.append(self.stats)

        self.content = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content.add_named(self._build_loading(), "loading")
        self.content.add_named(self._build_table(), "table")
        self.content.add_named(self._build_empty(), "empty")
        main.append(self.content)

        self.status = label("", "status", xalign=0)
        main.append(self.status)
        return main

    def _build_loading(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        box.append(Gtk.Spinner(spinning=True, width_request=36, height_request=36))
        box.append(label("Counting your packages…", "placeholder-title"))
        box.append(label("peeking into every box on your shelf", "dim"))
        return box

    def _build_empty(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.empty_emoji = label("🔍", "big-emoji")
        box.append(self.empty_emoji)
        self.empty_title = label("Nothing here!", "placeholder-title")
        box.append(self.empty_title)
        self.empty_hint = label("", "dim")
        box.append(self.empty_hint)
        return box

    def _build_table(self) -> Gtk.Widget:
        self.store = Gio.ListStore(item_type=PackageItem)
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.filtered.connect("items-changed", lambda *a: self._update_status())

        self.table = Gtk.ColumnView(css_classes=["package-table"], vexpand=True)
        sorted_model = Gtk.SortListModel(model=self.filtered, sorter=self.table.get_sorter())
        self.selection = Gtk.SingleSelection(model=sorted_model, autoselect=False,
                                             can_unselect=True)
        self.selection.connect("notify::selected-item", self._on_selection_changed)
        self.table.set_model(self.selection)

        name_col = self._add_column("Package", self._setup_name, self._bind_name,
                                    lambda p: p.name.casefold(), expand=True)
        self._add_column("Version", self._setup_version, self._bind_version,
                         lambda p: (p.update == "", p.version), width=150)
        self._add_column("Source", self._setup_source, self._bind_source,
                         lambda p: p.source, width=104)
        self._add_column("Size", self._setup_size, self._bind_size,
                         lambda p: p.size, width=140)
        self._add_column("Updated", self._setup_date, self._bind_date,
                         lambda p: p.installed or 0, width=116)
        self.table.sort_by_column(name_col, Gtk.SortType.ASCENDING)

        scroller = Gtk.ScrolledWindow(child=self.table, vexpand=True, hexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        card = Gtk.Box(css_classes=["table-card"], overflow=Gtk.Overflow.HIDDEN)
        card.append(scroller)
        return card

    def _build_details(self) -> Gtk.Widget:
        self.details = Gtk.Stack(css_classes=["details"], width_request=350,
                                 transition_type=Gtk.StackTransitionType.CROSSFADE)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, margin_start=24,
                        margin_end=24)
        empty.append(label("✨", "big-emoji"))
        empty.append(label("Pick a package", "placeholder-title"))
        empty.append(label("Click any row to see its story", "dim"))
        self.details.add_named(empty, "empty")

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        css_classes=["details-inner"])

        top = Gtk.Box(spacing=14)
        self.d_avatar = label("", "avatar", "big", valign=Gtk.Align.START)
        top.append(self.d_avatar)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         hexpand=True)
        self.d_name = label("", "detail-title", xalign=0, wrap=True, selectable=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.d_version = label("", "dim", "mono", xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        titles.append(self.d_name)
        titles.append(self.d_version)
        top.append(titles)
        inner.append(top)

        self.d_badges = Gtk.Box(spacing=6)
        inner.append(self.d_badges)
        self.d_summary = label("", xalign=0, wrap=True)
        inner.append(self.d_summary)

        # Primary actions: open / update / remove
        actions = Gtk.Box(spacing=8, margin_top=4)
        self.b_open = Gtk.Button(label="🚀 Open", css_classes=["cute"])
        self.b_open.connect("clicked", self._on_open)
        self.b_update = Gtk.Button(label="✨ Update", css_classes=["cute"])
        self.b_update.connect("clicked", self._on_update)
        self.b_remove = Gtk.Button(label="🗑️ Remove", css_classes=["danger"])
        self.b_remove.connect("clicked", self._on_remove)
        for button in (self.b_open, self.b_update, self.b_remove):
            actions.append(button)
        inner.append(actions)

        copies = Gtk.Box(spacing=8)
        copy_cmd = Gtk.Button(label="Copy uninstall command", css_classes=["soft", "small-button"])
        copy_cmd.connect("clicked", self._on_copy_remove)
        copy_name = Gtk.Button(label="Copy name", css_classes=["soft", "small-button"])
        copy_name.connect("clicked", self._on_copy_name)
        copies.append(copy_cmd)
        copies.append(copy_name)
        inner.append(copies)

        self.d_info = Gtk.Grid(column_spacing=14, row_spacing=6, margin_top=6)
        inner.append(self.d_info)

        inner.append(label("ABOUT", "section-heading", xalign=0))
        self.d_spinner = Gtk.Spinner(halign=Gtk.Align.START)
        inner.append(self.d_spinner)
        self.d_description = label("", xalign=0, wrap=True, selectable=True,
                                   wrap_mode=Pango.WrapMode.WORD_CHAR)
        inner.append(self.d_description)
        self.d_more_heading = label("MORE", "section-heading", xalign=0)
        inner.append(self.d_more_heading)
        self.d_more = Gtk.Grid(column_spacing=14, row_spacing=6)
        inner.append(self.d_more)

        inner.append(label("FILES", "section-heading", xalign=0))
        self.b_files = Gtk.Button(label="Show files", css_classes=["soft", "small-button"],
                                  halign=Gtk.Align.START)
        self.b_files.connect("clicked", self._on_show_files)
        inner.append(self.b_files)
        self.files_buffer = Gtk.TextBuffer()
        files_view = Gtk.TextView(buffer=self.files_buffer, editable=False, monospace=True,
                                  cursor_visible=False, css_classes=["log"])
        self.files_scroller = Gtk.ScrolledWindow(child=files_view, min_content_height=240,
                                                 css_classes=["log-scroller"], visible=False)
        inner.append(self.files_scroller)

        scroller = Gtk.ScrolledWindow(child=inner, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        self.details.add_named(scroller, "package")
        return self.details

    # ---------------------------------------------------------------- columns

    def _add_column(self, title, setup, bind, key, expand=False, width=None):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(setup()))
        factory.connect("bind", lambda _f, li: bind(li.get_child(), li.get_item()))
        column = Gtk.ColumnViewColumn(title=title, factory=factory, expand=expand,
                                      resizable=True)
        if width:
            column.set_fixed_width(width)
        column.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, *_: ordering(key(a.pkg), key(b.pkg))))
        self.table.append_column(column)
        return column

    @staticmethod
    def _setup_name():
        box = Gtk.Box(spacing=12)
        box.avatar = label("", "avatar")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        box.title = label("", "pkg-name", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        box.summary = label("", "dim", "small", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(box.title)
        text.append(box.summary)
        box.append(box.avatar)
        box.append(text)
        return box

    @staticmethod
    def _bind_name(box, item):
        pkg = item.pkg
        box.avatar.set_label(pkg.name[:1].upper())
        box.avatar.set_css_classes(["avatar", avatar_class(pkg.name)])
        box.title.set_label(pkg.name)
        box.summary.set_label(pkg.summary or " ")

    @staticmethod
    def _setup_version():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        box.version = label("", "mono", "dim", xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.update = label("", "pill", "pill-update", halign=Gtk.Align.START,
                           ellipsize=Pango.EllipsizeMode.END)
        box.append(box.version)
        box.append(box.update)
        return box

    @staticmethod
    def _bind_version(box, item):
        pkg = item.pkg
        box.version.set_label(pkg.version)
        box.set_tooltip_text(f"{pkg.version} → {pkg.update}" if pkg.update else pkg.version)
        box.update.set_label(f"↑ {pkg.update}")
        box.update.set_visible(bool(pkg.update))

    @staticmethod
    def _setup_source():
        return label("", "pill", halign=Gtk.Align.START, valign=Gtk.Align.CENTER)

    @staticmethod
    def _bind_source(widget, item):
        widget.set_label(short_label(item.source))
        widget.set_css_classes(pill_classes(item.source))

    @staticmethod
    def _setup_size():
        box = Gtk.Box(spacing=10)
        box.bar = Gtk.ProgressBar(css_classes=["size-bar"], valign=Gtk.Align.CENTER,
                                  width_request=56)
        box.text = label("", "small", xalign=1, hexpand=True)
        box.append(box.bar)
        box.append(box.text)
        return box

    def _bind_size(self, box, item):
        size = item.pkg.size
        # sqrt keeps the small packages visible next to the 1 GB giants
        box.bar.set_fraction(min(1.0, math.sqrt(size / self.max_size)) if size else 0)
        box.text.set_label(human_size(size))

    @staticmethod
    def _setup_date():
        return label("", "dim", "small", xalign=0)

    @staticmethod
    def _bind_date(widget, item):
        widget.set_label(format_date(item.pkg.installed))

    # ---------------------------------------------------------------- loading

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_button.set_sensitive(False)
        if not self.items:
            self.content.set_visible_child_name("loading")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        def load(src: Source):
            try:
                return src, src.list_packages(), None
            except Exception as exc:  # a broken source shouldn't take down the rest
                return src, [], str(exc)

        sources = available_sources()
        with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
            results = list(pool.map(load, sources))
        GLib.idle_add(self._on_loaded, results)

    def _on_loaded(self, results) -> bool:
        self._loading = False
        selected = self._selected()
        selected_key = (selected.source.id, selected.pkg.ident) if selected else None

        self._generation += 1
        self.sources = [src for src, pkgs, err in results if pkgs or err]
        self.items = [PackageItem(pkg, src) for src, pkgs, _ in results for pkg in pkgs]
        self.max_size = max((i.pkg.size for i in self.items), default=0) or 1

        self.store.splice(0, self.store.get_n_items(), self.items)
        self._populate_sidebar()
        self._refresh_view()
        self.refresh_button.set_sensitive(True)
        self._reselect(selected_key)

        errors = [f"{src.label}: {err}" for src, _, err in results if err]
        if errors:
            self.show_toast("😿 Couldn't read " + "; ".join(errors))
        elif not self.items:
            self.show_toast("No package managers found on this system")
        self.check_updates()
        return GLib.SOURCE_REMOVE

    def _reselect(self, key) -> None:
        if key:
            model = self.selection.get_model()
            for pos in range(model.get_n_items()):
                item = model.get_item(pos)
                if (item.source.id, item.pkg.ident) == key:
                    self.selection.set_selected(pos)
                    return
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.details.set_visible_child_name("empty")

    def check_updates(self) -> None:
        if self._checking_updates or not self.items:
            return
        self._checking_updates = True
        self.updates_spinner.set_visible(True)
        self.updates_spinner.start()
        self._update_stats()
        generation = self._generation
        by_source: dict[Source, list[Package]] = {}
        for item in self.items:
            by_source.setdefault(item.source, []).append(item.pkg)

        def check(entry):
            src, pkgs = entry
            try:
                return src.check_updates(pkgs), None
            except Exception as exc:
                return 0, f"{src.label}: {exc}"

        def worker():
            with ThreadPoolExecutor(max_workers=max(1, len(by_source))) as pool:
                results = list(pool.map(check, by_source.items()))
            GLib.idle_add(self._on_updates_checked, results, generation)

        threading.Thread(target=worker, daemon=True).start()

    def _on_updates_checked(self, results, generation) -> bool:
        self._checking_updates = False
        self.updates_spinner.stop()
        self.updates_spinner.set_visible(False)
        if generation != self._generation:
            # The list was reloaded while we were checking: check the new packages instead.
            self.check_updates()
            return GLib.SOURCE_REMOVE
        # Tell the table every row may have changed so update badges appear.
        n = self.store.get_n_items()
        self.store.items_changed(0, n, n)
        self._populate_sidebar()
        self._refresh_view()
        self._on_selection_changed()
        total = sum(count for count, _ in results)
        errors = [err for _, err in results if err]
        if errors:
            self.show_toast("😿 Couldn't check updates for " + "; ".join(errors))
        elif total:
            self.show_toast(f"✨ {total} update{'s' if total != 1 else ''} available")
        return GLib.SOURCE_REMOVE

    def _populate_sidebar(self) -> None:
        self._rebuilding_sidebar = True
        clear(self.source_list)
        updates = sum(1 for i in self.items if i.pkg.update)
        cutoff = time.time() - RECENT_DAYS * 86400
        recent = sum(1 for i in self.items if (i.pkg.installed or 0) >= cutoff)

        self._add_heading("LIBRARY")
        rows = [self._add_view_row(ALL, "🌈", "Everything", len(self.items)),
                self._add_view_row(UPDATES, "✨", "Updates", updates),
                self._add_view_row(RECENT, "🕒", f"Last {RECENT_DAYS} days", recent)]
        for kind, heading in KIND_LABELS.items():
            group = [s for s in self.sources if s.kind == kind]
            if group:
                self._add_heading(heading)
            for src in group:
                count = sum(1 for i in self.items if i.source is src)
                rows.append(self._add_view_row(src.id, src.emoji, src.label, count))

        target = next((r for r in rows if r.view_id == self.view), rows[0])
        self.view = target.view_id
        self.source_list.select_row(target)
        self._rebuilding_sidebar = False

    def _add_heading(self, text: str) -> None:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["heading-row"])
        row.set_child(label(text, "sidebar-heading", xalign=0))
        self.source_list.append(row)

    def _add_view_row(self, view_id: str, emoji: str, name: str, count: int) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.view_id = view_id
        box = Gtk.Box(spacing=10)
        box.append(label(emoji, "source-emoji"))
        box.append(label(name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        box.append(label(f"{count:,}", "count"))
        row.set_child(box)
        self.source_list.append(row)
        return row

    # ---------------------------------------------------------------- filtering

    def _in_view(self, item: PackageItem) -> bool:
        if self.view == UPDATES:
            if not item.pkg.update:
                return False
        elif self.view == RECENT:
            if (item.pkg.installed or 0) < time.time() - RECENT_DAYS * 86400:
                return False
        elif self.view != ALL and item.source.id != self.view:
            return False
        return not self.only_explicit or item.pkg.explicit

    def _filter_func(self, item: PackageItem, *_) -> bool:
        return self._in_view(item) and all(term in item.haystack for term in self.terms)

    def _refresh_view(self) -> None:
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._update_stats()
        self._update_status()

    def _update_stats(self) -> None:
        scope = [i for i in self.items if self._in_view(i)]
        self.stat_labels["count"][0].set_label(f"{len(scope):,}")
        self.stat_labels["size"][0].set_label(human_size(sum(i.pkg.size for i in scope)))
        self.stat_labels["mine"][0].set_label(f"{sum(1 for i in scope if i.pkg.explicit):,}")
        updates = sum(1 for i in scope if i.pkg.update)
        self.stat_labels["updates"][0].set_label("…" if self._checking_updates else f"{updates:,}")

    def _update_status(self) -> None:
        if not self.items:
            if not self._loading:
                self.status.set_label("")
                self._show_empty("📭", "No packages found", "URPM didn't find any package manager")
            return
        shown, total = self.filtered.get_n_items(), len(self.items)
        self.status.set_label(f"Showing {shown:,} of {total:,} packages")
        if shown:
            self.content.set_visible_child_name("table")
            return
        query = self.search.get_text().strip()
        if query:
            self._show_empty("🔍", "Nothing here!", f"No packages match “{query}”")
        elif self.view == UPDATES:
            hint = "Still checking…" if self._checking_updates else "Everything is up to date"
            self._show_empty("🎉", "All fresh!", hint)
        else:
            self._show_empty("🔍", "Nothing here!", "No packages in this view")

    def _show_empty(self, emoji: str, title: str, hint: str) -> None:
        self.empty_emoji.set_label(emoji)
        self.empty_title.set_label(title)
        self.empty_hint.set_label(hint)
        self.content.set_visible_child_name("empty")

    # ---------------------------------------------------------------- details

    def _selected(self) -> PackageItem | None:
        return self.selection.get_selected_item()

    def _on_selection_changed(self, *_) -> None:
        item = self._selected()
        if item is None:
            self.details.set_visible_child_name("empty")
            return
        pkg, src = item.pkg, item.source
        self.details.set_visible_child_name("package")

        self.d_avatar.set_label(pkg.name[:1].upper())
        self.d_avatar.set_css_classes(["avatar", "big", avatar_class(pkg.name)])
        self.d_name.set_label(pkg.name)
        self.d_version.set_label(f"{pkg.version}  →  {pkg.update}" if pkg.update else pkg.version)
        self.d_summary.set_label(pkg.summary)
        self.d_summary.set_visible(bool(pkg.summary))

        clear(self.d_badges)
        self.d_badges.append(label(f"{src.emoji} {short_label(src)}", *pill_classes(src)))
        if src.tracks_explicit:
            self.d_badges.append(label("💖 yours", "pill", "pill-mine") if pkg.explicit
                                 else label("🔗 dependency", "pill", "pill-dep"))
        if pkg.update:
            self.d_badges.append(label("✨ update", "pill", "pill-update"))

        rows = [("Size", human_size(pkg.size)), ("Section", pkg.section),
                ("Updated", format_date(pkg.installed))]
        if pkg.ident != pkg.name:
            rows.append(("ID", pkg.ident))
        rows = [(k, v) for k, v in rows if v and v != "—"]
        self._fill_grid(self.d_info, rows)
        if pkg.homepage.startswith(("http://", "https://")):
            link = Gtk.LinkButton(uri=pkg.homepage, label="Homepage ↗", halign=Gtk.Align.START)
            self.d_info.attach(label("Web", "key", xalign=0), 0, len(rows), 1, 1)
            self.d_info.attach(link, 1, len(rows), 1, 1)

        remove = src.remove_action(pkg)
        update = src.update_action(pkg)
        self.b_remove.set_visible(remove is not None)
        self.b_update.set_visible(update is not None)
        self.b_update.set_tooltip_text(update.display() if update else None)
        self.b_remove.set_tooltip_text(remove.display() if remove else None)
        self._launcher = None
        self.b_open.set_visible(False)  # shown once we know there's something to launch

        self.d_description.set_label("")
        self.d_description.set_visible(False)
        self.d_more_heading.set_visible(False)
        clear(self.d_more)
        self.files_scroller.set_visible(False)
        self.files_buffer.set_text("")
        self.b_files.set_label("Show files")
        self.b_files.set_sensitive(True)
        self.d_spinner.set_visible(True)
        self.d_spinner.start()

        self._details_token += 1
        token = self._details_token

        def worker():
            try:
                details, error = src.details(pkg), None
            except Exception as exc:
                details, error = None, str(exc)
            try:
                launcher = src.launch_argv(pkg)
            except Exception:
                launcher = None
            GLib.idle_add(self._on_details_loaded, token, details, error, launcher)

        threading.Thread(target=worker, daemon=True).start()

    def _on_details_loaded(self, token, details, error, launcher) -> bool:
        if token != self._details_token:
            return GLib.SOURCE_REMOVE  # the user already clicked something else
        self.d_spinner.stop()
        self.d_spinner.set_visible(False)
        if error:
            text = f"Couldn't load details: {error}"
        else:
            text = details.description or "No description provided."
        self.d_description.set_label(text)
        self.d_description.set_visible(True)
        if details and details.fields:
            self.d_more_heading.set_visible(True)
            self._fill_grid(self.d_more, details.fields)
        self._launcher = launcher
        self.b_open.set_visible(bool(launcher))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _fill_grid(grid: Gtk.Grid, rows) -> None:
        clear(grid)
        for i, (key, value) in enumerate(rows):
            grid.attach(label(key, "key", xalign=0, valign=Gtk.Align.START), 0, i, 1, 1)
            grid.attach(label(value, xalign=0, wrap=True, selectable=True, hexpand=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR), 1, i, 1, 1)

    def _on_show_files(self, _button) -> None:
        item = self._selected()
        if not item:
            return
        self.b_files.set_sensitive(False)
        self.b_files.set_label("Loading files…")
        token = self._details_token

        def worker():
            try:
                files, error = item.source.files(item.pkg), None
            except Exception as exc:
                files, error = None, str(exc)
            GLib.idle_add(self._on_files_loaded, token, files, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_files_loaded(self, token, files, error) -> bool:
        if token != self._details_token:
            return GLib.SOURCE_REMOVE
        if error or files is None:
            self.b_files.set_label(f"Couldn't list files: {error}" if error
                                   else "This source doesn't track files")
            return GLib.SOURCE_REMOVE
        self.b_files.set_label(f"{len(files):,} file{'s' if len(files) != 1 else ''}")
        text = "\n".join(files[:MAX_FILES_SHOWN])
        if len(files) > MAX_FILES_SHOWN:
            text += f"\n… and {len(files) - MAX_FILES_SHOWN:,} more"
        self.files_buffer.set_text(text)
        self.files_scroller.set_visible(True)
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- actions

    def _install_actions(self) -> None:
        for name, callback in (("refresh", lambda *_: self.reload()),
                               ("search", lambda *_: self.search.grab_focus()),
                               ("check-updates", lambda *_: self.check_updates()),
                               ("export", lambda *_: self._on_export()),
                               ("shortcuts", lambda *_: self._on_shortcuts())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _on_search_changed(self, entry) -> None:
        self.terms = entry.get_text().lower().split()
        self._refresh_view()

    def _on_view_selected(self, _list, row) -> None:
        if row is None or getattr(self, "_rebuilding_sidebar", False) \
                or not hasattr(row, "view_id"):
            return
        self.view = row.view_id
        self._refresh_view()

    def _on_explicit_toggled(self, switch, _pspec) -> None:
        self.only_explicit = switch.get_active()
        self._refresh_view()

    def _on_toggle_theme(self, _button) -> None:
        self.get_application().theme.toggle()
        self._sync_theme_icon()

    def _sync_theme_icon(self) -> None:
        dark = self.get_application().theme.dark
        self.theme_button.set_icon_name("weather-clear-symbolic" if dark
                                        else "weather-clear-night-symbolic")

    def _on_open(self, _button) -> None:
        item = self._selected()
        if item and self._launcher:
            try:
                launch(self._launcher)
                self.show_toast(f"🚀 Opening {item.pkg.name}…")
            except OSError as exc:
                self.show_toast(f"😿 Couldn't open {item.pkg.name}: {exc}")

    def _run_action(self, title: str, action, done_message: str) -> None:
        problem = can_run(action)
        if problem:
            self._copy(action.display(), f"😿 {problem}. The command was copied, "
                                         "so you can run it in a terminal.")
            return

        def finished(ok: bool):
            if ok:
                self.show_toast(done_message)
                self.reload()

        CommandDialog(self, title, action, finished).present()

    def _on_remove(self, _button) -> None:
        item = self._selected()
        action = item.source.remove_action(item.pkg) if item else None
        if not action:
            return
        pkg = item.pkg

        def confirmed():
            self._run_action(f"Removing {pkg.name}…", action, f"👋 Removed {pkg.name}")

        ConfirmRemoveDialog(self, pkg, item.source, action, confirmed).present()

    def _on_update(self, _button) -> None:
        item = self._selected()
        action = item.source.update_action(item.pkg) if item else None
        if action:
            self._run_action(f"Updating {item.pkg.name}…", action,
                             f"✨ Updated {item.pkg.name}")

    def _on_export(self) -> None:
        model = self.selection.get_model()
        items = [model.get_item(i) for i in range(model.get_n_items())]
        if not items:
            self.show_toast("Nothing to export in this view")
            return

        def save(path: str):
            try:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(render_export(items, path))
            except OSError as exc:
                self.show_toast(f"😿 Couldn't save: {exc.strerror}")
                return
            self.show_toast(f"💾 Exported {len(items):,} packages")

        choose_export_file(self, save)

    def _on_shortcuts(self) -> None:
        rows = (("Type anywhere", "Search"), ("Esc", "Clear search"), ("Ctrl+F", "Focus search"),
                ("Ctrl+R / F5", "Reload packages"), ("Ctrl+U", "Check for updates"),
                ("Ctrl+E", "Export list"), ("Ctrl+Q", "Quit"))
        dialog = Gtk.Window(transient_for=self, modal=True, title="Keyboard shortcuts",
                            css_classes=["urpm", "dialog-window"], resizable=False)
        grid = Gtk.Grid(column_spacing=24, row_spacing=10, css_classes=["dialog-body"])
        for i, (keys, what) in enumerate(rows):
            grid.attach(label(keys, "pill", "pill-gray", halign=Gtk.Align.START), 0, i, 1, 1)
            grid.attach(label(what, xalign=0), 1, i, 1, 1)
        dialog.set_child(grid)
        dialog.present()

    def _copy(self, text: str, message: str) -> None:
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
        self.show_toast(message)

    def _on_copy_remove(self, _button) -> None:
        item = self._selected()
        command = item.source.remove_command(item.pkg) if item else ""
        if command:
            self._copy(command, "📋 Uninstall command copied")
        elif item:
            self.show_toast("This source has no uninstall command")

    def _on_copy_name(self, _button) -> None:
        item = self._selected()
        if item:
            self._copy(item.pkg.name, f"📋 Copied “{item.pkg.name}”")

    def show_toast(self, message: str) -> None:
        self.toast_label.set_label(message)
        self.toast.set_reveal_child(True)
        if self._toast_timeout:
            GLib.source_remove(self._toast_timeout)

        def hide():
            self.toast.set_reveal_child(False)
            self._toast_timeout = 0
            return GLib.SOURCE_REMOVE

        self._toast_timeout = GLib.timeout_add_seconds(4, hide)
