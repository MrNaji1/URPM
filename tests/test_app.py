"""Command-line options, saved settings and shared helpers. No windows are opened."""

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from urpm.sources import base
from urpm.sources.base import Action, Package, Source, UnsafeCommand

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from urpm.ui import app, theme
except (ImportError, ValueError):
    app = theme = None


@unittest.skipIf(app is None, "needs PyGObject with GTK 4")
class CommandLineTests(unittest.TestCase):
    def run_main(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = app.main(["urpm", *args])
        return code, out.getvalue()

    def test_version_and_help(self):
        from urpm import __version__
        self.assertEqual(self.run_main("--version"), (0, f"URPM {__version__}\n"))
        code, text = self.run_main("--help")
        self.assertEqual(code, 0)
        self.assertIn("--install-desktop", text)

    def test_install_and_remove_desktop_entry(self):
        with tempfile.TemporaryDirectory() as data, \
                mock.patch.object(app.GLib, "get_user_data_dir", return_value=data):
            code, text = self.run_main("--install-desktop")
            self.assertEqual(code, 0)
            entry = Path(data, "applications", "io.github.urpm.desktop").read_text()
            self.assertIn("Exec=", entry)
            self.assertNotIn("Exec=urpm\n", entry)          # points at this install
            self.assertTrue(Path(data, "icons/hicolor/scalable/apps/urpm.svg").exists())
            code, text = self.run_main("--uninstall-desktop")
            self.assertEqual(code, 0)
            self.assertFalse(Path(data, "applications", "io.github.urpm.desktop").exists())


@unittest.skipIf(theme is None, "needs PyGObject with GTK 4")
class PreferencesTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, "urpm", "settings.json")
            with mock.patch.object(theme, "CONFIG", config):
                self.assertEqual(theme.load_prefs(), {})             # nothing saved yet
                theme.save_prefs({"theme": "gotham"})
                self.assertEqual(theme.load_prefs(), {"theme": "gotham"})
                config.write_text("{not json")
                self.assertEqual(theme.load_prefs(), {})             # a broken file is ignored
            with mock.patch.object(theme, "CONFIG", Path("/proc/forbidden/settings.json")):
                theme.save_prefs({"theme": "gotham"})                # fails quietly

    def test_system_dark_preference(self):
        with mock.patch.dict(os.environ, {"GTK_THEME": "Mint-Y-Dark-Aqua"}):
            self.assertTrue(theme.system_prefers_dark())


class _Minimal(Source):
    id, label, emoji = "minimal", "Minimal", "🧩"

    def list_packages(self):
        return []


class HelperTests(unittest.TestCase):
    def test_source_defaults(self):
        src, pkg = _Minimal(), Package("x", "1", "minimal")
        self.assertEqual(src.details(pkg).description, "")
        self.assertIsNone(src.files(pkg))
        self.assertIsNone(src.remove_action(pkg))
        self.assertIsNone(src.safe_remove_action(pkg))
        self.assertIsNone(src.simulate_remove(pkg))
        self.assertEqual(src.check_updates([pkg]), 0)
        self.assertEqual(src.safe_update_actions([pkg]), [])
        self.assertIsNone(src.launch_argv(pkg))
        self.assertIsNone(src.launch_warning(pkg))
        self.assertEqual(src.remove_command(pkg), "")
        self.assertFalse(src.available())                       # no binary set

    def test_which_checks_common_extra_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder, "mytool")
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}), \
                    mock.patch.object(base, "EXTRA_PATHS", (folder,)):
                self.assertEqual(base.which("mytool"), str(tool))
                self.assertIsNone(base.which("not-there"))
        self.assertIsNone(base.which(""))

    def test_run_reports_errors_clearly(self):
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            base.run("definitely-not-a-real-program-urpm")
        with self.assertRaisesRegex(RuntimeError, "sh: boom"):
            base.run("sh", "-c", "echo boom >&2; exit 3")
        with self.assertRaisesRegex(RuntimeError, "exited with 4"):
            base.run("sh", "-c", "exit 4")
        self.assertEqual(base.run("sh", "-c", "echo hi; exit 1", ok_codes=(0, 1)), "hi\n")
        with mock.patch.object(base.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)), \
                self.assertRaisesRegex(RuntimeError, "timed out"):
            base.run("sleep", "10", timeout=1)

    def test_file_helpers(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a").write_text("12345")
            Path(folder, "sub").mkdir()
            Path(folder, "sub", "b").write_text("123")
            os.symlink("/nonexistent/target", Path(folder, "dangling"))
            self.assertGreaterEqual(base.dir_size(folder), 8)
            self.assertTrue(base.can_write(folder))
        self.assertIsNone(base.mtime("/nonexistent/file"))

    def test_desktop_launcher_skips_hidden_and_unreadable_entries(self):
        with tempfile.TemporaryDirectory() as root:
            apps = Path(root, "share", "applications")
            apps.mkdir(parents=True)
            hidden = apps / "hidden.desktop"
            hidden.write_text("[Desktop Entry]\nType=Application\nNoDisplay=true\n")
            missing = str(apps / "missing.desktop")
            with mock.patch.object(base, "which", return_value="/usr/bin/gtk-launch"):
                self.assertIsNone(base.desktop_launcher([str(hidden), missing, "/usr/bin/x"]))
            with mock.patch.object(base, "which", return_value=None):
                self.assertIsNone(base.desktop_launcher([str(hidden)]))

    def test_root_binary(self):
        self.assertIsNone(base.root_binary(os.path.expanduser("~/bin/sh")))
        self.assertEqual(base.root_binary("/bin/sh"), "/bin/sh")
        with mock.patch.object(base.shutil, "which", return_value=None), \
                self.assertRaises(UnsafeCommand):
            Action(["anything"], root=True).full_argv()


class PipDetailsTests(unittest.TestCase):
    def test_details_files_and_removal(self):
        from urpm.sources.pip import PipSource
        with tempfile.TemporaryDirectory() as site:
            info = Path(site, "demo-1.0.dist-info")
            info.mkdir()
            (info / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nSummary: A demo\n"
                "License: MIT\nAuthor: Someone\nRequires-Dist: requests\n"
                "Project-URL: Source, https://example.org/demo\n\nThe long description.\n")
            (info / "RECORD").write_text("demo.py,,10\n")
            Path(site, "demo.py").write_text("x" * 10)
            src = PipSource()
            src.site = site
            (pkg,) = src.list_packages()
            self.assertEqual(pkg.homepage, "https://example.org/demo")  # from Project-URL
            self.assertTrue(pkg.explicit)                     # no REQUESTED files at all
            details = src.details(pkg)
            self.assertEqual(details.description, "The long description.")
            self.assertEqual(dict(details.fields)["Requires"], "requests")
            self.assertTrue(src.files(pkg)[0].endswith("demo.py"))
            with mock.patch("urpm.sources.pip.importlib.util.find_spec", return_value=True), \
                    mock.patch("urpm.sources.pip.externally_managed", return_value=True):
                action = src.remove_action(pkg)
            self.assertIn("--break-system-packages", action.argv)
            self.assertEqual(action.display(), "python3 -m pip uninstall demo")
            gone = Package("gone", "1", "pip", ident="gone")
            self.assertEqual(src.details(gone).description, "")
            self.assertEqual(src.files(gone), [])
            with mock.patch("urpm.sources.pip.importlib.util.find_spec", return_value=None):
                self.assertIsNone(src.remove_action(pkg))
                self.assertEqual(src.check_updates([pkg]), 0)
                self.assertEqual(src.update_actions([pkg]), [])


if __name__ == "__main__":
    unittest.main()
