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

from urpm import cleanup, icons, migrate  # noqa: E402
from urpm.duplicates import find_duplicates  # noqa: E402
from urpm.i18n import _, ngettext  # noqa: E402
from urpm.export import render_export  # noqa: E402
from urpm.sources import Package, Source, available_sources, human_size  # noqa: E402
from urpm.sources.base import UnsafeCommand, check_targets  # noqa: E402
from urpm.ui import theme  # noqa: E402
from urpm.ui.cleanup_page import CleanupPage  # noqa: E402
from urpm.ui.details import DetailsPanel  # noqa: E402
from urpm.ui.dialogs import (CommandDialog, ConfirmCleanupDialog,  # noqa: E402
                             ConfirmLaunchDialog, ConfirmRemoveDialog, can_run,
                             choose_export_file, launch)
from urpm.ui.duplicates_page import DuplicatesPage  # noqa: E402
from urpm.ui.history_page import HistoryPage  # noqa: E402
from urpm.ui.settings import SettingsWindow  # noqa: E402
from urpm.ui.sidebar import (ALL, CLEANUP, DUPLICATES, HISTORY, RECENT,  # noqa: E402
                             TOOL_VIEWS, UPDATES, Sidebar, recent_cutoff)
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
        self.cleanups: list = []             # what the Cleanup centre found
        self.cleanup_errors: list[str] = []
        self._scanning = False
        self._scanned = False
        self.duplicates: list = []
        self.history: list = []
        self._history_loaded = False
        self._duplicates_for = -1  # the package list version they were found for

        self.sidebar = Sidebar(self._on_view_selected, self._on_explicit_toggled)
        self.packages = PackageTable(self._in_view, self._on_row_activated,
                                     lambda: self._on_remove())
        self.packages.selection.connect("notify::selected-item", self._on_selection_changed)
        self.packages.shown.connect("items-changed", lambda *_args: self._update_status())
        self.details = DetailsPanel(on_open=self._on_open, on_update=self._on_update,
                                    on_remove=self._on_remove, on_copy_command=self._on_copy_remove,
                                    on_copy_name=self._on_copy_name, on_hold=self._on_hold)
        self.cleanup_page = CleanupPage(self._review_cleanup, self.scan_cleanups)
        self.duplicates_page = DuplicatesPage(self._remove_item, self._show_item)
        self.history_page = HistoryPage(self._show_event_package)
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

        self.search = Gtk.SearchEntry(placeholder_text=_("Search your packages…"), width_chars=30)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("stop-search", lambda e: e.set_text(""))
        self.search.set_key_capture_widget(self)
        header.set_title_widget(self.search)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("Check for updates"), "win.check-updates")
        section.append(_("Export list…"), "win.export")
        section.append(_("Move to a new computer…"), "win.move")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Settings…"), "win.settings")
        section.append(_("Keyboard shortcuts"), "win.shortcuts")
        section.append(_("About URPM"), "app.about")
        section.append(_("Quit"), "app.quit")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                     tooltip_text=_("Menu"))
        accessible_name(menu_button, _("Main menu"))
        header.pack_end(menu_button)

        self.theme_button = Gtk.Button(icon_name="applications-graphics-symbolic",
                                       tooltip_text=_("Themes (Ctrl+,)"), action_name="win.settings")
        accessible_name(self.theme_button, _("Settings and themes"))
        header.pack_end(self.theme_button)

        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                         tooltip_text=_("Reload packages (Ctrl+R)"),
                                         action_name="win.refresh")
        accessible_name(self.refresh_button, _("Reload packages"))
        header.pack_end(self.refresh_button)

        self.updates_spinner = Gtk.Spinner(tooltip_text=_("Checking for updates…"), visible=False)
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

        # Not homogeneous: one long number ("807.0 MB") shouldn't make every card wider and
        # push the whole window wider than the screen.
        self.stats = Gtk.Box(spacing=14, css_classes=["stats"])
        self.stat_labels: dict[str, tuple[Gtk.Label, Gtk.Label]] = {}
        self.stat_cards = []
        for key, emoji, caption, color in (("count", "📦", _("packages"), "pink"),
                                           ("size", "💾", _("on disk"), "lilac"),
                                           ("mine", "💖", _("installed by you"), "mint"),
                                           ("updates", "✨", _("updates available"), "peach")):
            card = Gtk.Box(spacing=12, css_classes=["stat-card", color], hexpand=True)
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
        self.update_all_button = Gtk.Button(label=_("✨ Update all"), css_classes=["cute"],
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
        loading.append(label(_("Counting your packages…"), "placeholder-title"))
        loading.append(label(_("peeking into every box on your shelf"), "dim"))
        self.content.add_named(loading, "loading")
        self.content.add_named(self.packages, "table")
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.empty_emoji = label("🔍", "big-emoji")
        self.empty_title = label(_("Nothing here!"), "placeholder-title")
        self.empty_hint = label("", "dim")
        for widget in (self.empty_emoji, self.empty_title, self.empty_hint):
            empty.append(widget)
        self.content.add_named(empty, "empty")
        self.content.add_named(self.cleanup_page, CLEANUP)
        self.content.add_named(self.duplicates_page, DUPLICATES)
        self.content.add_named(self.history_page, HISTORY)
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
        # Tool pages don't need it, and on small windows the empty "Pick a package" panel
        # isn't worth the space.
        self.details.set_visible(self.view not in TOOL_VIEWS and
                                 (self._layout_mode == 0 or self._selected() is not None))

    def _save_window_state(self, *_args):
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
                packages = src.list_packages()
            except Exception as exc:  # a broken source shouldn't take down the rest
                return src, [], str(exc)
            icons.assign_icons(src, packages, icon_index)
            return src, packages, None

        sources = available_sources()
        GLib.idle_add(self._on_load_started, load_id, sources)
        try:
            icon_index = icons.build_index()
        except Exception:  # icons are a nicety; letters work fine
            icon_index = {}
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
            self.show_toast(_("😿 Couldn't read {errors}").format(errors="; ".join(errors)))
        elif not self.items:
            self.show_toast(_("No package managers found on this system"))
        self.check_updates()
        self.scan_cleanups()
        self.load_history()
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- history

    def load_history(self) -> None:
        sources = list(self.sources)

        def worker():
            events = []
            for src in sources:
                try:
                    events.extend(src.history())
                except Exception:  # unreadable or rotated-away logs: show what we can
                    continue
            GLib.idle_add(self._on_history_loaded, events)

        threading.Thread(target=worker, daemon=True).start()

    def _on_history_loaded(self, events) -> bool:
        self.history = events
        self._history_loaded = True
        self.history_page.set_hide_automatic(self.sidebar.only_explicit)
        self.history_page.set_events(events)
        self._refresh_view(rebuild_sidebar=True)
        return GLib.SOURCE_REMOVE

    def _show_event_package(self, event) -> None:
        """Jump from a history entry to the package, if it's still installed."""
        match = next((i for i in self.items if i.pkg.name.split(":")[0] == event.name
                      and i.source.label.startswith(event.source)), None)
        if match is None:
            self.show_toast(_("{name} isn't installed any more").format(name=event.name))
            return
        self._show_item(match)

    # ---------------------------------------------------------------- cleanup centre

    def scan_cleanups(self) -> None:
        if self._scanning or self._loading:
            return
        self._scanning = True
        self.cleanup_page.show_scanning()
        by_source: dict[Source, list[Package]] = {}
        for item in self.items:
            by_source.setdefault(item.source, []).append(item.pkg)
        for src in self.sources:  # a source with no packages can still have caches
            by_source.setdefault(src, [])

        def worker():
            found, errors = cleanup.scan(by_source)
            GLib.idle_add(self._on_cleanups_scanned, found, errors)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cleanups_scanned(self, found, errors) -> bool:
        self._scanning = False
        self._scanned = True
        self.cleanups, self.cleanup_errors = found, errors
        self.cleanup_page.show_results(found, errors)
        self._refresh_view(rebuild_sidebar=True)
        return GLib.SOURCE_REMOVE

    def _tool_badges(self) -> dict[str, str]:
        if not self._scanned or self._scanning and not self.cleanups:
            cleanup_badge = "…"
        else:
            total = sum(c.size for c in self.cleanups)
            cleanup_badge = human_size(total) if total else ("0" if not self.cleanups else "✓")
        history = f"{len(self.history):,}" if self._history_loaded else "…"
        return {CLEANUP: cleanup_badge, DUPLICATES: f"{len(self.duplicates):,}", HISTORY: history}

    def _review_cleanup(self, item) -> None:
        try:
            cleanup.check(item)
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return

        def confirmed():
            self._run_action(_("Cleaning up: {what}…").format(what=item.title),
                             list(item.actions),
                             _("🧹 Cleaned up, {size} freed").format(size=human_size(item.size))
                             if item.size else _("🧹 Cleaned up"))

        ConfirmCleanupDialog(self, item, confirmed).present()

    def _show_item(self, item: PackageItem) -> None:
        """Jump from a tool page to a package in the list."""
        self.search.set_text("")
        self.view = ALL
        self._refresh_view(rebuild_sidebar=True)
        self.packages.select_key(item.key, clear_if_missing=False)

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
        total = sum(count for count, _err in results)
        errors = [err for _count, err in results if err]
        if errors:
            self.show_toast(_("😿 Couldn't check updates for {errors}").format(
                errors="; ".join(errors)))
        elif total:
            self.show_toast(ngettext("✨ {n} update available", "✨ {n} updates available", total)
                            .format(n=total))
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------------- filtering

    def _in_view(self, item: PackageItem) -> bool:
        view = self.sidebar.view
        if view in TOOL_VIEWS:
            view = ALL  # the list behind a tool page shows everything
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
            if self._duplicates_for != self.packages.version:  # only when packages changed
                self._duplicates_for = self.packages.version
                self.duplicates = find_duplicates(self.items)
                self.duplicates_page.show_groups(self.duplicates)
            self.sidebar.populate(self.items, self.sources, self.errors, self._tool_badges())
        on_tool_page = self.view in TOOL_VIEWS
        self.stats.set_visible(not on_tool_page)
        self._sync_details_visibility()
        self.packages.refilter_view()
        self._update_stats()
        self._update_banner()
        self._update_status()

    def _updatable(self) -> list[PackageItem]:
        """Packages in the current view that have an update we can install (not held)."""
        return [i for i in self.items if i.pkg.update and not i.pkg.held and self._in_view(i)
                and i.source.update_action(i.pkg) is not None]

    def _update_banner(self) -> None:
        ready = self._updatable() if self.view == UPDATES else []
        if ready:
            sources = sorted({i.source.label for i in ready})
            security = sum(1 for i in ready if i.pkg.security)
            text = ngettext("✨ {n} update ready from {sources}",
                            "✨ {n} updates ready from {sources}", len(ready)).format(
                                n=len(ready), sources=", ".join(sources))
            if security:
                text += " · " + _("🛡 {n} security").format(n=security)
            self.banner_label.set_label(text)
        self.banner.set_reveal_child(bool(ready))

    def _update_stats(self) -> None:
        scope = [i for i in self.items if self._in_view(i)]
        self.stat_labels["count"][0].set_label(f"{len(scope):,}")
        self.stat_labels["size"][0].set_label(human_size(sum(i.pkg.size for i in scope)))
        self.stat_labels["mine"][0].set_label(f"{sum(1 for i in scope if i.pkg.explicit):,}")
        updates = sum(1 for i in scope if i.pkg.update)
        security = sum(1 for i in scope if i.pkg.update and i.pkg.security)
        self.stat_labels["updates"][0].set_label("…" if self._checking_updates else f"{updates:,}")
        self.stat_labels["updates"][1].set_label(_("updates · 🛡 {n}").format(n=f"{security:,}")
                                                 if security else _("updates available"))

    def _update_status(self) -> None:
        if self.view in TOOL_VIEWS:
            self.content.set_visible_child_name(self.view)
            self.status.set_label("")
            return
        if not self.items:
            if not self._loading:
                self.status.set_label("")
                self._show_empty("📭", _("No packages found"),
                                 _("URPM didn't find any package manager"))
            return
        shown, total = self.packages.shown.get_n_items(), len(self.items)
        status = _("Showing {shown} of {total} packages").format(shown=f"{shown:,}",
                                                                 total=f"{total:,}")
        if self._loading and self._pending:
            status += " · " + _("still loading {names}…").format(
                names=", ".join(self._pending.values()))
        if self._last_checked:
            status += " · " + _("updates checked at {time}").format(
                time=time.strftime("%H:%M", time.localtime(self._last_checked)))
        self.status.set_label(status)
        if shown:
            self.content.set_visible_child_name("table")
            return
        query = self.search.get_text().strip()
        if query:
            self._show_empty("🔍", _("Nothing here!"),
                             _("No packages match “{query}”").format(query=query))
        elif self.view == UPDATES:
            hint = _("Still checking…") if self._checking_updates else _("Everything is up to date")
            self._show_empty("🎉", _("All fresh!"), hint)
        else:
            self._show_empty("🔍", _("Nothing here!"), _("No packages in this view"))

    def _show_empty(self, emoji: str, title: str, hint: str) -> None:
        self.empty_emoji.set_label(emoji)
        self.empty_title.set_label(title)
        self.empty_hint.set_label(hint)
        self.content.set_visible_child_name("empty")

    # ---------------------------------------------------------------- events

    def _install_actions(self) -> None:
        for name, callback in (("refresh", lambda *_args: self.reload()),
                               ("search", lambda *_args: self.search.grab_focus()),
                               ("check-updates", lambda *_args: self.check_updates()),
                               ("export", lambda *_args: self._on_export()),
                               ("move", lambda *_args: self._on_move()),
                               ("shortcuts", lambda *_args: self._on_shortcuts()),
                               ("settings", lambda *_args: self._on_settings())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _on_search_changed(self, entry) -> None:
        # Stats describe the whole view and ignore the search box, so skip them here.
        self.history_page.set_query(entry.get_text())
        self.packages.set_search(entry.get_text())
        self._update_status()

    def _on_view_selected(self, _view: str) -> None:
        self._refresh_view()

    def _on_explicit_toggled(self, active: bool) -> None:
        self.history_page.set_hide_automatic(active)  # "Just mine" hides dependency installs
        self._refresh_view(rebuild_sidebar=True)

    def _on_selection_changed(self, *_args) -> None:
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
                       _("😿 {problem}. The command was copied, so you can run it in a "
                         "terminal.").format(problem=problem))
            return

        def finished(ok: bool):
            if ok:
                self.show_toast(done_message)
                self.reload()

        CommandDialog(self, title, actions, finished).present()

    def _on_remove(self, *_args) -> None:
        item = self._selected()
        if item:
            self._remove_item(item)

    def _remove_item(self, item: PackageItem) -> None:
        try:
            action = item.source.safe_remove_action(item.pkg)
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if not action:
            return
        pkg = item.pkg

        def confirmed():
            self._run_action(_("Removing {name}…").format(name=pkg.name), action,
                             _("👋 Removed {name}").format(name=pkg.name))

        ConfirmRemoveDialog(self, pkg, item.source, action, confirmed).present()

    def _on_hold(self) -> None:
        item = self._selected()
        if not item:
            return
        pkg, src = item.pkg, item.source
        action = src.unhold_action(pkg) if pkg.held else src.hold_action(pkg)
        if not action:
            return
        try:
            check_targets(action, [pkg])
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if pkg.held:
            self._run_action(_("Allowing updates for {name}…").format(name=pkg.name), action,
                             _("📌 {name} will be updated again").format(name=pkg.name))
        else:
            self._run_action(_("Keeping {name} at {version}…").format(name=pkg.name,
                                                                      version=pkg.version),
                             action, _("📌 {name} stays at {version}").format(
                                 name=pkg.name, version=pkg.version))

    def _on_update(self) -> None:
        item = self._selected()
        if not item or not item.pkg.update or item.pkg.held:
            return
        try:
            actions = item.source.safe_update_actions([item.pkg])
        except UnsafeCommand as exc:
            self.show_toast(f"🛑 {exc}")
            return
        if actions:
            self._run_action(_("Updating {name}…").format(name=item.pkg.name), actions,
                             _("✨ Updated {name}").format(name=item.pkg.name))

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
            self.show_toast(_("Nothing to update here"))
            return
        count = sum(len(p) for p in by_source.values())
        self._run_action(ngettext("Updating {n} package…", "Updating {n} packages…", count)
                         .format(n=count), actions,
                         ngettext("✨ Updated {n} package", "✨ Updated {n} packages", count)
                         .format(n=count))

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
            self.show_toast(_("{name} isn't an app you can open").format(name=item.pkg.name))
            return GLib.SOURCE_REMOVE
        warning = None if confirmed else item.source.launch_warning(item.pkg)
        if warning:
            ConfirmLaunchDialog(self, item.pkg.name, item.pkg.ident, warning,
                                lambda: self._launch_item(item, argv, confirmed=True)).present()
            return GLib.SOURCE_REMOVE
        try:
            launch(argv)
            self.show_toast(_("🚀 Opening {name}…").format(name=item.pkg.name))
        except OSError as exc:
            self.show_toast(_("😿 Couldn't open {name}: {error}").format(name=item.pkg.name,
                                                                         error=exc))
        return GLib.SOURCE_REMOVE

    def _on_export(self) -> None:
        items = self.packages.shown_items()
        if not items:
            self.show_toast(_("Nothing to export in this view"))
            return

        def save(path: str):
            try:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(render_export(items, path))
            except OSError as exc:
                self.show_toast(_("😿 Couldn't save: {error}").format(error=exc.strerror))
                return
            self.show_toast(ngettext("💾 Exported {n} package", "💾 Exported {n} packages",
                                     len(items)).format(n=f"{len(items):,}"))

        choose_export_file(self, save)

    def _on_move(self) -> None:
        from urpm.ui.move import MoveDialog
        by_source: dict[Source, list[Package]] = {}
        for item in self.items:
            by_source.setdefault(item.source, []).append(item.pkg)
        installed = {src.id: pkgs for src, pkgs in by_source.items()}
        self.move_dialog = MoveDialog(self, migrate.snapshot(by_source), installed,
                                      self._install_apps, self.show_toast)
        self.move_dialog.present()

    def _install_apps(self, actions, count: int) -> None:
        self._run_action(ngettext("Installing {n} app…", "Installing {n} apps…", count)
                         .format(n=count), actions,
                         ngettext("📦 Installed {n} app", "📦 Installed {n} apps", count)
                         .format(n=count))

    def _on_shortcuts(self) -> None:
        rows = ((_("Type anywhere"), _("Search")), ("Esc", _("Clear search")),
                ("Ctrl+F", _("Focus search")), (_("Enter / double-click"), _("Open the app")),
                ("Delete", _("Remove package…")), ("Ctrl+R / F5", _("Reload packages")),
                ("Ctrl+U", _("Check for updates")), ("Ctrl+E", _("Export list")),
                ("Ctrl+,", _("Settings and themes")), ("Ctrl+Q", _("Quit")))
        dialog = Gtk.Window(transient_for=self, modal=True, title=_("Keyboard shortcuts"),
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
            self._copy(command, _("📋 Uninstall command copied"))
        elif item:
            self.show_toast(_("This source has no uninstall command"))

    def _on_copy_name(self) -> None:
        item = self._selected()
        if item:
            self._copy(item.pkg.name, _("📋 Copied “{name}”").format(name=item.pkg.name))

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
