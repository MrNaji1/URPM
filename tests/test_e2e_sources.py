"""Every package manager, end to end, on a fake system (see tests/fakes).

These run URPM's real source code against fake `pacman`, `rpm`, `apk` … programs that
print realistic output, plus fake package databases. So the Fedora, Arch, Alpine, Void,
Gentoo and Solus code paths are tested on any machine, including CI.

Each test also checks *which* commands URPM ran: listing, details, files, update checks
and removal previews must only ever run read-only commands.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import TOOLS, FakeSystem  # noqa: E402

from urpm.sources import (ApkSource, AppImageSource, BrewSource, CargoSource,  # noqa: E402
                          DpkgSource, EopkgSource, FlatpakSource, NixSource, NpmSource,
                          PacmanSource, PipSource, PipxSource, PortageSource, RpmSource,
                          SnapSource, XbpsSource)
from urpm.sources import nix as nix_module  # noqa: E402


class FakeSystemTest(unittest.TestCase):
    tools = TOOLS

    def setUp(self):
        self.fake = FakeSystem(self.tools).__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.root = str(self.fake.root)

    def by_name(self, packages):
        return {p.name: p for p in packages}

    def assert_ran_only(self, *expected):
        """The commands URPM ran, as 'tool first-arg' strings, must all be expected ones."""
        ran = {" ".join(call[:2]) for call in self.fake.calls()}
        self.assertLessEqual(ran, set(expected), f"unexpected commands: {ran - set(expected)}")


class DebianTests(FakeSystemTest):
    def test_everything(self):
        src = DpkgSource()
        self.assertTrue(src.available())
        pkgs = self.by_name(src.list_packages())
        self.assertEqual(set(pkgs), {"firefox", "libgtk-4-1:amd64", "7zip",
                                     "bind9-libs:amd64"})  # the "rc" package is skipped
        firefox = pkgs["firefox"]
        self.assertTrue(firefox.explicit)
        self.assertFalse(pkgs["libgtk-4-1:amd64"].explicit)
        self.assertTrue(pkgs["bind9-libs:amd64"].explicit)  # native-arch suffix matched
        self.assertEqual(firefox.size, 260000 * 1024)
        self.assertIsNotNone(firefox.installed)          # from dpkg/info/firefox.list

        details = src.details(firefox)
        self.assertIn("Firefox delivers safe, easy web browsing. A familiar", details.description)
        self.assertIn("- Tabbed browsing", details.description)
        self.assertEqual(dict(details.fields)["Architecture"], "amd64")
        desktop = f"{self.root}/usr/share/applications/firefox.desktop"
        self.assertIn(desktop, src.files(firefox))
        self.assertEqual(src.launch_argv(firefox), [f"{self.fake.bin}/gtk-launch", "firefox.desktop"])
        self.assertEqual(src.simulate_remove(pkgs["7zip"]), ["p7zip-full", "7zip"])

        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertEqual(firefox.update, "132.0+build2")
        update = src.update_action(firefox)
        self.assertTrue(update.root)
        self.assertEqual(update.argv[:4], ["apt-get", "install", "--only-upgrade", "-y"])
        self.assertIn("Dpkg::Options::=--force-confold", update.argv)
        self.assertEqual(update.display(), "sudo apt install --only-upgrade firefox")
        self.assertEqual(src.safe_remove_action(pkgs["7zip"]).argv, ["apt-get", "remove", "-y", "7zip"])
        self.assert_ran_only("apt-mark showmanual", "dpkg --print-architecture", "dpkg-query -W",
                             "dpkg-query -s", "dpkg-query -L", "apt-get -s", "apt list")

    def test_dpkg_tools_without_a_dpkg_database(self):
        """dpkg installed as a helper on Fedora/Arch: no status file, so no packages."""
        os.remove(f"{self.root}/dpkg/status")
        self.assertEqual(DpkgSource().list_packages(), [])


class FedoraTests(FakeSystemTest):
    def test_everything_with_dnf(self):
        src = RpmSource()
        pkgs = self.by_name(src.list_packages())
        self.assertEqual(set(pkgs), {"bash", "gimp"})     # gpg-pubkey skipped
        gimp = pkgs["gimp"]
        self.assertEqual((gimp.version, gimp.size, gimp.section), ("2.10.38-1.fc40", 120000000, ""))
        details = src.details(gimp)
        self.assertTrue(details.description.startswith("GIMP (GNU Image Manipulation Program)"))
        self.assertEqual(dict(details.fields)["Vendor"], "Fedora Project")
        self.assertEqual(src.launch_argv(gimp), [f"{self.fake.bin}/gtk-launch", "gimp.desktop"])
        self.assertEqual(src.simulate_remove(gimp), ["gimp", "gimp-data", "gimp-libs"])
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertEqual(gimp.update, "2.10.38-2.fc40")  # epoch dropped
        self.assertEqual(src.update_action(gimp).display(), "sudo dnf upgrade gimp")
        self.assertEqual(src.remove_action(gimp).argv, ["dnf", "remove", "-y", "gimp"])
        self.assert_ran_only("rpm -qa", "rpm -qi", "rpm -ql", "dnf repoquery")

    def test_no_rpm_database(self):
        os.rmdir(f"{self.root}/rpmdb")
        self.assertEqual(RpmSource().list_packages(), [])


class OpenSuseTests(FakeSystemTest):
    tools = tuple(t for t in TOOLS if t != "dnf") + ("zypper",)

    def test_zypper(self):
        src = RpmSource()
        gimp = self.by_name(src.list_packages())["gimp"]
        self.assertEqual(src.simulate_remove(gimp), ["gimp", "gimp-plugin-aa"])
        self.assertEqual(src.check_updates([gimp]), 0)    # zypper has no quiet update list
        self.assertEqual(src.remove_action(gimp).display(), "sudo zypper remove gimp")
        gimp.update = "2.10.40"
        self.assertEqual(src.update_action(gimp).argv,
                         ["zypper", "--non-interactive", "update", "gimp"])
        self.assert_ran_only("rpm -qa", "zypper -n")


class ArchTests(FakeSystemTest):
    def test_everything(self):
        src = PacmanSource()
        pkgs = self.by_name(src.list_packages())
        bash, firefox = pkgs["bash"], pkgs["firefox"]
        self.assertFalse(bash.explicit)
        self.assertTrue(firefox.explicit)
        self.assertEqual(firefox.size, 240 * 1024**2)
        self.assertEqual(firefox.section, "extra")
        self.assertIsNotNone(firefox.installed)
        fields = dict(src.details(firefox).fields)
        self.assertEqual(fields["Licenses"], "MPL-2.0")
        self.assertNotIn("Required By", fields)            # "None" values are hidden
        self.assertNotIn("/usr/", src.files(firefox))
        self.assertEqual(src.launch_argv(firefox)[1], "firefox.desktop")
        self.assertEqual(src.simulate_remove(firefox), ["firefox", "libxt"])
        self.assertEqual(src.check_updates([bash, firefox]), 1)
        self.assertEqual(firefox.update, "132.0-1")
        self.assertIsNone(src.update_action(firefox))       # no partial upgrades on Arch
        self.assertEqual(src.remove_action(firefox).display(), "sudo pacman -Rs firefox")
        self.assert_ran_only("pacman -Qi", "pacman -Qlq", "pacman -Rs", "checkupdates")
        self.assertTrue(all("--print" in call for call in self.fake.calls()
                            if call[:2] == ["pacman", "-Rs"]))


class VoidTests(FakeSystemTest):
    def test_everything(self):
        src = XbpsSource()
        pkgs = self.by_name(src.list_packages())
        self.assertEqual({n: p.explicit for n, p in pkgs.items()},
                         {"base-system": True, "vim": True, "ncurses": False})
        vim = pkgs["vim"]
        self.assertEqual(dict(src.details(vim).fields)["License"], "Vim")
        self.assertEqual(vim.size, 33_000_000)              # filled in from details
        self.assertEqual(src.files(vim), ["/usr/bin/vim", "/usr/bin/vi"])
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertEqual(vim.update, "9.1.1_1")
        self.assertEqual(src.update_action(vim).argv, ["xbps-install", "-uy", "vim"])
        self.assertEqual(src.remove_action(vim).argv, ["xbps-remove", "-Ry", "vim"])
        self.assert_ran_only("xbps-query -m", "xbps-query -l", "xbps-query -S", "xbps-query -f",
                             "xbps-install -nu")


class AlpineTests(FakeSystemTest):
    def test_everything(self):
        src = ApkSource()
        pkgs = self.by_name(src.list_packages())
        vim, musl = pkgs["vim"], pkgs["musl"]
        self.assertTrue(vim.explicit)
        self.assertFalse(musl.explicit)
        self.assertEqual((vim.version, vim.size), ("9.1.0-r0", 3400000))
        self.assertEqual(dict(src.details(vim).fields)["License"], "Vim")
        self.assertEqual(src.files(vim), ["/usr/bin/vim", "/usr/share/applications/vim.desktop"])
        self.assertEqual(src.simulate_remove(vim), ["vim", "xxd"])
        self.assertEqual(src.check_updates([vim, musl]), 1)
        self.assertEqual(vim.update, "9.1.1-r0")
        self.assertEqual(src.update_action(vim).display(), "sudo apk add -u vim")
        self.assertEqual(src.remove_action(vim).display(), "sudo apk del vim")
        self.assert_ran_only("apk del", "apk version")
        self.assertTrue(all("-s" in call for call in self.fake.calls() if call[:2] == ["apk", "del"]))


class GentooTests(FakeSystemTest):
    def test_everything(self):
        src = PortageSource()
        self.assertTrue(src.available())
        pkgs = self.by_name(src.list_packages())
        vim = pkgs["vim"]
        self.assertTrue(vim.explicit)
        self.assertFalse(pkgs["ncurses"].explicit)
        self.assertEqual((vim.version, vim.size, vim.section, vim.ident),
                         ("9.1.0", 33000000, "app-editors", "app-editors/vim"))
        self.assertEqual(vim.homepage, "https://www.vim.org/")
        self.assertEqual(dict(src.details(vim).fields)["USE flags"], "acl nls")
        self.assertEqual(src.files(vim), ["/usr/bin/vim", "/usr/bin/vi"])
        remove = src.safe_remove_action(vim)
        self.assertTrue(remove.root)
        self.assertEqual(remove.argv[:2], ["sh", "-c"])
        self.assertIn("emerge --deselect app-editors/vim", remove.argv[2])
        self.assertEqual(self.fake.calls(), [])             # Portage is read from files only


class SolusTests(FakeSystemTest):
    def test_everything(self):
        src = EopkgSource()
        (nano,) = src.list_packages()
        self.assertEqual((nano.name, nano.version, nano.size), ("nano", "8.2-42", 2600000))
        self.assertEqual(nano.homepage, "https://nano-editor.org")
        details = src.details(nano)
        self.assertEqual(details.description, "GNU nano is an easy to use text editor.")
        self.assertEqual(src.files(nano), ["/usr/bin/nano"])
        self.assertEqual(src.check_updates([nano]), 1)
        self.assertEqual(src.update_action(nano).argv, ["eopkg", "upgrade", "-y", "nano"])
        self.assertEqual(src.remove_action(nano).argv, ["eopkg", "remove", "-y", "nano"])
        self.assert_ran_only("eopkg list-upgrades")


class FlatpakTests(FakeSystemTest):
    def test_everything(self):
        src = FlatpakSource()
        pkgs = self.by_name(src.list_packages())
        notes, platform = pkgs["Notes"], pkgs["Freedesktop Platform"]
        self.assertTrue(notes.explicit)
        self.assertFalse(platform.explicit)
        self.assertIsNotNone(notes.installed)
        self.assertIsNotNone(platform.installed)
        details = src.details(notes)
        self.assertEqual(details.description, "Notes is a tiny notebook.\n\n• Markdown\n• Sync")
        self.assertEqual(dict(details.fields)["Ref"], "app/org.example.Notes/x86_64/stable")
        self.assertTrue(any(f.endswith("/bin/notes") for f in src.files(notes)))
        self.assertEqual(src.launch_argv(notes),
                         ["flatpak", "run", "--system", "org.example.Notes/x86_64/stable"])
        self.assertIsNone(src.launch_argv(platform))       # runtimes aren't apps
        self.assertEqual(src.check_updates([notes, platform]), 1)
        self.assertEqual(notes.update, "2.2")
        self.assertEqual(src.update_action(notes).display(),
                         "flatpak update --system org.example.Notes/x86_64/stable")
        self.assertFalse(src.remove_action(notes).root)    # Flatpak asks for itself
        self.assert_ran_only("flatpak list", "flatpak info", "flatpak remote-ls")


class SnapTests(FakeSystemTest):
    def test_everything(self):
        src = SnapSource()
        pkgs = self.by_name(src.list_packages())
        firefox, core = pkgs["firefox"], pkgs["core22"]
        self.assertTrue(firefox.explicit)
        self.assertFalse(core.explicit)
        self.assertEqual((firefox.size, core.size), (2048, 1024))
        details = src.details(firefox)
        self.assertEqual(details.description, "Firefox is a powerful, extensible web browser.")
        self.assertEqual(firefox.summary, "Mozilla Firefox web browser")
        self.assertEqual(src.launch_argv(firefox), ["snap", "run", "firefox"])
        self.assertIsNone(src.launch_argv(core))
        self.assertEqual(src.check_updates([firefox, core]), 1)
        self.assertEqual(src.update_action(firefox).display(), "sudo snap refresh firefox")
        self.assertEqual(src.remove_action(firefox).display(), "sudo snap remove firefox")
        self.assert_ran_only("snap list", "snap info", "snap refresh")
        self.assertTrue(all("--list" in call for call in self.fake.calls()
                            if call[:2] == ["snap", "refresh"]))


class NixTests(FakeSystemTest):
    def test_nix_env(self):
        src = NixSource()
        pkgs = self.by_name(src.list_packages())
        self.assertEqual({n: p.version for n, p in pkgs.items()},
                         {"hello": "2.12.1", "ripgrep": "14.1.0"})
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertEqual(pkgs["hello"].update, "2.12.2")
        self.assertEqual(src.update_action(pkgs["hello"]).argv, ["nix-env", "-u", "hello"])
        self.assertEqual(src.remove_action(pkgs["hello"]).argv, ["nix-env", "-e", "hello"])
        self.assert_ran_only("nix-env -q", "nix-env -qc")

    def test_nix_profile(self):
        Path(nix_module.PROFILE_MANIFEST).write_text("{}")
        src = NixSource()
        (hello,) = src.list_packages()
        self.assertEqual((hello.name, hello.version), ("hello", "2.12.1"))
        self.assertEqual(src.check_updates([hello]), 0)
        self.assertEqual(src.update_actions([hello]), [])
        self.assertEqual(src.remove_action(hello).display(), "nix profile remove hello")


class BrewTests(FakeSystemTest):
    def test_everything(self):
        src = BrewSource()
        pkgs = self.by_name(src.list_packages())
        gh = pkgs["gh"]
        self.assertTrue(gh.explicit)
        self.assertFalse(pkgs["openssl@3"].explicit)
        self.assertEqual((gh.version, gh.update, gh.size), ("2.57.0", "2.58.0", 4096))
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertTrue(src.files(gh)[0].endswith("/Cellar/gh/2.57.0/bin/gh"))
        self.assertEqual(src.update_action(gh).argv, ["brew", "upgrade", "gh"])
        self.assertEqual(src.remove_action(gh).argv, ["brew", "uninstall", "gh"])
        self.assert_ran_only("brew info", "brew --cellar")


class PipxTests(FakeSystemTest):
    def test_everything(self):
        src = PipxSource()
        (black,) = src.list_packages()
        self.assertEqual((black.name, black.version), ("black", "24.8.0"))
        self.assertEqual(black.summary, "Provides: black, blackd")
        self.assertGreater(black.size, 0)
        self.assertEqual(src.check_updates([black]), 1)
        self.assertEqual(black.update, "24.10.0")          # click's update isn't black's
        self.assertEqual(src.update_action(black).argv, ["pipx", "upgrade", "black"])
        self.assertEqual(src.remove_action(black).argv, ["pipx", "uninstall", "black"])
        self.assert_ran_only("pipx list", "pipx environment", "python -m")


class NpmTests(FakeSystemTest):
    def test_everything(self):
        src = NpmSource()
        pkgs = self.by_name(src.list_packages())
        self.assertEqual(set(pkgs), {"typescript", "@vue/cli", "npm"})  # not "-rf"
        ts = pkgs["typescript"]
        self.assertTrue(ts.explicit)
        self.assertFalse(pkgs["npm"].explicit)              # ships with Node
        self.assertEqual(ts.homepage, "https://github.com/microsoft/TypeScript")
        fields = dict(src.details(ts).fields)
        self.assertEqual((fields["License"], fields["Commands"]), ("Apache-2.0", "tsc"))
        self.assertTrue(src.files(ts)[0].endswith("package.json"))
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        update = src.update_action(ts)
        self.assertEqual(update.argv, ["npm", "install", "-g", "typescript@latest"])
        self.assertFalse(update.root)                       # the folder is writable
        self.assertEqual(src.remove_action(ts).argv, ["npm", "uninstall", "-g", "typescript"])
        self.assert_ran_only("npm root", "npm outdated")


class CargoTests(FakeSystemTest):
    def test_everything(self):
        src = CargoSource()
        self.assertTrue(src.available())
        pkgs = self.by_name(src.list_packages())
        rg = pkgs["ripgrep"]
        self.assertEqual((rg.version, rg.size, rg.section), ("14.1.0", 5000, "crates.io"))
        self.assertTrue(src.files(rg)[0].endswith("/cargo/bin/rg"))
        self.assertEqual(src.check_updates(list(pkgs.values())), 1)
        self.assertEqual((rg.update, pkgs["bat"].update), ("14.1.1", ""))
        self.assertEqual(src.update_action(rg).argv, ["cargo", "install", "ripgrep"])
        self.assertEqual(src.remove_action(rg).argv, ["cargo", "uninstall", "ripgrep"])
        self.assert_ran_only("cargo install-update")

    def test_no_update_add_on(self):
        os.remove(self.fake.bin / "cargo-install-update")
        src = CargoSource()
        self.assertEqual(src.check_updates(src.list_packages()), 0)
        self.assertEqual(self.fake.calls(), [])


class PipTests(FakeSystemTest):
    def test_everything(self):
        src = PipSource()
        src.site = f"{self.root}/site"
        self.assertTrue(src.available())
        pkgs = self.by_name(src.list_packages())
        requests = pkgs["Requests"]
        self.assertTrue(requests.explicit)                  # has a REQUESTED marker
        self.assertFalse(pkgs["urllib3"].explicit)
        self.assertEqual((requests.ident, requests.size), ("requests", 1200))
        self.assertEqual(requests.homepage, "https://example.org/requests")
        self.assertTrue(any(f.endswith("requests/__init__.py") for f in src.files(requests)))
        with mock.patch("urpm.sources.pip.importlib.util.find_spec", return_value=True):
            self.assertEqual(src.check_updates(list(pkgs.values())), 1)
            self.assertEqual(requests.update, "2.32.3")
            update = src.update_action(requests)
        self.assertEqual(update.argv[:7],
                         ["python3", "-m", "pip", "install", "--user", "--upgrade", "Requests"])
        self.assert_ran_only("python3 -m")


class AppImageTests(FakeSystemTest):
    def test_everything(self):
        src = AppImageSource()
        pkgs = self.by_name(src.list_packages())
        tool, fresh = pkgs["Tool"], pkgs["Fresh"]
        self.assertEqual((tool.version, fresh.version), ("1.0", "2.0"))
        self.assertEqual(tool.section, "~/Applications")
        self.assertIsNone(src.launch_warning(tool))
        self.assertIn("isn't marked as a program", src.launch_warning(fresh))
        self.assertEqual(src.remove_action(tool).argv, ["gio", "trash", tool.ident])
        self.assertEqual(self.fake.calls(), [])


if __name__ == "__main__":
    unittest.main()
