"""Drives the real window with a fake package source.

These open real GTK windows, so they only run when asked to, on a hidden display:

    make test-ui          # uses xvfb-run, so nothing appears on your screen
    URPM_UI_TESTS=1 xvfb-run -a dbus-run-session -- python3 -m unittest tests.test_ui
"""

import os
import time
import unittest
from unittest import mock

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    HAVE_GTK = Gtk.init_check() if hasattr(Gtk, "init_check") else True
except (ImportError, ValueError):
    HAVE_GTK = False

from urpm.sources.base import DEV, Action, Cleanup, Details, Package, Source


class FakeSource(Source):
    id, label, emoji, kind = "fake", "Fake", "🧸", DEV

    def available(self):
        return True

    def list_packages(self):
        now = time.time()
        return [Package("alpha", "1.10", "fake", summary="first letter", size=5_000_000,
                        installed=now),
                Package("beta", "1.9", "fake", summary="second", size=10, explicit=False),
                Package("gamma", "10.0", "fake", summary="third", homepage="https://example.org"),
                Package("   ", "1.0", "fake", summary="a nameless package must be skipped")]

    def check_updates(self, packages):
        for pkg in packages:
            if pkg.name == "gamma":
                pkg.update = "3.1"
                pkg.security = True
        return 1

    def history(self):
        from urpm.history import Event
        now = time.time()
        return [Event(now - 10, "removed", "gone", "1.0", source="Fake"),
                Event(now - 50, "installed", "libdep", "1.0", source="Fake", automatic=True),
                Event(now - 100, "installed", "alpha", "1.10", source="Fake"),
                Event(now - 3 * 86400, "updated", "gamma", "10.0", "9.0", "Fake")]

    def hold_action(self, pkg):
        return Action(["true", "hold", pkg.name])

    def unhold_action(self, pkg):
        return Action(["true", "unhold", pkg.name])

    def details(self, pkg):
        return Details(description=f"All about {pkg.name}", fields=[("Key", "Value")])

    def files(self, pkg):
        return [f"/usr/bin/{pkg.name}"]

    def remove_action(self, pkg):
        return Action(["true", pkg.name])

    def update_actions(self, pkgs):
        return [Action(["true", "update", *(p.name for p in pkgs)])]

    def launch_argv(self, pkg):
        return ["true", pkg.name] if pkg.explicit else None

    def required_by(self, pkg):
        return {"beta": ["alpha"], "alpha": [], "gamma": []}.get(pkg.name)

    def cleanups(self, packages):
        return [Cleanup("fake-cache", "Fake download cache", "Old downloads.", "Fake", 5000,
                        ["/tmp/fake-cache"], [Action(["true", "clean"])], kind="cache")]


class BrokenSource(Source):
    id, label, emoji, kind = "broken", "Broken", "💥", DEV

    def available(self):
        return True

    def list_packages(self):
        raise RuntimeError("database is locked")


def spin(until, timeout=10.0):
    """Run the GTK main loop until `until()` is true."""
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while not until():
        if time.time() > end:
            raise AssertionError("timed out waiting for the UI")
        ctx.iteration(False)
        time.sleep(0.005)


@unittest.skipUnless(os.environ.get("URPM_UI_TESTS") == "1",
                     "UI tests open windows; run `make test-ui` (hidden display) to include them")
@unittest.skipUnless(HAVE_GTK, "needs GTK 4")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from urpm.ui import app as appmod, theme
        cls.prefs = mock.patch.object(theme, "save_prefs")  # never touch the real config
        cls.prefs.start()
        cls.patch = mock.patch("urpm.ui.window.available_sources",
                               return_value=[FakeSource(), BrokenSource()])
        cls.patch.start()
        # Keep tests independent of this machine: no real caches, no real icons.
        cls.no_caches = mock.patch("urpm.cleanup.CACHES", ())
        cls.no_caches.start()
        cls.no_icons = mock.patch("urpm.icons.build_index", return_value={})
        cls.no_icons.start()
        cls.app = appmod.UrpmApp()
        cls.app.register(None)
        cls.app.do_startup() if cls.app.theme is None else None
        cls.win = appmod.UrpmWindow(cls.app)
        spin(lambda: cls.win.items and not cls.win._loading and not cls.win._checking_updates
             and cls.win._scanned and cls.win._history_loaded)

    @classmethod
    def tearDownClass(cls):
        cls.no_icons.stop()
        cls.no_caches.stop()
        cls.win.destroy()
        cls.patch.stop()
        cls.prefs.stop()

    def visible(self):
        model = self.win.packages.selection.get_model()
        return [model.get_item(i).pkg.name for i in range(model.get_n_items())]

    def setUp(self):
        self.win.search.set_text("")
        self.win.sidebar.explicit_switch.set_active(False)
        self.win.view = "all"
        self.win._refresh_view()
        spin(lambda: len(self.visible()) == 3)

    def test_lists_and_sorts(self):
        self.assertEqual(self.visible(), ["alpha", "beta", "gamma"])
        self.assertEqual(self.win.stat_labels["count"][0].get_label(), "3")

    def test_search(self):
        self.win.search.set_text("SECOND")
        spin(lambda: self.visible() == ["beta"])
        self.win.search.set_text("zzz")
        spin(lambda: self.win.content.get_visible_child_name() == "empty")

    def test_just_mine_and_views(self):
        self.win.sidebar.explicit_switch.set_active(True)
        spin(lambda: self.visible() == ["alpha", "gamma"])
        self.win.sidebar.explicit_switch.set_active(False)
        self.win.view = "updates"
        self.win._refresh_view()
        spin(lambda: self.visible() == ["gamma"])
        self.win.view = "recent"
        self.win._refresh_view()
        spin(lambda: self.visible() == ["alpha"])

    def test_sidebar_has_views_and_source(self):
        ids = []
        row = self.win.sidebar.source_list.get_first_child()
        while row:
            ids.append(getattr(row, "view_id", None))
            row = row.get_next_sibling()
        self.assertEqual([i for i in ids if i], ["all", "updates", "recent", "cleanup",
                                                 "duplicates", "history", "fake", "broken"])

    def sidebar_rows(self):
        rows, row = {}, self.win.sidebar.source_list.get_first_child()
        while row:
            if hasattr(row, "view_id"):
                labels, child = [], row.get_child().get_first_child()
                while child:
                    labels.append(child.get_label())
                    child = child.get_next_sibling()
                rows[row.view_id] = (labels, row.get_tooltip_text())
            row = row.get_next_sibling()
        return rows

    def test_broken_source_is_marked_not_fatal(self):
        labels, tooltip = self.sidebar_rows()["broken"]
        self.assertIn("⚠️", labels)
        self.assertIn("database is locked", tooltip)
        self.assertEqual(len(self.visible()), 3)  # the other source still works

    def test_sidebar_counts_follow_just_mine(self):
        self.assertEqual(self.sidebar_rows()["fake"][0][-1], "3")
        self.win.sidebar.explicit_switch.set_active(True)
        self.assertEqual(self.sidebar_rows()["fake"][0][-1], "2")
        self.assertEqual(self.sidebar_rows()["all"][0][-1], "2")

    def test_version_sort_is_natural(self):
        column = self.win.packages.col_version
        self.win.packages.view.sort_by_column(column, Gtk.SortType.ASCENDING)
        spin(lambda: self.visible() == ["beta", "alpha", "gamma"])  # 1.9 < 1.10 < 10.0
        self.win.packages.view.sort_by_column(self.win.packages.view.get_columns().get_item(0),
                                      Gtk.SortType.ASCENDING)

    def shown_names(self, table=None):
        table = table or self.win.packages
        return [item.pkg.name for item in table.shown_items()]

    def test_every_column_sorts_both_ways(self):
        table, view = self.win.packages, self.win.packages.view
        expected = {  # alpha 1.10 (5 MB), beta 1.9 (10 B), gamma 10.0 (0 B)
            table.col_name: ["alpha", "beta", "gamma"],
            table.col_version: ["beta", "alpha", "gamma"],   # natural: 1.9 < 1.10 < 10.0
            table.col_size: ["gamma", "beta", "alpha"],
        }
        for column, ascending in expected.items():
            view.sort_by_column(column, Gtk.SortType.ASCENDING)
            self.assertEqual(self.shown_names(), ascending, column.get_title())
            view.sort_by_column(column, Gtk.SortType.DESCENDING)
            self.assertEqual(self.shown_names(), ascending[::-1], column.get_title())
        view.sort_by_column(table.col_name, Gtk.SortType.ASCENDING)

    def test_sort_stays_right_when_packages_arrive(self):
        """Progressive loading adds rows while sorted: ranks must be recomputed."""
        from urpm.ui.table import PackageItem, PackageTable
        table = PackageTable(lambda item: True, lambda item: None, lambda: None)
        src = FakeSource()
        table.view.sort_by_column(table.col_version, Gtk.SortType.ASCENDING)
        table.replace_source_items("fake", [PackageItem(Package(n, v, "fake"), src)
                                            for n, v in (("b", "2.0"), ("d", "10.0"))])
        self.assertEqual(self.shown_names(table), ["b", "d"])
        other = type("Other", (FakeSource,), {"id": "other", "label": "Other"})()
        table.replace_source_items("other", [PackageItem(Package(n, v, "other"), other)
                                             for n, v in (("a", "1.0"), ("c", "9.0"))])
        self.assertEqual(self.shown_names(table), ["a", "b", "c", "d"])
        table.replace_source_items("fake", [])   # a source disappears
        self.assertEqual(self.shown_names(table), ["a", "c"])
        table.view.sort_by_column(table.col_name, Gtk.SortType.DESCENDING)
        self.assertEqual(self.shown_names(table), ["c", "a"])

    def test_sorting_on_older_gtk(self):
        """GTK before 4.10 can't tell us the clicked column; sorting must still be right."""
        from urpm.ui import table as table_module
        with mock.patch.object(table_module, "hasattr", create=True, return_value=False):
            table = table_module.PackageTable(lambda item: True, lambda item: None, lambda: None)
        self.assertFalse(table._direct)
        src = FakeSource()
        table.replace_source_items("fake", [table_module.PackageItem(Package(n, v, "fake"), src)
                                            for n, v in (("x", "1.10"), ("y", "1.9"), ("z", "2"))])
        table.view.sort_by_column(table.col_version, Gtk.SortType.ASCENDING)
        self.assertEqual(self.shown_names(table), ["y", "x", "z"])
        table.view.sort_by_column(table.col_name, Gtk.SortType.DESCENDING)
        self.assertEqual(self.shown_names(table), ["z", "y", "x"])

    def test_incremental_search_stays_correct(self):
        steps = [("a", ["alpha", "beta", "gamma"]), ("al", ["alpha"]), ("alp", ["alpha"]),
                 ("al", ["alpha"]), ("", ["alpha", "beta", "gamma"]), ("ga", ["gamma"]),
                 ("gam third", ["gamma"]), ("gam second", []), ("fake beta", ["beta"])]
        for text, expected in steps:
            self.win.search.set_text(text)
            self.win._on_search_changed(self.win.search)
            self.assertEqual(self.visible(), expected, f"search {text!r}")

    def test_update_all(self):
        self.win.view = "updates"
        self.win._refresh_view()
        self.assertTrue(self.win.banner.get_reveal_child())
        with mock.patch.object(self.win, "_run_action") as run:
            self.win._on_update_all()
        title, actions, _msg = run.call_args.args
        self.assertEqual(title, "Updating 1 package…")
        self.assertEqual([a.argv for a in actions], [["true", "update", "gamma"]])
        self.win.view = "all"
        self.win._refresh_view()
        self.assertFalse(self.win.banner.get_reveal_child())

    def test_double_click_opens_app(self):
        with mock.patch("urpm.ui.window.launch") as launch:
            self.win._on_row_activated(self.win.packages.shown.get_item(0))  # alpha
            spin(lambda: launch.called)
        self.assertEqual(launch.call_args.args[0], ["true", "alpha"])
        with mock.patch("urpm.ui.window.launch") as launch:
            self.win._on_row_activated(self.win.packages.shown.get_item(1))  # beta is a dependency, not an app
            spin(lambda: "isn't an app" in self.win.toast_label.get_label())
        launch.assert_not_called()

    def test_compact_layouts(self):
        self.win._apply_layout(2)
        self.assertFalse(self.win.packages.col_updated.get_visible())
        self.assertFalse(self.win.packages.col_source.get_visible())
        self.win.packages.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.win._layout_mode = 2
        self.win._sync_details_visibility()
        self.assertFalse(self.win.details.get_visible())  # empty panel hidden when small
        self.win.packages.selection.set_selected(0)
        self.assertTrue(self.win.details.get_visible())
        self.win._layout_mode = 0
        self.win._apply_layout(0)
        self.assertTrue(self.win.packages.col_updated.get_visible())
        self.assertTrue(self.win.details.get_visible())

    def test_window_size_is_saved(self):
        from urpm.ui import theme
        with mock.patch.object(theme, "save_prefs") as save, \
                mock.patch.object(theme, "load_prefs", return_value={"dark": True}):
            self.win._save_window_state()
        saved = save.call_args.args[0]
        self.assertTrue(saved["dark"])  # other preferences are kept
        self.assertEqual(set(saved["window"]), {"width", "height", "maximized"})

    def test_settings_window_switches_theme(self):
        from urpm.ui import theme
        from urpm.ui.settings import SettingsWindow
        app_theme = self.win.get_application().theme
        start = app_theme.current.id
        with mock.patch.object(theme, "save_prefs") as save, \
                mock.patch.object(theme, "load_prefs", return_value={"dark": False, "x": 1}):
            window = SettingsWindow(self.win, app_theme)
            self.assertEqual(set(window._cards),
                             {"cotton-candy", "midnight", "gotham", "matcha", "aurora"})
            window.choose("gotham")
        self.assertEqual(app_theme.current.id, "gotham")
        self.assertTrue(Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme)
        self.assertTrue(window._cards["gotham"].has_css_class("active"))
        self.assertFalse(window._cards[start].has_css_class("active"))
        saved = save.call_args.args[0]
        self.assertEqual(saved["theme"], "gotham")
        self.assertNotIn("dark", saved)  # the old light/dark key is replaced
        self.assertEqual(saved["x"], 1)  # other settings are kept
        with mock.patch.object(theme, "save_prefs"), mock.patch.object(theme, "load_prefs",
                                                                        return_value={}):
            window.choose(start)
        window.destroy()

    def test_slow_source_does_not_block_the_others(self):
        """A package manager that takes a while must not hold back the rest."""
        from urpm.ui import app as appmod

        class Slow(FakeSource):
            id, label = "slow", "Slow"

            def list_packages(self):
                time.sleep(1.5)
                return [Package("slowpoke", "1", "slow")]

        with mock.patch("urpm.ui.window.available_sources",
                        return_value=[Slow(), FakeSource()]):
            win = appmod.UrpmWindow(self.win.get_application())
            started = time.time()
            spin(lambda: len(win.items) == 3)
            self.assertLess(time.time() - started, 1.0, "fast source waited for the slow one")
            self.assertIn("still loading Slow", win.status.get_label())
            spin(lambda: not win._loading, timeout=10)
            self.assertEqual(len(win.items), 4)
            self.assertNotIn("still loading", win.status.get_label())
        win.destroy()

    def test_untrusted_app_asks_before_opening(self):
        """S2: opening something like a downloaded AppImage shows a warning first."""
        from urpm.ui.window import PackageItem
        item = PackageItem(Package("Firefox", "1", "fake", ident="/home/u/Downloads/F.AppImage"),
                           FakeSource())
        with mock.patch.object(FakeSource, "launch_warning", return_value="downloaded file"), \
                mock.patch("urpm.ui.window.launch") as launch, \
                mock.patch("urpm.ui.window.ConfirmLaunchDialog") as dialog:
            self.win._launch_item(item, ["true"])
            launch.assert_not_called()                     # nothing ran yet
            name, path, reason, on_confirm = dialog.call_args.args[1:]
            self.assertEqual((name, path), ("Firefox", "/home/u/Downloads/F.AppImage"))
            on_confirm()                                   # the user clicked "Open anyway"
            launch.assert_called_once_with(["true"])

    def test_unsafe_package_names_are_refused_in_the_ui(self):
        """S5: a package whose name looks like an option never reaches a dialog."""
        from urpm.sources.base import UnsafeCommand
        self.win.packages.selection.set_selected(0)
        with mock.patch.object(FakeSource, "safe_remove_action",
                               side_effect=UnsafeCommand("refusing '-rf'")), \
                mock.patch("urpm.ui.window.ConfirmRemoveDialog") as dialog:
            self.win._on_remove(None)
        dialog.assert_not_called()
        self.assertIn("refusing", self.win.toast_label.get_label())

    def test_running_command_can_be_cancelled(self):
        """S6: a stuck command can be stopped instead of trapping the user."""
        from urpm.ui.dialogs import CANCELLED, CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing", [Action(["sleep", "30"]),
                                                     Action(["echo", "never"])], done.append)
        spin(lambda: dialog._proc is not None)
        started = time.time()
        dialog.cancel()
        spin(lambda: done, timeout=8)
        self.assertLess(time.time() - started, 6)
        self.assertEqual(done, [False])
        self.assertIn("Cancelled", dialog.status.get_label())
        start, end = dialog.buffer.get_bounds()
        self.assertNotIn("never\n", dialog.buffer.get_text(start, end, False).replace(
            "$ echo never", ""))
        self.assertEqual(CANCELLED, -15)
        dialog.destroy()

    def test_cancel_also_stops_child_processes(self):
        """S6: package managers start helpers (apt runs dpkg); Cancel must stop those too."""
        from urpm.ui.dialogs import CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing",
                               Action(["sh", "-c", "echo started; sleep 30; echo late"]),
                               done.append)
        spin(lambda: dialog._proc is not None)
        time.sleep(0.3)  # let sh start its sleep child
        started = time.time()
        dialog.cancel()
        spin(lambda: done, timeout=10)
        self.assertLess(time.time() - started, 6, "Cancel waited for the child process")
        self.assertIn("Cancelled", dialog.status.get_label())
        dialog.destroy()

    def test_command_output_is_capped(self):
        """S6: a very chatty command can't make the window use unbounded memory."""
        from urpm.ui.dialogs import MAX_LOG_LINES, CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing", Action(["seq", "1", "20000"]), done.append)
        spin(lambda: done, timeout=20)
        self.assertLessEqual(dialog.buffer.get_line_count(), MAX_LOG_LINES + 1)
        start, end = dialog.buffer.get_bounds()
        self.assertTrue(dialog.buffer.get_text(start, end, False).rstrip().endswith("20000"))
        dialog.destroy()

    def test_export_writes_the_shown_packages(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "list.txt")
            with mock.patch("urpm.ui.window.choose_export_file",
                            side_effect=lambda parent, save: save(path)):
                self.win._on_export()
            with open(path) as fh:
                self.assertEqual([line.split("\t")[0] for line in fh], ["alpha", "beta", "gamma"])
            self.assertIn("Exported 3", self.win.toast_label.get_label())
            with mock.patch("urpm.ui.window.choose_export_file",
                            side_effect=lambda parent, save: save("/proc/nope/list.csv")):
                self.win._on_export()
            self.assertIn("Couldn't save", self.win.toast_label.get_label())

    def test_export_file_dialog(self):
        from urpm.ui import dialogs
        saved = []

        class FakeFile:
            def get_path(self):
                return "/tmp/x.csv"

        class FakeDialog:
            def __init__(self, **props):
                self.props = props

            def save(self, parent, cancellable, done):
                done(self, "result")

            def save_finish(self, result):
                return FakeFile()

        with mock.patch.object(dialogs.Gtk, "FileDialog", FakeDialog, create=True):
            dialogs.choose_export_file(self.win, saved.append)
        self.assertEqual(saved, ["/tmp/x.csv"])

    def test_copy_buttons_and_shortcuts_window(self):
        self.win.packages.selection.set_selected(0)  # alpha
        self.win._on_copy_name()
        self.assertIn("Copied", self.win.toast_label.get_label())
        self.win._on_copy_remove()
        self.assertIn("Uninstall command copied", self.win.toast_label.get_label())
        before = set(Gtk.Window.list_toplevels())
        self.win._on_shortcuts()
        opened = [w for w in Gtk.Window.list_toplevels() if w not in before]
        self.assertEqual([w.get_title() for w in opened], ["Keyboard shortcuts"])
        for window in opened:
            window.destroy()

    def test_update_button_runs_one_update(self):
        names = self.shown_names()
        self.win.packages.selection.set_selected(names.index("gamma"))
        with mock.patch.object(self.win, "_run_action") as run:
            self.win._on_update()
        title, actions, _done = run.call_args.args
        self.assertEqual((title, actions[0].argv), ("Updating gamma…", ["true", "update", "gamma"]))

    def test_commands_that_cannot_run_are_copied_instead(self):
        with mock.patch("urpm.ui.window.can_run", return_value="pkexec isn't installed"), \
                mock.patch("urpm.ui.window.CommandDialog") as dialog:
            self.win._run_action("Removing x…", Action(["true"]), "done")
        dialog.assert_not_called()
        self.assertIn("The command was copied", self.win.toast_label.get_label())

    def test_update_check_errors_are_reported(self):
        with mock.patch.object(FakeSource, "check_updates", side_effect=RuntimeError("offline")):
            self.win.check_updates()
            spin(lambda: not self.win._checking_updates)
        self.assertIn("Couldn't check updates for Fake: offline", self.win.toast_label.get_label())
        self.win.check_updates()   # restore the update badges for other tests
        spin(lambda: not self.win._checking_updates)

    def test_remove_preview_when_the_package_manager_fails_or_cannot_tell(self):
        from urpm.ui.dialogs import ConfirmRemoveDialog
        src, pkg = FakeSource(), Package("alpha", "1.10", "fake")

        def labels(dialog):
            found, stack = [], [dialog.sim_box]
            while stack:
                widget = stack.pop()
                if isinstance(widget, Gtk.Label):
                    found.append(widget.get_label())
                child = widget.get_first_child()
                while child:
                    stack.append(child)
                    child = child.get_next_sibling()
            return " ".join(found)

        for patch, expected in (({"side_effect": RuntimeError("locked")}, "says: locked"),
                                ({"return_value": None}, "can't preview")):
            with mock.patch.object(FakeSource, "simulate_remove", **patch):
                dialog = ConfirmRemoveDialog(self.win, pkg, src, Action(["true"]), lambda: None)
                spin(lambda d=dialog: d.confirm.get_sensitive())
            self.assertIn(expected, labels(dialog))
            dialog.destroy()

    def test_can_run_explains_problems(self):
        from urpm.ui.dialogs import can_run
        self.assertIsNone(can_run(Action(["true"])))
        self.assertIn("isn't installed", can_run(Action(["no-such-program-urpm"])))
        with mock.patch("urpm.ui.dialogs.root_binary", return_value=None):
            self.assertIn("pkexec", can_run(Action(["true"], root=True)))
        with mock.patch("urpm.sources.base.shutil.which", return_value=None), \
                mock.patch("urpm.ui.dialogs.root_binary", return_value="/usr/bin/pkexec"):
            self.assertIn("system folder", can_run(Action(["true"], root=True)))

    def test_settings_window_and_theme_toggle(self):
        from urpm.ui import theme
        before = set(Gtk.Window.list_toplevels())
        app_theme = self.win.get_application().theme
        start = app_theme.current.id
        with mock.patch.object(theme, "save_prefs"), mock.patch.object(theme, "load_prefs",
                                                                        return_value={}):
            self.win._on_settings()
            app_theme.toggle()
            self.assertNotEqual(app_theme.current.dark, theme.THEMES[start].dark)
            app_theme.set(start)
        for window in set(Gtk.Window.list_toplevels()) - before:
            window.destroy()

    def test_window_width_picks_the_layout(self):
        from urpm.ui.window import layout_mode
        self.assertEqual([layout_mode(w) for w in (1400, 1200, 900)], [0, 1, 2])

    def test_cleanup_page(self):
        rows = self.sidebar_rows()
        self.assertEqual(rows["cleanup"][0][-1], "5.0 KB")         # badge: space to free
        self.assertEqual(rows["duplicates"][0][-1], "0")
        self.win.view = "cleanup"
        self.win._refresh_view()
        self.assertEqual(self.win.content.get_visible_child_name(), "cleanup")
        self.assertFalse(self.win.stats.get_visible())
        self.assertFalse(self.win.details.get_visible())            # no details on tool pages
        self.assertFalse(self.win.sidebar.get_hexpand())            # sidebar keeps its width
        page = self.win.cleanup_page
        self.assertEqual(page.title.get_label(), "You can free up 5.0 KB")
        self.assertEqual([c.cleanup.id for c in self.children(page.cards)], ["fake-cache"])
        with mock.patch("urpm.ui.window.ConfirmCleanupDialog") as dialog, \
                mock.patch.object(self.win, "_run_action") as run:
            self.win._review_cleanup(page.cleanups[0])
            dialog.call_args.args[2]()                              # the user confirmed
        title, actions, done = run.call_args.args
        self.assertEqual([a.argv for a in actions], [["true", "clean"]])
        self.assertIn("5.0 KB freed", done)
        self.win.view = "all"
        self.win._refresh_view()
        self.assertTrue(self.win.stats.get_visible())
        self.assertTrue(self.win.details.get_visible())
        self.assertEqual(self.win.content.get_visible_child_name(), "table")

    @staticmethod
    def children(box):
        found, child = [], box.get_first_child()
        while child:
            found.append(child)
            child = child.get_next_sibling()
        return found

    def test_cleanup_with_option_like_names_is_refused(self):
        bad = Cleanup("bad", "Bad", "", "Fake", 1, ["--force-everything"],
                      [Action(["true", "--force-everything"])])
        with mock.patch("urpm.ui.window.ConfirmCleanupDialog") as dialog:
            self.win._review_cleanup(bad)
        dialog.assert_not_called()
        self.assertIn("refusing", self.win.toast_label.get_label())

    def test_cleanup_dialog_shows_what_will_go(self):
        from urpm.ui.dialogs import ConfirmCleanupDialog
        item = Cleanup("x", "Unused packages", "", "APT", 2_000_000,
                       [f"lib{n}" for n in range(70)],
                       [Action(["apt-get", "autoremove", "-y"], root=True,
                               pretty="sudo apt autoremove")])
        confirmed = []
        dialog = ConfirmCleanupDialog(self.win, item, lambda: confirmed.append(True))
        text = " ".join(w.get_label() for w in self.labels_in(dialog))
        self.assertIn("These packages will be removed", text)
        self.assertIn("lib59", text)
        self.assertIn("… and 10 more", text)                       # long lists are shortened
        self.assertIn("sudo apt autoremove", text)
        self.assertIn("password", text)
        self.assertEqual(dialog.confirm.get_label(), "Free up 2.0 MB")
        self.assertIsNot(dialog.get_focus(), dialog.confirm)
        dialog.confirm.emit("clicked")
        self.assertEqual(confirmed, [True])

    def labels_in(self, widget):
        """Every label inside widget, in the order they appear on screen."""
        found = [widget] if isinstance(widget, Gtk.Label) else []
        for child in self.children(widget):
            found.extend(self.labels_in(child))
        return found

    def test_needed_by_in_details(self):
        names = self.shown_names()
        self.win.packages.selection.set_selected(names.index("beta"))
        spin(lambda: self.win.details.needed_by.get_visible())
        self.assertEqual(self.win.details.needed_by.get_label(), "1 installed package: alpha")
        self.win.packages.selection.set_selected(names.index("gamma"))
        spin(lambda: self.win.details.needed_by.get_label() == "Nothing else installed needs this.")
        from urpm.ui.details import describe_needed_by
        self.assertIn("can probably be removed", describe_needed_by([], explicit=False))
        self.assertIsNone(describe_needed_by(None, explicit=True))
        self.assertIn("… and 5 more", describe_needed_by([f"p{n}" for n in range(45)], True))

    def test_duplicates_page(self):
        from urpm.duplicates import DuplicateGroup
        from urpm.ui.table import PackageItem
        other = type("Other", (FakeSource,), {"id": "other", "label": "Other", "emoji": "🎁"})()
        copies = [PackageItem(Package("Alpha", "1", "fake", size=300), FakeSource()),
                  PackageItem(Package("alpha", "1", "other", size=100), other)]
        page = self.win.duplicates_page
        page.show_groups([DuplicateGroup("Alpha", copies)])
        self.assertEqual(page.title.get_label(), "1 app is installed more than once")
        self.assertIn("could free 100 B", page.subtitle.get_label())
        buttons = [w for w in self.walk(page) if isinstance(w, Gtk.Button)]
        removes = [b for b in buttons if b.get_label() == "Remove this copy"]
        self.assertEqual(len(removes), 2)
        with mock.patch.object(self.win, "_remove_item") as remove:
            page._on_remove = self.win._remove_item
            for button in removes:
                button.emit("clicked")
        self.assertEqual({id(c.args[0]) for c in remove.call_args_list},
                         {id(c) for c in copies})                   # each removes its own copy
        page.show_groups([])
        self.assertEqual(page.title.get_label(), "🎉 No duplicates")

    def walk(self, widget):
        stack, found = [widget], []
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(self.children(current))
        return found

    def test_show_item_jumps_from_a_tool_page_to_the_package(self):
        self.win.view = "duplicates"
        self.win._refresh_view()
        gamma = next(i for i in self.win.items if i.pkg.name == "gamma")
        self.win._show_item(gamma)
        self.assertEqual(self.win.view, "all")
        self.assertEqual(self.win.packages.selected().pkg.name, "gamma")

    def test_icons_in_rows_and_details(self):
        import tempfile
        from gi.repository import Gdk, GLib
        from urpm.ui.widgets import show_icon
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "icon.png")
            pixels = GLib.Bytes.new(bytes([255, 0, 128, 255]) * 16 * 16)
            Gdk.MemoryTexture.new(16, 16, Gdk.MemoryFormat.R8G8B8A8, pixels, 64).save_to_png(path)
            image = Gtk.Image()
            self.assertTrue(show_icon(image, path))
            self.assertFalse(show_icon(image, os.path.join(tmp, "missing.png")))
            self.assertFalse(show_icon(image, "no-such-icon-name-urpm"))
            self.assertFalse(show_icon(image, ""))
            alpha = next(i for i in self.win.items if i.pkg.name == "alpha")
            alpha.pkg.icon = path
            self.win.packages.rows_changed()
            self.win.packages.selection.set_selected(self.shown_names().index("alpha"))
            self.assertTrue(self.win.details.icon.get_visible())
            self.assertFalse(self.win.details.avatar.get_visible())
            alpha.pkg.icon = ""

    def test_history_page(self):
        self.assertEqual(self.sidebar_rows()["history"][0][-1], "4")
        self.win.view = "history"
        self.win._refresh_view()
        self.assertEqual(self.win.content.get_visible_child_name(), "history")
        page = self.win.history_page

        def shown():
            return [w.get_label() for w in self.labels_in(page.list)
                    if "pkg-name" in w.get_css_classes()]
        self.assertEqual(page.title.get_label(), "4 changes")
        self.assertEqual(shown(), ["gone", "libdep", "alpha", "gamma"])        # newest first
        headings = [w.get_label() for w in self.labels_in(page.list)
                    if "section-heading" in w.get_css_classes()]
        self.assertEqual(headings[0], "TODAY")
        page.chips["removed"].set_active(True)
        self.assertEqual(shown(), ["gone"])
        page.chips["updated"].set_active(True)
        self.assertEqual(shown(), ["gamma"])
        page.chips["all"].set_active(True)
        self.win.sidebar.explicit_switch.set_active(True)                     # "Just mine"
        self.assertNotIn("libdep", shown())
        self.win.sidebar.explicit_switch.set_active(False)
        self.win.search.set_text("alp")
        self.win._on_search_changed(self.win.search)
        self.assertEqual(shown(), ["alpha"])
        self.win.search.set_text("")
        self.win._on_search_changed(self.win.search)
        events = {e.name: e for e in page.events}
        self.win._show_event_package(events["gone"])
        self.assertIn("isn't installed any more", self.win.toast_label.get_label())
        self.win._show_event_package(events["alpha"])
        self.assertEqual((self.win.view, self.win.packages.selected().pkg.name), ("all", "alpha"))

    def test_history_paging_and_day_labels(self):
        from urpm.history import Event
        from urpm.ui.history_page import HistoryPage, day_label
        now = time.mktime(time.strptime("2026-10-11 12:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(day_label(now - 3600, now), "Today")
        self.assertEqual(day_label(now - 86400, now), "Yesterday")
        self.assertEqual(day_label(now - 5 * 86400, now), "Tuesday, Oct 06, 2026")
        page = HistoryPage(lambda event: None)
        page.set_events([Event(time.time() - n, "installed", f"p{n}") for n in range(600)])
        self.assertTrue(page.more.get_visible())
        self.assertEqual(page.more.get_label(), "Show more (350 left)")
        page.more.emit("clicked")
        self.assertEqual(page.more.get_label(), "Show more (100 left)")
        page.set_events([])
        self.assertEqual(page.title.get_label(), "No history found")

    def test_security_updates_stand_out(self):
        names = self.shown_names()
        self.win.packages.selection.set_selected(names.index("gamma"))
        badges = [w.get_label() for w in self.labels_in(self.win.details.badges)]
        self.assertIn("🛡 security update", badges)
        self.assertEqual(self.win.stat_labels["updates"][1].get_label(), "updates · 🛡 1")
        self.win.view = "updates"
        self.win._refresh_view()
        self.assertIn("🛡 1 security", self.win.banner_label.get_label())
        self.win.view = "all"
        self.win._refresh_view()

    def test_hold_and_release(self):
        names = self.shown_names()
        self.win.packages.selection.set_selected(names.index("alpha"))
        details = self.win.details
        self.assertTrue(details.hold_button.get_visible())
        self.assertEqual(details.hold_button.get_label(), "📌 Keep this version")
        with mock.patch.object(self.win, "_run_action") as run:
            self.win._on_hold()
        title, action, done = run.call_args.args
        self.assertEqual((action.argv, done), (["true", "hold", "alpha"], "📌 alpha stays at 1.10"))
        gamma = next(i for i in self.win.items if i.pkg.name == "gamma")
        gamma.pkg.held = True                                    # as if a hold took effect
        try:
            self.win.packages.selection.set_selected(self.shown_names().index("gamma"))
            self.assertFalse(details.update_button.get_visible())   # held: no update button
            self.assertEqual(details.hold_button.get_label(), "📌 Allow updates again")
            self.assertIn("📌 held", [w.get_label() for w in self.labels_in(details.badges)])
            self.assertEqual(self.win._updatable(), [])               # Update all skips it
            with mock.patch.object(self.win, "_run_action") as run:
                self.win._on_update()                                 # ignored while held
                self.win._on_hold()
            self.assertEqual(run.call_args.args[1].argv, ["true", "unhold", "gamma"])
        finally:
            gamma.pkg.held = False

    def test_background_check_setting(self):
        from urpm.ui.settings import SettingsWindow
        with mock.patch("urpm.ui.settings.schedule") as schedule:
            schedule.is_enabled.return_value = False
            schedule.enable.return_value = "systemd"
            window = SettingsWindow(self.win, self.win.get_application().theme)
            window.background_switch.set_active(True)
            spin(lambda: window.background_status.get_visible())
            self.assertIn("every 6 hours", window.background_status.get_label())
            schedule.enable.assert_called_once()
            schedule.disable.side_effect = RuntimeError("systemctl said no")
            window.background_switch.set_active(False)
            spin(lambda: "Couldn't" in window.background_status.get_label())
            self.assertTrue(window.background_switch.get_active())     # put back
        window.destroy()

    def test_multiple_actions_stop_at_first_failure(self):
        from urpm.ui.dialogs import CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing", [Action(["echo", "one"]), Action(["false"]),
                                                     Action(["echo", "three"])], done.append)
        spin(lambda: done)
        start, end = dialog.buffer.get_bounds()
        output = dialog.buffer.get_text(start, end, False)
        self.assertEqual(done, [False])
        self.assertIn("one", output)
        self.assertNotIn("three\n", output.replace("$ echo three", ""))
        dialog.destroy()

    def test_details_panel(self):
        self.win.packages.selection.set_selected(2)  # gamma
        spin(lambda: self.win.details.description.get_label() == "All about gamma")
        self.assertTrue(self.win.details.update_button.get_visible())
        self.assertTrue(self.win.details.remove_button.get_visible())
        self.assertIn("3.1", self.win.details.version_label.get_label())
        self.win.details.load_files()
        spin(lambda: self.win.details.files_scroller.get_visible())
        start, end = self.win.details.files_buffer.get_bounds()
        self.assertEqual(self.win.details.files_buffer.get_text(start, end, False), "/usr/bin/gamma")

    def test_run_action_reloads(self):
        from urpm.ui.dialogs import CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing", Action(["echo", "hello"]), done.append)
        spin(lambda: done)
        self.assertEqual(done, [True])
        start, end = dialog.buffer.get_bounds()
        self.assertEqual(dialog.buffer.get_text(start, end, False).strip(), "hello")
        dialog.destroy()

    def test_confirm_dialog_previews_extra_removals(self):
        from urpm.ui.dialogs import ConfirmRemoveDialog
        src = FakeSource()
        pkg = Package("alpha", "1.10", "fake")
        confirmed = []
        with mock.patch.object(FakeSource, "simulate_remove",
                               return_value=["alpha", "alpha-data", "libalpha"]):
            dialog = ConfirmRemoveDialog(self.win, pkg, src, src.remove_action(pkg),
                                         lambda: confirmed.append(True))
            spin(lambda: dialog.confirm.get_sensitive())
        self.assertEqual(dialog.confirm.get_label(), "Remove 3 packages")
        self.assertIsNot(dialog.get_focus(), dialog.confirm)  # never pre-focus the scary button
        self.assertEqual(confirmed, [])
        dialog.confirm.emit("clicked")
        self.assertEqual(confirmed, [True])

    def test_failed_action_reports_failure(self):
        from urpm.ui.dialogs import CommandDialog
        done = []
        dialog = CommandDialog(self.win, "Testing", Action(["false"]), done.append)
        spin(lambda: done)
        self.assertEqual(done, [False])
        self.assertIn("went wrong", dialog.status.get_label())
        dialog.destroy()


if __name__ == "__main__":
    unittest.main()
