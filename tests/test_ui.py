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

from urpm.sources.base import DEV, Action, Details, Package, Source


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
        return 1

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
        cls.app = appmod.UrpmApp()
        cls.app.register(None)
        cls.app.do_startup() if cls.app.theme is None else None
        cls.win = appmod.UrpmWindow(cls.app)
        spin(lambda: cls.win.items and not cls.win._loading and not cls.win._checking_updates)

    @classmethod
    def tearDownClass(cls):
        cls.win.destroy()
        cls.patch.stop()
        cls.prefs.stop()

    def visible(self):
        model = self.win.selection.get_model()
        return [model.get_item(i).pkg.name for i in range(model.get_n_items())]

    def setUp(self):
        self.win.search.set_text("")
        self.win.explicit_switch.set_active(False)
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
        self.win.explicit_switch.set_active(True)
        spin(lambda: self.visible() == ["alpha", "gamma"])
        self.win.explicit_switch.set_active(False)
        self.win.view = "updates"
        self.win._refresh_view()
        spin(lambda: self.visible() == ["gamma"])
        self.win.view = "recent"
        self.win._refresh_view()
        spin(lambda: self.visible() == ["alpha"])

    def test_sidebar_has_views_and_source(self):
        ids = []
        row = self.win.source_list.get_first_child()
        while row:
            ids.append(getattr(row, "view_id", None))
            row = row.get_next_sibling()
        self.assertEqual([i for i in ids if i], ["all", "updates", "recent", "fake", "broken"])

    def sidebar_rows(self):
        rows, row = {}, self.win.source_list.get_first_child()
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
        self.win.explicit_switch.set_active(True)
        self.assertEqual(self.sidebar_rows()["fake"][0][-1], "2")
        self.assertEqual(self.sidebar_rows()["all"][0][-1], "2")

    def test_version_sort_is_natural(self):
        column = self.win.col_version
        self.win.table.sort_by_column(column, Gtk.SortType.ASCENDING)
        spin(lambda: self.visible() == ["beta", "alpha", "gamma"])  # 1.9 < 1.10 < 10.0
        self.win.table.sort_by_column(self.win.table.get_columns().get_item(0),
                                      Gtk.SortType.ASCENDING)

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
            self.win._on_row_activated(self.win.table, 0)  # alpha
            spin(lambda: launch.called)
        self.assertEqual(launch.call_args.args[0], ["true", "alpha"])
        with mock.patch("urpm.ui.window.launch") as launch:
            self.win._on_row_activated(self.win.table, 1)  # beta is a dependency, not an app
            spin(lambda: "isn't an app" in self.win.toast_label.get_label())
        launch.assert_not_called()

    def test_compact_layouts(self):
        self.win._apply_layout(2)
        self.assertFalse(self.win.col_updated.get_visible())
        self.assertFalse(self.win.col_source.get_visible())
        self.win.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.win._layout_mode = 2
        self.win._sync_details_visibility()
        self.assertFalse(self.win.details.get_visible())  # empty panel hidden when small
        self.win.selection.set_selected(0)
        self.assertTrue(self.win.details.get_visible())
        self.win._layout_mode = 0
        self.win._apply_layout(0)
        self.assertTrue(self.win.col_updated.get_visible())
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
        self.win.selection.set_selected(0)
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
        self.win.selection.set_selected(2)  # gamma
        spin(lambda: self.win.d_description.get_label() == "All about gamma")
        self.assertTrue(self.win.b_update.get_visible())
        self.assertTrue(self.win.b_remove.get_visible())
        self.assertIn("3.1", self.win.d_version.get_label())
        self.win._on_show_files(None)
        spin(lambda: self.win.files_scroller.get_visible())
        start, end = self.win.files_buffer.get_bounds()
        self.assertEqual(self.win.files_buffer.get_text(start, end, False), "/usr/bin/gamma")

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
