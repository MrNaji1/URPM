"""Phase 3 features on the fake system: Cleanup centre, "needed by", icons, duplicates."""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import TOOLS, FakeSystem  # noqa: E402

from urpm import cleanup, icons  # noqa: E402
from urpm.duplicates import find_duplicates, name_keys  # noqa: E402
from urpm.sources import (ApkSource, DpkgSource, FlatpakSource, PacmanSource,  # noqa: E402
                          RpmSource, SnapSource, XbpsSource)
from urpm.sources.base import Package, Source  # noqa: E402


class FakeSystemTest(unittest.TestCase):
    tools = TOOLS

    def setUp(self):
        self.fake = FakeSystem(self.tools).__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.root = str(self.fake.root)

    def cleanups(self, src):
        return {c.id: c for c in src.cleanups(src.list_packages())}

    def assert_read_only(self):
        """Finding cleanups must never clean anything."""
        destructive = ("clean", "purge", "autoremove", "-Rns", "uninstall", "remove", "-rk1")
        for call in self.fake.calls():
            joined = " ".join(call)
            if call[:2] == ["apt-get", "-s"] or "cleanup -n" in joined:
                continue  # simulations / dry runs
            self.assertFalse(any(word in call for word in destructive), f"ran {joined}")


class CleanupTests(FakeSystemTest):
    def test_debian(self):
        found = self.cleanups(DpkgSource())
        unused = found["apt-autoremove"]
        self.assertEqual(unused.items, ["libgtk-4-1", "old-helper"])
        self.assertEqual(unused.size, 9000 * 1024)       # libgtk's size; old-helper is unknown
        self.assertEqual(unused.actions[0].display(), "sudo apt autoremove")
        self.assertTrue(unused.actions[0].root)
        downloads = found["apt-cache"]
        self.assertEqual((downloads.size, downloads.kind), (5000, "cache"))  # only .deb files
        self.assertEqual(downloads.actions[0].display(), "sudo apt clean")
        self.assert_read_only()

    def test_arch(self):
        found = self.cleanups(PacmanSource())
        orphans = found["pacman-orphans"]
        self.assertEqual(orphans.items, ["bash"])
        self.assertEqual(orphans.size, int(9.41 * 1024**2))
        self.assertEqual(orphans.actions[0].argv, ["pacman", "-Rns", "--noconfirm", "bash"])
        cache = found["pacman-cache"]
        self.assertEqual(cache.size, int(120.36 * 1024**2))  # from paccache's dry run
        self.assertEqual(cache.actions[0].argv, ["paccache", "-rk1"])
        self.assert_read_only()

    def test_arch_without_paccache(self):
        os.remove(self.fake.bin / "paccache")
        self.assertNotIn("pacman-cache", self.cleanups(PacmanSource()))

    def test_fedora(self):
        found = self.cleanups(RpmSource())
        unused = found["dnf-autoremove"]
        self.assertEqual(unused.items, ["bash", "old-lib"])           # duplicates removed
        self.assertEqual(unused.size, 8123456)
        self.assertEqual(unused.actions[0].display(), "sudo dnf autoremove")
        self.assertEqual(found["dnf-cache"].size, 7000)
        self.assert_read_only()

    def test_flatpak(self):
        found = self.cleanups(FlatpakSource())
        runtimes = found["flatpak-unused-runtimes"]
        # GNOME 45 is unused; Freedesktop 24.08 is used by Notes; GL drivers are never flagged.
        self.assertEqual(runtimes.items, ["GNOME Application Platform (45)"])
        self.assertEqual(runtimes.size, 900_000_000)
        self.assertEqual(runtimes.actions[0].argv, ["flatpak", "uninstall", "-y",
                                                    "--noninteractive", "--system",
                                                    "org.gnome.Platform/x86_64/45"])
        leftover = found["flatpak-leftover-data"]
        old = str(self.fake.home / ".var/app/org.old.Removed")
        self.assertEqual(leftover.items, [old])                # Notes is still installed
        self.assertEqual((leftover.size, leftover.kind), (6000, "data"))
        self.assertEqual(leftover.actions[0].argv, ["gio", "trash", old])  # recoverable
        self.assert_read_only()

    def test_snap(self):
        old = self.cleanups(SnapSource())["snap-old-revisions"]
        self.assertEqual(old.items, ["firefox (revision 4800)"])
        self.assertEqual(old.size, 1900)
        self.assertEqual(old.actions[0].display(), "sudo snap remove firefox --revision=4800")
        self.assert_read_only()

    def test_developer_caches(self):
        with mock.patch("urpm.cleanup.which", side_effect=lambda n: str(self.fake.bin / n)):
            found = {c.id: c for c in filter(None, (check() for check in cleanup.CACHES))}
        self.assertEqual(found["pip-cache"].size, 3000)
        self.assertEqual(found["pip-cache"].actions[0].argv,
                         ["python3", "-m", "pip", "cache", "purge"])
        self.assertEqual(found["npm-cache"].size, 9000)
        self.assertEqual(found["npm-cache"].actions[0].display(), "npm cache clean --force")
        cargo = found["cargo-cache"]
        self.assertEqual(cargo.size, 5000)
        self.assertEqual(cargo.actions[0].argv[:3], ["rm", "-rf", "--"])
        self.assertTrue(all(p.startswith(self.root) for p in cargo.actions[0].argv[3:]))
        self.assertEqual(found["brew-cleanup"].size, 12_500_000)  # from brew's dry run
        self.assert_read_only()

    def test_caches_are_skipped_when_missing_empty_or_failing(self):
        with mock.patch("urpm.cleanup.which", return_value=None):
            self.assertEqual([check() for check in (cleanup.pip_cache, cleanup.npm_cache,
                                                    cleanup.brew_cache)], [None] * 3)
        for folder in ("pip-cache", "npm-cache/_cacache", "cargo/registry"):
            for path in Path(self.root, folder).rglob("*"):
                if path.is_file():
                    path.unlink()                                  # caches emptied
        with mock.patch("urpm.cleanup.which", side_effect=lambda n: str(self.fake.bin / n)):
            self.assertIsNone(cleanup.pip_cache())
            self.assertIsNone(cleanup.npm_cache())
            self.assertIsNone(cleanup.cargo_cache())
            with mock.patch("urpm.cleanup.run", side_effect=RuntimeError("pip: no cache")):
                self.assertIsNone(cleanup.pip_cache())             # pip without a cache
            with mock.patch("urpm.cleanup.run", return_value="Nothing to clean.\n"):
                self.assertIsNone(cleanup.brew_cache())

        def broken_cache():
            raise RuntimeError("disk on fire")
        with mock.patch.object(cleanup, "CACHES", (broken_cache,)):
            found, errors = cleanup.scan({})
        self.assertEqual((found, errors), ([], ["broken cache: disk on fire"]))

    def test_scan_sorts_biggest_first_and_survives_errors(self):
        class Broken(Source):
            id, label = "broken", "Broken"

            def list_packages(self):
                return []

            def cleanups(self, packages):
                raise RuntimeError("locked")

        flatpak = FlatpakSource()
        with mock.patch.object(cleanup, "CACHES", ()):
            found, errors = cleanup.scan({flatpak: flatpak.list_packages(), Broken(): []})
        self.assertEqual([c.id for c in found],
                         ["flatpak-unused-runtimes", "flatpak-leftover-data"])
        self.assertEqual(errors, ["Broken: locked"])


class NeededByTests(FakeSystemTest):
    def test_each_package_manager(self):
        apt = DpkgSource()
        pkgs = {p.name: p for p in apt.list_packages()}
        self.assertEqual(apt.required_by(pkgs["libgtk-4-1:amd64"]),
                         ["firefox", "gnome-text-editor"])          # de-duplicated, "|" gone
        self.assertEqual(apt.required_by(pkgs["firefox"]), [])
        pacman = PacmanSource()
        pkgs = {p.name: p for p in pacman.list_packages()}
        self.assertEqual(pacman.required_by(pkgs["bash"]), ["firefox", "readline"])
        self.assertEqual(pacman.required_by(pkgs["firefox"]), [])   # "None"
        rpm = RpmSource()
        gimp = {p.name: p for p in rpm.list_packages()}["gimp"]
        self.assertEqual(rpm.required_by(gimp), ["gimp-plugin-x"])
        xbps = XbpsSource()
        vim = {p.name: p for p in xbps.list_packages()}["vim"]
        self.assertEqual(xbps.required_by(vim), ["vim-colorschemes"])
        apk = ApkSource()
        vim = {p.name: p for p in apk.list_packages()}["vim"]
        self.assertEqual(apk.required_by(vim), ["vim-airline"])
        flatpak = FlatpakSource()
        pkgs = {p.name: p for p in flatpak.list_packages()}
        self.assertEqual(flatpak.required_by(pkgs["Freedesktop Platform"]), ["Notes"])
        self.assertEqual(flatpak.required_by(pkgs["GNOME Application Platform"]), [])
        self.assertIsNone(flatpak.required_by(pkgs["Notes"]))       # apps: can't tell


class IconTests(FakeSystemTest):
    def test_icons_come_from_each_packages_desktop_entry(self):
        apps = Path(self.root, "usr/share/applications")
        (apps / "firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\nIcon=firefox\n"
                                              "[Desktop Action new]\nIcon=wrong-section\n")
        (apps / "kde4").mkdir()
        (apps / "kde4" / "old.desktop").write_text("[Desktop Entry]\nIcon=/opt/old/icon.png\n")
        (apps / "noicon.desktop").write_text("[Desktop Entry]\nName=Nothing\n")
        exports = Path(self.root, "flatpak-exports/applications")
        exports.mkdir(parents=True)
        (exports / "org.example.Notes.desktop").write_text("[Desktop Entry]\nIcon=org.example.Notes\n")
        snap_dir = Path(self.root, "snap-desktop")
        (snap_dir / "firefox_firefox.desktop").write_text("[Desktop Entry]\nIcon=/snap/firefox/x.png\n")

        index = icons.build_index([str(apps), str(exports), str(snap_dir)])
        self.assertEqual(index["firefox.desktop"], "firefox")     # main section only
        self.assertEqual(index["kde4-old.desktop"], "/opt/old/icon.png")
        self.assertNotIn("noicon.desktop", index)

        for src, name, icon in ((DpkgSource(), "firefox", "firefox"),
                                (FlatpakSource(), "Notes", "org.example.Notes"),
                                (SnapSource(), "firefox", "/snap/firefox/x.png")):
            pkgs = src.list_packages()
            icons.assign_icons(src, pkgs, index)
            by_name = {p.name: p for p in pkgs}
            self.assertEqual(by_name[name].icon, icon, src.label)
        self.assertTrue(any(call[:2] == ["dpkg-query", "-S"] for call in self.fake.calls()))

    def test_application_dirs_follow_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/home/me/.local/share",
                                          "XDG_DATA_DIRS": "/usr/share:/usr/share"}):
            dirs = icons.application_dirs()
        self.assertEqual(dirs[0], "/home/me/.local/share/applications")
        self.assertEqual(dirs.count("/usr/share/applications"), 1)   # no duplicates
        self.assertIn("/var/lib/snapd/desktop/applications", dirs)


def item(name, source_id, size=10, explicit=True, app_id=None, tracks=True):
    src = SimpleNamespace(id=source_id, label=source_id.capitalize(), tracks_explicit=tracks)
    extra = {"app_id": app_id} if app_id else {}
    return SimpleNamespace(pkg=Package(name, "1", source_id, size=size, explicit=explicit,
                                       extra=extra), source=src)


class DuplicateTests(unittest.TestCase):
    def test_names_that_mean_the_same_app(self):
        self.assertEqual(name_keys(item("telegram-desktop", "apt")), {"telegram"})
        self.assertEqual(name_keys(item("Telegram", "flatpak", app_id="org.telegram.desktop")),
                         {"telegram"})
        self.assertIn("firefox", name_keys(item("Firefox", "flatpak", app_id="org.mozilla.firefox")))
        self.assertEqual(name_keys(item("vi", "apt")), set())          # too short to compare

    def test_finds_copies_across_package_managers(self):
        items = [
            item("firefox", "apt", 300), item("firefox", "snap", 250),
            item("Firefox", "flatpak", 280, app_id="org.mozilla.firefox"),
            item("telegram-desktop", "apt", 100),
            item("Telegram", "flatpak", 200, app_id="org.telegram.desktop"),
            item("gimp", "apt"), item("gimp-data", "apt"),             # same manager: fine
            item("libfirefox", "apt"),
            item("firefox", "rpm", explicit=False),                    # a dependency: ignored
            item("Tool", "appimage", tracks=False), item("tool", "apt"),
        ]
        groups = find_duplicates(items)
        names = {g.name: sorted(c.source.id for c in g.copies) for g in groups}
        self.assertEqual(names, {"Firefox": ["apt", "flatpak", "snap"],
                                 "Telegram": ["apt", "flatpak"], "Tool": ["appimage", "apt"]})
        firefox = next(g for g in groups if g.name == "Firefox")
        self.assertEqual(firefox.wasted, 250 + 280)                    # keep the biggest
        self.assertEqual(groups[0].name, "Firefox")                     # biggest savings first

    def test_desktop_suffix_is_not_an_app_name(self):
        """com.bitwarden.desktop and org.telegram.desktop are different apps."""
        items = [item("Bitwarden", "flatpak", app_id="com.bitwarden.desktop"),
                 item("Telegram", "flatpak", app_id="org.telegram.desktop"),
                 item("desktop-base", "apt")]
        self.assertEqual(find_duplicates(items), [])

    def test_nothing_to_find(self):
        self.assertEqual(find_duplicates([item("a-app", "apt"), item("b-app", "flatpak")]), [])


if __name__ == "__main__":
    unittest.main()
