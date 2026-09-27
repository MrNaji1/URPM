"""Drives the real window with a fake package source. Skipped without GTK 4 or a display.

In CI it runs under a virtual display:  xvfb-run python3 -m unittest discover tests
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
        return [Package("alpha", "1.0", "fake", summary="first letter", size=5_000_000,
                        installed=now),
                Package("beta", "2.0", "fake", summary="second", size=10, explicit=False),
                Package("gamma", "3.0", "fake", summary="third", homepage="https://example.org")]

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

    def update_action(self, pkg):
        return Action(["true", "update", pkg.name]) if pkg.update else None


def spin(until, timeout=10.0):
    """Run the GTK main loop until `until()` is true."""
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while not until():
        if time.time() > end:
            raise AssertionError("timed out waiting for the UI")
        ctx.iteration(False)
        time.sleep(0.005)


@unittest.skipUnless(HAVE_GTK and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                     "needs GTK 4 and a display")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from urpm.ui import app as appmod, theme
        cls.prefs = mock.patch.object(theme, "save_prefs")  # never touch the real config
        cls.prefs.start()
        cls.patch = mock.patch("urpm.ui.window.available_sources", return_value=[FakeSource()])
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
        self.assertEqual([i for i in ids if i], ["all", "updates", "recent", "fake"])

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
        pkg = Package("alpha", "1.0", "fake")
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
