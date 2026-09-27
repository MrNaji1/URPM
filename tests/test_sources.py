"""Parser tests using captured output from each package manager.

Run with:  python3 -m unittest discover tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from urpm.sources import ALL_SOURCES
from urpm.sources.apk import parse_apk_db, parse_apk_simulation, parse_apk_version, parse_world
from urpm.sources.appimage import AppImageSource, find_appimages, split_appimage_name
from urpm.sources.base import (Action, Package, apply_updates, human_size, parse_fields,
                               parse_size, split_name_version)
from urpm.sources.brew import parse_brew_info
from urpm.sources.cargo import parse_crates2
from urpm.sources.dpkg import (format_description, parse_dpkg_list, parse_simulated_removal,
                               parse_upgradable)
from urpm.sources.eopkg import parse_files, parse_list_upgrades, parse_metadata
from urpm.sources.flatpak import parse_flatpak_list, parse_metainfo, parse_updates
from urpm.sources.nix import parse_nix_env, parse_nix_profile
from urpm.sources.npm import homepage_of, package_dirs, parse_outdated
from urpm.sources.pacman import parse_checkupdates, parse_pacman_info
from urpm.sources.pip import normalize, parse_outdated as parse_pip_outdated
from urpm.sources.pipx import parse_pipx_list
from urpm.sources.portage import parse_contents, parse_world as parse_portage_world
from urpm.sources.rpm import parse_repoquery_upgrades, parse_rpm_list, parse_zypper_dry_run
from urpm.sources.snap import parse_refresh_list, parse_snap_list
from urpm.sources.xbps import parse_xbps_list, parse_xbps_updates


class HelperTests(unittest.TestCase):
    def test_parse_size_variants(self):
        self.assertEqual(parse_size("2.1 MB"), 2_100_000)
        self.assertEqual(parse_size("511,1 MB"), 511_100_000)       # comma locales
        self.assertEqual(parse_size("1.1?GB"), 1_100_000_000)       # flatpak in the C locale
        self.assertEqual(parse_size("1.5 kB"), 1500)            # non-breaking space
        self.assertEqual(parse_size("12.00 MiB"), 12 * 1024**2)
        self.assertEqual(parse_size("723 bytes"), 723)
        self.assertEqual(parse_size("1234KB"), 1_234_000)
        self.assertEqual(parse_size("unknown"), 0)

    def test_human_size(self):
        self.assertEqual(human_size(0), "—")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(6_305_792), "6.3 MB")
        self.assertEqual(human_size(2_300_000_000), "2.3 GB")
        self.assertEqual(human_size(3_000_000_000_000), "3.0 TB")

    def test_split_name_version(self):
        self.assertEqual(split_name_version("hello-2.12.1"), ("hello", "2.12.1"))
        self.assertEqual(split_name_version("python3-pip-23.0_1"), ("python3-pip", "23.0_1"))
        self.assertEqual(split_name_version("busybox-1.36.1-r15"), ("busybox", "1.36.1-r15"))
        self.assertEqual(split_name_version("libfoo-2-1.0_1"), ("libfoo-2", "1.0_1"))
        self.assertEqual(split_name_version("vim-9.1.0-r1"), ("vim", "9.1.0-r1"))
        self.assertEqual(split_name_version("noversion"), ("noversion", ""))

    def test_action_display_and_argv(self):
        action = Action(["apt-get", "remove", "-y", "foo"], root=True,
                        env={"DEBIAN_FRONTEND": "noninteractive"}, pretty="sudo apt remove foo")
        self.assertEqual(action.display(), "sudo apt remove foo")
        argv = action.full_argv()
        self.assertEqual(argv[:3], ["pkexec", "env", "DEBIAN_FRONTEND=noninteractive"])
        self.assertEqual(argv[-3:], ["remove", "-y", "foo"])
        self.assertEqual(Action(["gio", "trash", "/tmp/my app.AppImage"]).display(),
                         "gio trash '/tmp/my app.AppImage'")
        self.assertEqual(Action(["brew", "uninstall", "x"]).full_argv()[0:1][0].split("/")[-1],
                         "brew")

    def test_apply_updates(self):
        pkgs = [Package("a", "1.0", "x"), Package("b", "2.0", "x"), Package("c", "3.0", "x")]
        self.assertEqual(apply_updates(pkgs, {"a": "1.1", "b": "2.0"}), 1)
        self.assertEqual([p.update for p in pkgs], ["1.1", "", ""])
        # Flatpak can publish a new build with the same version number.
        self.assertEqual(apply_updates(pkgs, {"c": "3.0"}, same_version_ok=True), 1)
        self.assertEqual(pkgs[2].update, "new build")

    def test_parse_fields_continuation(self):
        fields = parse_fields("Package: foo\nDescription: short\n long line\n .\n more\n")
        self.assertEqual(fields["Description"], "short\nlong line\n.\nmore")


class DpkgTests(unittest.TestCase):
    LIST = (
        "7zip\t23.01+dfsg-11\t6158\tutils\tii \thttps://www.7-zip.org/\t7-Zip archiver\n"
        "bind9-libs:amd64\t1:9.18\t3000\tlibs\tii \t\tShared libraries\n"
        "mesa-vulkan-drivers:i386\t24.0\t9000\tlibs\tii \t\tVulkan drivers\n"
        "held\t1.0\t10\tmisc\thi \t\tHeld package\n"
        "oldthing\t1.0\t10\tmisc\trc \t\tRemoved but config left\n"
    )

    def test_list(self):
        manual = {"7zip", "bind9-libs"}
        pkgs = {p.name: p for p in parse_dpkg_list(self.LIST, manual, "amd64")}
        self.assertNotIn("oldthing", pkgs)                    # 'rc' = not installed
        self.assertIn("held", pkgs)                           # 'hi' = held but installed
        self.assertEqual(pkgs["7zip"].size, 6158 * 1024)
        self.assertEqual(pkgs["7zip"].homepage, "https://www.7-zip.org/")
        self.assertTrue(pkgs["bind9-libs:amd64"].explicit)    # native arch suffix stripped
        self.assertFalse(pkgs["mesa-vulkan-drivers:i386"].explicit)

    def test_without_apt_mark_everything_is_explicit(self):
        self.assertTrue(all(p.explicit for p in parse_dpkg_list(self.LIST, None)))

    def test_description(self):
        raw = ("summary line\n"
               "This package includes the tools\n"
               "for creating users.\n"
               ".\n"
               "- first bullet\n"
               "- second bullet that\n"
               "wraps")
        self.assertEqual(format_description(raw),
                         "This package includes the tools for creating users.\n\n"
                         "- first bullet\n- second bullet that wraps")

    def test_simulated_removal(self):
        text = ("NOTE: This is only a simulation!\n"
                "The following packages will be REMOVED:\n  7zip p7zip p7zip-full\n"
                "Remv p7zip-full [16.02+transitional.1]\n"
                "Remv p7zip [16.02+transitional.1]\n"
                "Remv 7zip [23.01+dfsg-11]\n")
        self.assertEqual(parse_simulated_removal(text), ["p7zip-full", "p7zip", "7zip"])

    def test_upgradable(self):
        text = ("Listing...\n"
                "firefox/noble-updates 131.0+build1-0ubuntu1 amd64 [upgradable from: 130.0]\n"
                "libc6/noble-updates,noble-security 2.39-0ubuntu8.3 amd64 "
                "[upgradable from: 2.39-0ubuntu8.2]\n")
        self.assertEqual(parse_upgradable(text), {"firefox": "131.0+build1-0ubuntu1",
                                                  "libc6": "2.39-0ubuntu8.3"})

    def test_remove_action_uses_pkexec_and_noninteractive(self):
        src = next(s for s in ALL_SOURCES if s.id == "apt")
        with mock.patch("urpm.sources.dpkg.which", return_value="/usr/bin/apt-get"):
            action = src.remove_action(Package("foo", "1", "apt"))
        self.assertTrue(action.root)
        self.assertEqual(action.argv, ["apt-get", "remove", "-y", "foo"])
        self.assertEqual(action.env, {"DEBIAN_FRONTEND": "noninteractive"})
        self.assertEqual(action.display(), "sudo apt remove foo")


class FlatpakTests(unittest.TestCase):
    LIST = (
        "com.todoist.Todoist\tTodoist\t9.30.0\t15.8 MB\tflathub\tsystem\tsystem,current\t"
        "com.todoist.Todoist/x86_64/stable\tPlanner & Calendar\n"
        "org.freedesktop.Platform\tFreedesktop Platform\t25.08.17\t668.9 MB\tflathub\t"
        "user\tuser,runtime\torg.freedesktop.Platform/x86_64/25.08\tRuntime platform\n"
    )

    def test_list(self):
        app, runtime = parse_flatpak_list(self.LIST)
        self.assertEqual((app.name, app.size, app.explicit), ("Todoist", 15_800_000, True))
        self.assertEqual(app.ident, "com.todoist.Todoist/x86_64/stable")
        self.assertEqual(app.extra["app_id"], "com.todoist.Todoist")
        self.assertFalse(runtime.explicit)
        self.assertEqual(runtime.extra["installation"], "user")

    def test_info_right_aligned_keys(self):
        text = ("\nTodoist - Planner\n\n          ID: com.todoist.Todoist\n"
                "         Ref: app/com.todoist.Todoist/x86_64/stable\n\n"
                "      Commit: 6dd21c98\n")
        fields = parse_fields(text, indented_keys=True)
        self.assertEqual(fields["ID"], "com.todoist.Todoist")
        self.assertEqual(fields["Ref"], "app/com.todoist.Todoist/x86_64/stable")
        self.assertEqual(fields["Commit"], "6dd21c98")

    def test_updates(self):
        text = ("app/com.discordapp.Discord/x86_64/stable\t1.0.159\n"
                "runtime/org.kde.Platform/x86_64/6.10\t\n")
        self.assertEqual(parse_updates(text), {"com.discordapp.Discord/x86_64/stable": "1.0.159",
                                               "org.kde.Platform/x86_64/6.10": "new build"})

    def test_metainfo(self):
        xml = """<component><description>
            <p>First   paragraph.</p><p xml:lang="de">Erster Absatz.</p>
            <ul><li>One</li><li>Two</li></ul></description></component>"""
        self.assertEqual(parse_metainfo(xml), "First paragraph.\n\n• One\n• Two")
        self.assertEqual(parse_metainfo("<not xml"), "")


class SnapTests(unittest.TestCase):
    def test_list(self):
        text = ("Name      Version    Rev    Tracking       Publisher   Notes\n"
                "core22    20240111   1122   latest/stable  canonical** base\n"
                "firefox   124.0-2    3972   latest/stable  mozilla**   -\n"
                "snapd     2.61.2     21184  latest/stable  canonical** snapd\n")
        pkgs = {p.name: p for p in parse_snap_list(text)}
        self.assertFalse(pkgs["core22"].explicit)
        self.assertFalse(pkgs["snapd"].explicit)
        self.assertTrue(pkgs["firefox"].explicit)
        self.assertEqual(pkgs["firefox"].ident, "firefox_3972")

    def test_refresh_list(self):
        text = ("Name     Version  Rev   Size   Publisher  Notes\n"
                "firefox  125.0    4033  280MB  mozilla**  -\n")
        self.assertEqual(parse_refresh_list(text), {"firefox": "125.0"})
        self.assertEqual(parse_refresh_list("All snaps up to date.\n"), {})


class RpmTests(unittest.TestCase):
    def test_list(self):
        text = ("bash\t5.2.26-3.fc40\t8123456\tUnspecified\t1710000000\t"
                "https://www.gnu.org/software/bash\tThe GNU Bourne Again shell\n"
                "gpg-pubkey\ta15b79cc-63d04c2c\t0\tPublic Keys\t1710000000\t(none)\tgpg key\n")
        (bash,) = parse_rpm_list(text)
        self.assertEqual(bash.size, 8123456)
        self.assertEqual(bash.section, "")
        self.assertEqual(bash.installed, 1710000000.0)

    def test_repoquery_upgrades(self):
        text = "firefox 131.0-1.fc40\nkernel-core 1:6.10.12-200.fc40\n\n"
        self.assertEqual(parse_repoquery_upgrades(text), {"firefox": "131.0-1.fc40",
                                                          "kernel-core": "6.10.12-200.fc40"})

    def test_zypper_dry_run(self):
        text = ("Loading repository data...\nResolving package dependencies...\n\n"
                "The following 3 packages are going to be REMOVED:\n"
                "  gimp gimp-lang gimp-plugins-python3\n\n"
                "3 packages to remove.\n")
        self.assertEqual(parse_zypper_dry_run(text), ["gimp", "gimp-lang",
                                                      "gimp-plugins-python3"])

    def test_root_commands_ignore_user_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "apt-get")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                argv = Action(["apt-get", "remove", "x"], root=True).full_argv()
        self.assertNotEqual(argv[1], fake)


class PacmanTests(unittest.TestCase):
    def test_info_blocks(self):
        text = (
            "Name            : bash\n"
            "Version         : 5.2.026-2\n"
            "Description     : The GNU Bourne Again shell\n"
            "URL             : https://www.gnu.org/software/bash/bash.html\n"
            "Depends On      : readline  libreadline.so=8-64  glibc\n"
            "                  ncurses\n"
            "Installed Size  : 9.41 MiB\n"
            "Install Date    : Tue 23 Jan 2024 10:11:12 AM CET\n"
            "Install Reason  : Installed as a dependency for another package\n"
            "\n"
            "Name            : firefox\n"
            "Version         : 124.0-1\n"
            "URL             : None\n"
            "Installed Size  : 240.00 MiB\n"
            "Install Reason  : Explicitly installed\n"
        )
        (bash, bash_fields), (firefox, _) = parse_pacman_info(text)
        self.assertFalse(bash.explicit)
        self.assertTrue(firefox.explicit)
        self.assertEqual(bash.size, int(9.41 * 1024**2))
        self.assertIsNotNone(bash.installed)
        self.assertEqual(firefox.homepage, "")
        self.assertIn("ncurses", bash_fields["Depends On"])

    def test_checkupdates(self):
        text = "linux 6.10.9.arch1-1 -> 6.10.10.arch1-1\nfirefox 130.0-1 -> 131.0-1\n"
        self.assertEqual(parse_checkupdates(text), {"linux": "6.10.10.arch1-1",
                                                    "firefox": "131.0-1"})


class XbpsTests(unittest.TestCase):
    def test_list_and_updates(self):
        text = ("ii base-system-0.114_1   Void Linux base system meta package\n"
                "ii python3-pip-23.0_1     PyPA recommended tool for installing packages\n"
                "uu broken-1.0_1           Half-configured\n")
        pkgs = parse_xbps_list(text, {"base-system-0.114_1"})
        self.assertEqual([(p.name, p.version, p.explicit) for p in pkgs],
                         [("base-system", "0.114_1", True), ("python3-pip", "23.0_1", False)])
        updates = "firefox-131.0_1 update x86_64 https://repo-default.voidlinux.org/current\n"
        self.assertEqual(parse_xbps_updates(updates), {"firefox": "131.0_1"})


class ApkTests(unittest.TestCase):
    DB = ("C:Q1abc=\nP:busybox\nV:1.36.1-r15\nA:x86_64\nS:500000\nI:924000\n"
          "T:Size optimized toolbox of many common UNIX utilities\nU:https://busybox.net/\n"
          "L:GPL-2.0-only\no:busybox\nm:Sören Tempel <soeren@alpinelinux.org>\n"
          "F:bin\nR:busybox\nF:etc\nR:securetty\n\n"
          "P:musl\nV:1.2.4-r2\nI:600000\nT:the musl c library\n")

    def test_db(self):
        (busybox, files), (musl, _) = parse_apk_db(self.DB, {"busybox"})
        self.assertEqual((busybox.version, busybox.size), ("1.36.1-r15", 924000))
        self.assertTrue(busybox.explicit)
        self.assertFalse(musl.explicit)
        self.assertEqual(files, ["/bin/busybox", "/etc/securetty"])

    def test_world_version_and_simulation(self):
        self.assertEqual(parse_world("alpine-base\nvim>=9\nfoo@testing\n"),
                         {"alpine-base", "vim", "foo"})
        self.assertEqual(parse_apk_version("Installed:  Available:\nvim-9.0-r0 < 9.1-r0\n"),
                         {"vim": "9.1-r0"})
        self.assertEqual(parse_apk_simulation("(1/2) Purging vim (9.0-r0)\n"
                                              "(2/2) Purging xxd (9.0-r0)\nOK: 10 MiB\n"),
                         ["vim", "xxd"])


class PortageTests(unittest.TestCase):
    def test_contents_and_world(self):
        contents = ("dir /usr/bin\nobj /usr/bin/vim 0123abcd 1700000000\n"
                    "sym /usr/bin/vi -> vim 1700000000\nobj /usr/share/my file.txt ab 17\n")
        self.assertEqual(parse_contents(contents),
                         ["/usr/bin/vim", "/usr/bin/vi", "/usr/share/my file.txt"])
        self.assertEqual(parse_portage_world("app-editors/vim\ndev-lang/python:3.12\n"),
                         {"app-editors/vim", "dev-lang/python"})


class EopkgTests(unittest.TestCase):
    def test_metadata_and_files(self):
        xml = """<PISI><Source><Name>nano</Name><Homepage>https://nano-editor.org</Homepage></Source>
          <Package><Name>nano</Name><Summary>Small editor</Summary><PartOf>system.base</PartOf>
          <InstalledSize>2600000</InstalledSize><History><Update release="42">
          <Version>8.2</Version></Update></History></Package></PISI>"""
        pkg = parse_metadata(xml)
        self.assertEqual((pkg.name, pkg.version, pkg.size), ("nano", "8.2-42", 2600000))
        self.assertEqual(pkg.homepage, "https://nano-editor.org")
        self.assertEqual(parse_files("<Files><File><Path>usr/bin/nano</Path></File></Files>"),
                         ["/usr/bin/nano"])
        self.assertEqual(parse_list_upgrades("nano - Small editor\nvim  - Vi improved\n"),
                         {"nano": "newer release", "vim": "newer release"})


class NixTests(unittest.TestCase):
    def test_nix_env(self):
        text = json.dumps({"nixpkgs.hello": {"name": "hello-2.12.1", "pname": "hello",
                                             "version": "2.12.1"},
                           "ripgrep": {"name": "ripgrep-14.1.0"}})
        pkgs = {p.name: p.version for p in parse_nix_env(text)}
        self.assertEqual(pkgs, {"hello": "2.12.1", "ripgrep": "14.1.0"})

    def test_nix_profile(self):
        path = "/nix/store/" + "a" * 32 + "-hello-2.12.1"
        new = json.dumps({"elements": {"hello": {"storePaths": [path],
                                                 "originalUrl": "flake:nixpkgs"}}})
        old = json.dumps({"elements": [{"storePaths": [path]}]})
        self.assertEqual([(p.name, p.version, p.ident) for p in parse_nix_profile(new)],
                         [("hello", "2.12.1", "hello")])
        self.assertEqual([(p.name, p.ident) for p in parse_nix_profile(old)], [("hello", "0")])


class AppImageTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(split_appimage_name("Obsidian-1.4.16.AppImage"), ("Obsidian", "1.4.16"))
        self.assertEqual(split_appimage_name("Bionic-1.1.4-3-x64.AppImage"), ("Bionic", "1.1.4-3"))
        self.assertEqual(split_appimage_name("Cursor-0.42.3-x86_64.AppImage"),
                         ("Cursor", "0.42.3"))
        self.assertEqual(split_appimage_name("LM-Studio-0.2.31.AppImage"), ("LM-Studio", "0.2.31"))
        self.assertEqual(split_appimage_name("nvim.appimage"), ("nvim", ""))
        self.assertEqual(split_appimage_name("kdenlive_x86_64.AppImage"), ("kdenlive", ""))

    def test_scan_and_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "Apps")
            os.makedirs(nested)
            path = os.path.join(tmp, "Tool-2.0-x86_64.AppImage")
            open(path, "w").close()
            open(os.path.join(tmp, "notes.txt"), "w").close()
            found = find_appimages([tmp, tmp])  # duplicates are ignored
            self.assertEqual(found, [path])
            src = AppImageSource()
            pkg = Package("Tool", "2.0", "appimage", ident=path)
            self.assertEqual(src.remove_action(pkg).argv, ["gio", "trash", path])
            self.assertEqual(src.launch_argv(pkg)[0], "sh")  # not executable yet


class BrewTests(unittest.TestCase):
    def test_info(self):
        text = json.dumps({"formulae": [
            {"name": "gh", "desc": "GitHub CLI", "homepage": "https://cli.github.com",
             "versions": {"stable": "2.58.0"}, "outdated": True, "tap": "homebrew/core",
             "installed": [{"version": "2.57.0", "installed_on_request": True,
                            "time": 1720000000}]},
            {"name": "openssl@3", "desc": None, "versions": {"stable": "3.3.2"},
             "outdated": False, "installed": [{"version": "3.3.2",
                                               "installed_on_request": False}]},
        ], "casks": []})
        gh, ssl = parse_brew_info(text)
        self.assertEqual((gh.version, gh.update, gh.explicit), ("2.57.0", "2.58.0", True))
        self.assertEqual((ssl.update, ssl.explicit, ssl.summary), ("", False, ""))


class LanguageToolTests(unittest.TestCase):
    def test_pip(self):
        self.assertEqual(normalize("Foo_Bar.baz"), "foo-bar-baz")
        text = json.dumps([{"name": "Requests", "version": "2.31.0", "latest_version": "2.32.3"}])
        self.assertEqual(parse_pip_outdated(text), {"requests": "2.32.3"})

    def test_pipx(self):
        text = json.dumps({"venvs": {"black": {"metadata": {
            "python_version": "Python 3.12.3",
            "main_package": {"package": "black", "package_version": "24.8.0",
                             "apps": ["black", "blackd"]}}}}})
        (pkg,) = parse_pipx_list(text)
        self.assertEqual((pkg.name, pkg.version, pkg.section), ("black", "24.8.0", "Python 3.12.3"))
        self.assertEqual(pkg.summary, "Provides: black, blackd")

    def test_npm(self):
        self.assertEqual(parse_outdated(json.dumps({"npm": {"current": "10.9.0",
                                                            "latest": "11.0.0"}})),
                         {"npm": "11.0.0"})
        self.assertEqual(homepage_of({"repository": {"url": "git+https://github.com/a/b.git"}}),
                         "https://github.com/a/b")
        self.assertEqual(homepage_of({"repository": "github:a/b"}), "")
        with tempfile.TemporaryDirectory() as root:
            for name in ("typescript", "@vue/cli", ".bin"):
                os.makedirs(os.path.join(root, name))
            self.assertEqual([os.path.relpath(p, root) for p in package_dirs(root)],
                             ["@vue/cli", "typescript"])

    def test_cargo(self):
        text = json.dumps({"installs": {
            "ripgrep 14.1.0 (registry+https://github.com/rust-lang/crates.io-index)":
                {"bins": ["rg"]},
            "mytool 0.1.0 (git+https://github.com/me/mytool#abc123)": {"bins": ["mytool"]},
        }})
        (rg, rg_bins), (mine, _) = parse_crates2(text)
        self.assertEqual((rg.name, rg.version, rg.section, rg_bins),
                         ("ripgrep", "14.1.0", "crates.io", ["rg"]))
        self.assertEqual(mine.section, "https://github.com/me/mytool")


class RegistryTests(unittest.TestCase):
    def test_sources_are_complete(self):
        ids = [s.id for s in ALL_SOURCES]
        self.assertEqual(len(ids), len(set(ids)), "source ids must be unique")
        self.assertGreaterEqual(len(ids), 16)
        for src in ALL_SOURCES:
            self.assertTrue(src.label and src.emoji and src.kind, src.id)


if __name__ == "__main__":
    unittest.main()
