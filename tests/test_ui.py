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
                Package("gamma", "10.0", "fake", summary="third", homepage="https://example.org")]

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
        spin(lambda: cls.win.items and not cls.win._checking_updates)

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
