"""The main URPM window. It wires the parts together and owns loading and actions:

    header (search, buttons)
    Sidebar        │ stats · banner · PackageTable │ DetailsPanel
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from urpm.export import render_export  # noqa: E402
from urpm.sources import Package, Source, available_sources, human_size  # noqa: E402
from urpm.sources.base import UnsafeCommand  # noqa: E402
from urpm.ui import theme  # noqa: E402
from urpm.ui.details import DetailsPanel  # noqa: E402
from urpm.ui.dialogs import (CommandDialog, ConfirmLaunchDialog,  # noqa: E402
                             ConfirmRemoveDialog, can_run, choose_export_file, launch)
from urpm.ui.settings import SettingsWindow  # noqa: E402
from urpm.ui.sidebar import ALL, RECENT, UPDATES, Sidebar, recent_cutoff  # noqa: E402
from urpm.ui.table import PackageItem, PackageTable  # noqa: E402
from urpm.ui.widgets import accessible_name, label  # noqa: E402

# Window widths where the layout gets more compact (see _apply_layout).
WIDE, MEDIUM = 1280, 1120


def layout_mode(width: int) -> int:
    return 0 if width >= WIDE else 1 if width >= MEDIUM else 2


class UrpmWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="URPM", css_classes=["urpm"])
        saved = theme.load_prefs().get("window", {})
        self.set_default_size(saved.get("width", 1360), saved.get("height", 840))
        if saved.get("maximized"):
            self.maximize()
        self.connect("close-request", self._save_window_state)

        self.sources: list[Source] = []
        self.errors: dict[str, str] = {}     # source id -> why it couldn't be read
        self._toast_timeout = 0
        self._loading = False
        self._checking_updates = False
        self._generation = 0  # bumps on every reload, so stale update checks are ignored
        self._load_id = 0     # bumps on every reload, so results from an old one are ignored
        self._pending: dict[str, str] = {}  # sources still loading: id -> label
        self._loaded_ids: set[str] = set()
        self._load_order: list[Source] = []
        self._keep_selected = None
        self._last_checked: float | None = None
        self._recent_cutoff = recent_cutoff()

        self.sidebar = Sidebar(self._on_view_selected, self._on_explicit_toggled)
        self.packages = PackageTable(self._in_view, self._on_row_activated,
                                     lambda: self._on_remove())
        self.packages.selection.connect("notify::selected-item", self._on_selection_changed)
        self.packages.shown.connect("items-changed", lambda *_: self._update_status())
        self.details = DetailsPanel(on_open=self._on_open, on_update=self._on_update,
                                    on_remove=self._on_remove, on_copy_command=self._on_copy_remove,
                                    on_copy_name=self._on_copy_name)
        self._build_header()
        self._build_body()
        self._install_actions()
        # Start in the right layout so a small saved size isn't bumped up first.
        self._layout_mode = layout_mode(saved.get("width", 1360))
        self._apply_layout(self._layout_mode)
        self.reload()

    # Short names for the parts tests and actions use most.
    @property
    def items(self) -> list[PackageItem]:
        return self.packages.items

    @property
    def view(self) -> str:
        return self.sidebar.view

    @view.setter
    def view(self, value: str) -> None:
        self.sidebar.view = value

    def _selected(self) -> PackageItem | None:
        return self.packages.selected()

    # ------------------------------------------------------------------ layout

    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        brand = Gtk.Box(spacing=8, margin_start=6)
        icon = Gtk.Image.new_from_icon_name("urpm")
        icon.set_pixel_size(26)
        brand.append(icon)
        brand.append(label("URPM", "brand"))
        header.pack_start(brand)

        self.search = Gtk.SearchEntry(placeholder_text="Search your packages…", width_chars=30)
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
        section.append("Settings…", "win.settings")
        section.append("Keyboard shortcuts", "win.shortcuts")
        section.append("About URPM", "app.about")
        section.append("Quit", "app.quit")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text="Menu")
        accessible_name(menu_button, "Main menu")
        header.pack_end(menu_button)

        self.theme_button = Gtk.Button(icon_name="applications-graphics-symbolic",
                                       tooltip_text="Themes (Ctrl+,)", action_name="win.settings")
        accessible_name(self.theme_button, "Settings and themes")
        header.pack_end(self.theme_button)

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text="Reload packages (Ctrl+R)",
                                         action_name="win.refresh")
        accessible_name(self.refresh_button, "Reload packages")
        header.pack_end(self.refresh_button)

        self.updates_spinner = Gtk.Spinner(tooltip_text="Checking for updates…", visible=False)
        header.pack_end(self.updates_spinner)
        self.set_titlebar(header)

    def _build_body(self) -> None:
        root = Gtk.Box()
        root.append(self.sidebar)
        root.append(self._build_main())
        root.append(self.details)

        overlay = Gtk.Overlay(child=root)
        self.toast_label = label("", "toast", wrap=True, max_width_chars=70)
        self.toast = Gtk.Revealer(child=self.toast_label, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.END, margin_bottom=28, can_target=False,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)

        self.stats = Gtk.Box(spacing=14, homogeneous=True, css_classes=["stats"])
        self.stat_labels: dict[str, tuple[Gtk.Label, Gtk.Label]] = {}
        self.stat_cards = []
        for key, emoji, caption, color in (("count", "📦", "packages", "pink"),
                                           ("size", "💾", "on disk", "lilac"),
                                           ("mine", "💖", "installed by you", "mint"),
                                           ("updates", "✨", "updates available", "peach")):
            card = Gtk.Box(spacing=12, css_classes=["stat-card", color])
            card.emoji = label(emoji, "stat-emoji")
            card.append(card.emoji)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            value = label("…", "stat-value", xalign=0)  # numbers are never cut off
            cap = label(caption, "stat-caption", xalign=0, ellipsize=Pango.EllipsizeMode.END)
            text.append(value)
            text.append(cap)
            card.append(text)
            self.stats.append(card)
            self.stat_cards.append(card)
            self.stat_labels[key] = (value, cap)
        main.append(self.stats)

        banner = Gtk.Box(spacing=12, css_classes=["banner"])
        self.banner_label = label("", "banner-text", xalign=0, hexpand=True, wrap=True)
        banner.append(self.banner_label)
        self.update_all_button = Gtk.Button(label="✨ Update all", css_classes=["cute"],
                                            valign=Gtk.Align.CENTER)
        self.update_all_button.connect("clicked", self._on_update_all)
        banner.append(self.update_all_button)
        self.banner = Gtk.Revealer(child=banner,
                                   transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        main.append(self.banner)

        self.content = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        loading.append(Gtk.Spinner(spinning=True, width_request=36, height_request=36))
        loading.append(label("Counting your packages…", "placeholder-title"))
        loading.append(label("peeking into every box on your shelf", "dim"))
        self.content.add_named(loading, "loading")
        self.content.add_named(self.packages, "table")
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.empty_emoji = label("🔍", "big-emoji")
        self.empty_title = label("Nothing here!", "placeholder-title")
        self.empty_hint = label("", "dim")
        for widget in (self.empty_emoji, self.empty_title, self.empty_hint):
            empty.append(widget)
        self.content.add_named(empty, "empty")
        main.append(self.content)

        self.status = label("", "status", xalign=0)
        main.append(self.status)
        return main

    # ---------------------------------------------------------------- responsive layout

    def do_size_allocate(self, width, height, baseline):
        Gtk.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        mode = layout_mode(width)
        if mode != self._layout_mode:
            self._layout_mode = mode
            # Changing widgets during allocation isn't allowed; do it right after.
            GLib.idle_add(self._apply_layout, mode)

    def _apply_layout(self, mode: int) -> bool:
        """0 = everything, 1 = hide the Updated column, 2 = also Source + slimmer panels."""
        self.packages.apply_layout(mode)
        self.sidebar.set_size_request(236 if mode < 2 else 196, -1)
        self.details.set_size_request(350 if mode == 0 else 300, -1)
        for card in self.stat_cards:
            card.emoji.set_visible(mode < 2)
        self._sync_details_visibility()
        return GLib.SOURCE_REMOVE

    def _sync_details_visibility(self) -> None:
        # On small windows the empty "Pick a package" panel isn't worth the space.
        self.details.set_visible(self._layout_mode == 0 or self._selected() is not None)

    def _save_window_state(self, *_):
        width, height = self.get_default_size()
        prefs = theme.load_prefs()
        prefs["window"] = {"width": width, "height": height, "maximized": self.is_maximized()}
        theme.save_prefs(prefs)
        return False  # let the window close

    # ---------------------------------------------------------------- loading

    def reload(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._load_id += 1
        self.refresh_button.set_sensitive(False)
        if not self.items:
            self.content.set_visible_child_name("loading")
        threading.Thread(target=self._load_worker, args=(self._load_id,), daemon=True).start()

    def _load_worker(self, load_id: int) -> None:
        def load(src: Source):
            try:
                return src, src.list_packages(), None
            except Exception as exc:  # a broken source shouldn't take down the rest
                return src, [], str(exc)

        sources = available_sources()
        GLib.idle_add(self._on_load_started, load_id, sources)
        # Hand each package manager's results to the UI as soon as it finishes, so one
        # slow source (say, a hung snapd) doesn't hold back all the others.
        with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
            for future in as_completed([pool.submit(load, src) for src in sources]):
                GLib.idle_add(self._on_source_loaded, load_id, *future.result())
        GLib.idle_add(self._on_load_finished, load_id)

    def _on_load_started(self, load_id: int, sources: list[Source]) -> bool:
        if load_id != self._load_id:
            return GLib.SOURCE_REMOVE
        selected = self._selected()
        self._keep_selected = selected.key if selected else None
        self._generation += 1
        self._load_order = sources
        self._pending = {src.id: src.label for src in sources}
        self._loaded_ids = set()
        self._update_status()
        return GLib.SOURCE_REMOVE

    def _on_source_loaded(self, load_id: int, src: Source, pkgs: list[Package],
                          error: str | None) -> bool:
        if load_id != self._load_id:
            return GLib.SOURCE_REMOVE
        self._pending.pop(src.id, None)
        self._loaded_ids.add(src.id)
        if error:
            self.errors[src.id] = error
        else:
            self.errors.pop(src.id, None)
        # Every package needs a name to be shown, searched and acted on.
        self.packages.replace_source_items(
            src.id, [PackageItem(pkg, src) for pkg in pkgs if pkg.name.strip()])
        present = {i.source.id for i in self.items}
        self.sources = [s for s in self._load_order if s.id in self.errors or s.id in present]
        self._refresh_view(rebuild_sidebar=True)
        if self._keep_selected and self._selected() is None:
            self.packages.select_key(self._keep_selected, clear_if_missing=False)
        return GLib.SOURCE_REMOVE

    def _on_load_finished(self, load_id: int) -> bool:
        if load_id != self._load_id:
            return GLib.SOURCE_REMOVE
        self._loading = False
        # Drop rows and errors from package managers that have gone since the last load.
        for source_id in {i.source.id for i in self.items} - self._loaded_ids:
            self.packages.replace_source_items(source_id, [])
        for source_id in set(self.errors) - self._loaded_ids:
            del self.errors[source_id]
        self.refresh_button.set_sensitive(True)
        self._refresh_view(rebuild_sidebar=True)
        self.packages.select_key(self._keep_selected)

        errors = [f"{src.label}: {self.errors[src.id]}" for src in self._load_order
                  if src.id in self.errors]
        if errors:
            self.show_toast("😿 Couldn't read " + "; ".join(errors))
        elif not self.items:
            self.show_toast("No package managers found on this system")
        self.check_updates()
        return GLib.SOURCE_REMOVE

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
        self.packages.rows_changed()  # so update badges appear
        self._last_checked = time.time()
        self._refresh_view(rebuild_sidebar=True)
        self.details.show_package(self._selected())
        total = sum(count for count, _ in results)
        errors = [err for _, err in results if err]
        if errors:
            self.show_toast("😿 Couldn't check updates for " + "; ".join(errors))
        elif total:
            self.show_toast(f"✨ {total} update{'s' if total != 1 else ''} available")
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- filtering

    def _in_view(self, item: PackageItem) -> bool:
        view = self.sidebar.view
        if view == UPDATES:
            if not item.pkg.update:
                return False
        elif view == RECENT:
            if (item.pkg.installed or 0) < self._recent_cutoff:
                return False
        elif view != ALL and item.source.id != view:
            return False
        return item.pkg.explicit or not self.sidebar.only_explicit

    def _refresh_view(self, rebuild_sidebar: bool = False) -> None:
        self._recent_cutoff = recent_cutoff()
        if rebuild_sidebar:
            self.sidebar.populate(self.items, self.sources, self.errors)
        self.packages.refilter_view()
        self._update_stats()
        self._update_banner()
        self._update_status()

    def _updatable(self) -> list[PackageItem]:
        """Packages in the current view that have an update we can install."""
        return [i for i in self.items if i.pkg.update and self._in_view(i)
                and i.source.update_action(i.pkg) is not None]

    def _update_banner(self) -> None:
        ready = self._updatable() if self.view == UPDATES else []
        if ready:
            sources = sorted({i.source.label for i in ready})
            self.banner_label.set_label(
                f"✨ {len(ready)} update{'s' if len(ready) != 1 else ''} ready from "
                f"{', '.join(sources)}")
        self.banner.set_reveal_child(bool(ready))

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
        shown, total = self.packages.shown.get_n_items(), len(self.items)
        status = f"Showing {shown:,} of {total:,} packages"
        if self._loading and self._pending:
            status += " · still loading " + ", ".join(self._pending.values()) + "…"
        if self._last_checked:
            status += " · updates checked at " + time.strftime(
                "%H:%M", time.localtime(self._last_checked))
        self.status.set_label(status)
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

    # ---------------------------------------------------------------- events

    def _install_actions(self) -> None:
        for name, callback in (("refresh", lambda *_: self.reload()),
                               ("search", lambda *_: self.search.grab_focus()),
                               ("check-updates", lambda *_: self.check_updates()),
                               ("export", lambda *_: self._on_export()),
                               ("shortcuts", lambda *_: self._on_shortcuts()),
                               ("settings", lambda *_: self._on_settings())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _on_search_changed(self, entry) -> None:
        # Stats describe the whole view and ignore the search box, so skip them here.
        self.packages.set_search(entry.get_text())
        self._update_status()

    def _on_view_selected(self, _view: str) -> None:
        self._refresh_view()

    def _on_explicit_toggled(self, _active: bool) -> None:
        self._refresh_view(rebuild_sidebar=True)

    def _on_selection_changed(self, *_) -> None:
        self._sync_details_visibility()
        self.details.show_package(self._selected())

    def _on_settings(self) -> None:
        SettingsWindow(self, self.get_application().theme).present()

    # ---------------------------------------------------------------- actions

    def _on_open(self, launcher) -> None:
        item = self._selected()
        if item and launcher:
            self._launch_item(item, launcher)

    def _run_action(self, title: str, action, done_message: str) -> None:
        actions = action if isinstance(action, list) else [action]
        problem = next((p for p in map(can_run, actions) if p), None)
        if problem:
            self._copy("\n".join(a.display() for a in actions),
                       f"😿 {problem}. The command was copied, so you can run it in a terminal.")
            return

        def finished(ok: bool):
            if ok:
                self.show_toast(done_message)
                self.reload()

        CommandDialog(self, title, actions, finished).present()

    def _on_remove(self, *_) -> None:
        item = self._selected()
        if not item:
            return
        try:
            action = item.source.safe_remove_action(item.pkg)
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if not action:
            return
        pkg = item.pkg

        def confirmed():
            self._run_action(f"Removing {pkg.name}…", action, f"👋 Removed {pkg.name}")

        ConfirmRemoveDialog(self, pkg, item.source, action, confirmed).present()

    def _on_update(self) -> None:
        item = self._selected()
        if not item or not item.pkg.update:
            return
        try:
            actions = item.source.safe_update_actions([item.pkg])
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if actions:
            self._run_action(f"Updating {item.pkg.name}…", actions,
                             f"✨ Updated {item.pkg.name}")

    def _on_update_all(self, _button=None) -> None:
        by_source: dict[Source, list[Package]] = {}
        for item in self._updatable():
            by_source.setdefault(item.source, []).append(item.pkg)
        try:
            actions = [a for src, pkgs in by_source.items()
                       for a in src.safe_update_actions(pkgs)]
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if not actions:
            self.show_toast("Nothing to update here")
            return
        count = sum(len(p) for p in by_source.values())
        self._run_action(f"Updating {count} package{'s' if count != 1 else ''}…", actions,
                         f"✨ Updated {count} package{'s' if count != 1 else ''}")

    def _on_row_activated(self, item: PackageItem | None) -> None:
        if item is None:
            return

        def worker():
            try:
                argv = item.source.launch_argv(item.pkg)
            except Exception:
                argv = None
            GLib.idle_add(self._launch_item, item, argv)

        threading.Thread(target=worker, daemon=True).start()

    def _launch_item(self, item: PackageItem, argv, confirmed: bool = False) -> bool:
        if not argv:
            self.show_toast(f"{item.pkg.name} isn't an app you can open")
            return GLib.SOURCE_REMOVE
        warning = None if confirmed else item.source.launch_warning(item.pkg)
        if warning:
            ConfirmLaunchDialog(self, item.pkg.name, item.pkg.ident, warning,
                                lambda: self._launch_item(item, argv, confirmed=True)).present()
            return GLib.SOURCE_REMOVE
        try:
            launch(argv)
            self.show_toast(f"🚀 Opening {item.pkg.name}…")
        except OSError as exc:
            self.show_toast(f"😿 Couldn't open {item.pkg.name}: {exc}")
        return GLib.SOURCE_REMOVE

    def _on_export(self) -> None:
        items = self.packages.shown_items()
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
                ("Enter / double-click", "Open the app"), ("Delete", "Remove package…"),
                ("Ctrl+R / F5", "Reload packages"), ("Ctrl+U", "Check for updates"),
                ("Ctrl+E", "Export list"), ("Ctrl+,", "Settings and themes"), ("Ctrl+Q", "Quit"))
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

    def _on_copy_remove(self) -> None:
        item = self._selected()
        command = item.source.remove_command(item.pkg) if item else ""
        if command:
            self._copy(command, "📋 Uninstall command copied")
        elif item:
            self.show_toast("This source has no uninstall command")

    def _on_copy_name(self) -> None:
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
