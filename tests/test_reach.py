"""Phase 5: the command line, moving to a new computer, Flatpak permissions, translations.

Nothing here needs a display. Commands run against the fake system in tests/fakes, and
installing is never actually done: the commands are checked instead.
"""

import gettext
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeSystem  # noqa: E402

from urpm import cli, i18n, migrate  # noqa: E402
from urpm.sources import (ApkSource, AppImageSource, BrewSource, CargoSource,  # noqa: E402
                          DpkgSource, EopkgSource, FlatpakSource, NpmSource, PacmanSource,
                          PipxSource, PortageSource, RpmSource, SnapSource, XbpsSource)
from urpm.sources import dpkg, flatpak, rpm  # noqa: E402
from urpm.sources.base import Package, UnsafeCommand  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENGLISH = {"LANGUAGE": "en", "LANG": "C.UTF-8"}


def english():
    """Run with English output, whatever this computer's language is."""
    return mock.patch.dict(os.environ, ENGLISH)


class CommandLineTests(unittest.TestCase):
    """`urpm list`, `urpm info`… on the fake system."""

    def setUp(self):
        self.fake = FakeSystem().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        patcher = english()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.setup, "en")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["urpm", *args])
        return code, out.getvalue(), err.getvalue()

    def commands(self):
        return {" ".join(c[:2]) for c in self.fake.calls()}

    def test_help_version_and_unknown_commands(self):
        code, out, _err = self.run_cli("--help")
        self.assertEqual(code, 0)
        for command in ("list", "search", "info", "updates", "dupes", "clean", "history",
                        "export", "backup", "restore"):
            self.assertRegex(out, rf"\n  {command}\b")
        self.assertEqual(self.run_cli("--version")[1].strip(), "URPM " + cli.__version__)
        code, _out, err = self.run_cli("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("Unknown command: frobnicate", err)

    def test_list_mine_source_and_json(self):
        code, out, _err = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("firefox", out)
        self.assertIn("Notes", out)
        mine = self.run_cli("list", "--mine", "--source", "apt")[1]
        self.assertIn("firefox", mine)
        self.assertNotIn("nano", mine)                  # came with the system
        self.assertNotIn("libgtk-4-1", mine)            # a dependency
        rows = json.loads(self.run_cli("list", "--source", "flatpak", "--json")[1])
        self.assertEqual({r["source"] for r in rows}, {"Flatpak"})
        self.assertIn("Notes", [r["name"] for r in rows])

    def test_search_info_and_permissions(self):
        code, out, _err = self.run_cli("search", "notes")
        self.assertEqual(code, 0)
        self.assertIn("Notes", out)
        self.assertEqual(self.run_cli("search", "zzzz-nothing")[0], 1)
        code, out, _err = self.run_cli("info", "org.example.Notes")
        self.assertEqual(code, 0)
        self.assertIn("Permissions", out)
        self.assertIn("🏠 Your whole home folder (read-only)", out)
        self.assertIn("🌐 Internet access", out)
        self.assertEqual(self.run_cli("info", "not-installed")[0], 1)

    def test_updates_dupes_clean_history_are_read_only(self):
        code, out, _err = self.run_cli("updates")
        self.assertEqual(code, 0)
        self.assertIn("🛡", out)                         # firefox has a security update
        self.assertRegex(out, r"\d+ updates available")
        self.assertEqual(self.run_cli("dupes")[0], 0)
        code, out, _err = self.run_cli("clean", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Nothing was removed", out)
        code, out, _err = self.run_cli("history", "--days", "100000")
        self.assertEqual(code, 0)
        self.assertIn("Installed", out)
        ran = self.commands()
        for writer in ("apt-get remove", "apt-get autoremove", "apt-get install",
                       "pacman -Rns", "snap remove", "flatpak uninstall", "npm cache"):
            self.assertNotIn(writer, ran)

    def test_export_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv = os.path.join(tmp, "all.csv")
            self.assertEqual(self.run_cli("export", csv, "--mine")[0], 0)
            self.assertIn("firefox", Path(csv).read_text())
            saved = os.path.join(tmp, "apps.json")
            code, out, _err = self.run_cli("backup", saved)
            self.assertEqual(code, 0)
            snap = json.loads(Path(saved).read_text())
            self.assertEqual(snap["format"], "urpm-apps")
            names = {(a["source"], a["name"]) for a in snap["apps"]}
            self.assertIn(("apt", "firefox"), names)
            self.assertNotIn(("apt", "nano"), names)
            self.assertIn(("flatpak", "Notes"), names)
            script = Path(tmp, "apps.sh")
            self.assertTrue(os.access(script, os.X_OK))
            self.assertEqual(subprocess.run(["/bin/sh", "-n", str(script)]).returncode, 0)

            # Everything from this computer's list is already here.
            code, out, _err = self.run_cli("restore", saved)
            self.assertEqual(code, 0)
            self.assertIn("Nothing to install", out)

            # A list from another computer: vlc is in the repositories, brave-browser isn't.
            snap["apps"] += [{"source": "apt", "name": "vlc", "id": "vlc"},
                             {"source": "apt", "name": "brave-browser", "id": "brave-browser"},
                             {"source": "appimage", "name": "Bionic", "id": "/x/Bionic"}]
            Path(saved).write_text(json.dumps(snap))
            code, out, _err = self.run_cli("restore", saved)
            self.assertEqual(code, 0)
            self.assertIn("$ sudo apt install vlc\n", out)
            self.assertIn("add their repositories first): brave-browser", out)
            self.assertRegex(out, r"Install these AppImage apps yourself: .*Bionic")
            self.assertIn("Nothing was installed", out)
            self.assertNotIn("apt-get install", self.commands())

            # Never really install: check the command instead. (pkexec may not be installed.)
            with mock.patch.object(cli, "execute", return_value=0) as execute, \
                    mock.patch("urpm.sources.base.root_binary", lambda name: f"/usr/bin/{name}"):
                code, out, _err = self.run_cli("restore", saved, "--install")
            self.assertEqual(code, 0)
            self.assertEqual(execute.call_count, 1)
            argv = execute.call_args.args[0]
            self.assertEqual(argv[-1], "vlc")
            self.assertIn("install", argv)

            Path(saved).write_text("{not json")
            code, _out, err = self.run_cli("restore", saved)
            self.assertEqual(code, 1)
            self.assertIn("isn't a URPM app list", err)

    def test_check_updates_flag_goes_to_the_notifier(self):
        with mock.patch("urpm.notify.main", return_value=0) as notify_main:
            self.assertEqual(self.run_cli("--check-updates")[0], 0)
        notify_main.assert_called_once()


class MigrateTests(unittest.TestCase):
    def test_what_gets_saved(self):
        apt, rpm_src, flat, snap, appimage = (DpkgSource(), RpmSource(), FlatpakSource(),
                                              SnapSource(), AppImageSource())
        by_source = {
            apt: [Package("gimp", "2", "apt"), Package("libfoo", "1", "apt", explicit=False)],
            rpm_src: [Package("glibc", "2", "rpm"), Package("inkscape", "1", "rpm", icon="x")],
            flat: [Package("Notes", "1", "flatpak", ident="org.example.Notes/x86_64/stable",
                           extra={"app_id": "org.example.Notes", "installation": "user",
                                  "origin": "flathub"}),
                   Package("Platform", "1", "flatpak", explicit=False)],
            snap: [Package("code", "1", "snap", extra={"classic": "1"})],
            appimage: [Package("Bionic", "1", "appimage", ident="/home/x/Bionic.AppImage")],
        }
        with mock.patch.object(migrate, "OS_RELEASE", "/nonexistent"):
            snap_ = migrate.snapshot(by_source)
        apps = {(a["source"], a["name"]): a for a in snap_["apps"]}
        self.assertEqual(set(apps), {("apt", "gimp"), ("rpm", "inkscape"), ("flatpak", "Notes"),
                                     ("snap", "code"), ("appimage", "Bionic")})
        self.assertEqual(apps[("flatpak", "Notes")]["id"], "org.example.Notes")
        self.assertEqual(apps[("flatpak", "Notes")]["extra"]["installation"], "user")
        self.assertEqual(apps[("snap", "code")]["extra"], {"classic": "1"})
        self.assertEqual(migrate.load(migrate.dumps(snap_))["apps"][0]["name"], "gimp")

    def test_os_release(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write('NAME="Linux Mint"\nPRETTY_NAME="Linux Mint 22"\nID=linuxmint\n')
        self.addCleanup(os.unlink, fh.name)
        self.assertEqual(migrate.os_release(fh.name),
                         {"pretty_name": "Linux Mint 22", "id": "linuxmint"})
        self.assertEqual(migrate.os_release("/nonexistent"), {})

    def test_loading_refuses_bad_files_and_drops_bad_entries(self):
        for text in ("nope", "[]", '{"format": "other"}',
                     '{"format": "urpm-apps", "version": 99, "apps": []}'):
            with self.assertRaises(ValueError):
                migrate.load(text)
        data = migrate.load(json.dumps({"format": "urpm-apps", "version": 1, "apps": [
            {"source": "apt", "name": "gimp", "id": "gimp"},
            {"source": "apt", "name": "--purge", "id": "--purge"},       # an option
            {"source": "apt", "name": "a\nrm -rf ~", "id": "a"},          # a second line
            {"source": "apt", "name": "", "id": "x"},
            "not a dict",
            {"source": "flatpak", "name": "Notes", "id": "org.example.Notes",
             "extra": {"origin": "-evil", "installation": "user", "n": 5}}],
            "system": {"pretty_name": "Fedora", "x": 1}}))
        self.assertEqual([a["name"] for a in data["apps"]], ["gimp", "Notes"])
        self.assertEqual(data["apps"][1]["extra"], {"installation": "user"})
        self.assertEqual(data["system"], {"pretty_name": "Fedora"})

    def test_plan(self):
        snap = migrate.load(json.dumps({"format": "urpm-apps", "version": 1, "apps": [
            {"source": "apt", "name": "gimp", "id": "gimp"},
            {"source": "apt", "name": "vlc", "id": "vlc"},
            {"source": "apt", "name": "chrome", "id": "chrome"},
            {"source": "flatpak", "name": "Notes", "id": "org.example.Notes"},
            {"source": "flatpak", "name": "Discord", "id": "com.discordapp.Discord"},
            {"source": "snap", "name": "spotify", "id": "spotify"},
            {"source": "pip", "name": "requests", "id": "requests"},
            {"source": "martian", "name": "x", "id": "x"}]}))
        apt, flat = DpkgSource(), FlatpakSource()
        installed = {"apt": [Package("gimp:amd64", "2", "apt")],
                     "flatpak": [Package("Notes", "1", "flatpak",
                                         extra={"app_id": "org.example.Notes"})]}
        with mock.patch.object(DpkgSource, "installable", return_value={"vlc"}), \
                mock.patch.object(FlatpakSource, "installable", side_effect=RuntimeError):
            plan = migrate.plan(snap, [apt, flat], installed)
        groups = {g.source.id: g for g in plan.groups}
        self.assertEqual([p.name for p in groups["apt"].missing], ["vlc"])
        self.assertEqual([p.name for p in groups["apt"].not_found], ["chrome"])
        self.assertEqual(groups["apt"].present, 1)                 # gimp:amd64 is gimp
        self.assertEqual([p.name for p in groups["flatpak"].missing], ["Discord"])
        self.assertEqual(plan.unavailable, {"Snap": ["spotify"]})
        self.assertEqual(plan.manual, {"pip (user)": ["requests"], "martian": ["x"]})
        self.assertEqual((plan.missing, plan.present), (2, 2))
        unchecked = migrate.plan(snap, [apt], installed, check=False)
        self.assertEqual(len(unchecked.groups[0].missing), 2)

    def test_script(self):
        snap = migrate.load(json.dumps({
            "format": "urpm-apps", "version": 1, "created": "2026-10-11T10:00:00",
            "system": {"pretty_name": "Linux Mint 22"}, "apps": [
                {"source": "apt", "name": "gimp", "id": "gimp"},
                {"source": "apt", "name": "it's", "id": "it's"},          # needs quoting
                {"source": "flatpak", "name": "Notes", "id": "org.example.Notes",
                 "extra": {"origin": "flathub", "installation": "user"}},
                {"source": "snap", "name": "code", "id": "code", "extra": {"classic": "1"}},
                {"source": "appimage", "name": "Bionic", "id": "/x/Bionic"}]}))
        text = migrate.script(snap, "my apps.sh")
        self.assertIn("sh 'my apps.sh'", text)
        self.assertIn("Linux Mint 22", text)
        self.assertIn("if command -v apt-get >/dev/null 2>&1; then", text)
        # One of the names isn't plain, so APT installs them all in one quoted command.
        self.assertIn("apt-get install -y gimp 'it'\"'\"'s'", text)
        self.assertIn("flatpak remote-add --if-not-exists --user flathub", text)
        self.assertIn("flatpak install -y --noninteractive --user flathub org.example.Notes",
                      text)
        self.assertIn("sudo snap install --classic code", text)
        self.assertIn("Install these yourself: Bionic", text)
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(text)
        self.addCleanup(os.unlink, fh.name)
        self.assertEqual(subprocess.run(["/bin/sh", "-n", fh.name]).returncode, 0)

        plain = migrate.script(migrate.load(json.dumps({"format": "urpm-apps", "version": 1,
                                                        "apps": [{"source": "apt",
                                                                  "name": "gimp",
                                                                  "id": "gimp"}]})))
        self.assertIn('if apt-cache show "$p" >/dev/null 2>&1; then', plain)
        self.assertIn("apt-get install -y $ok", plain)

    def test_script_runs_and_skips_unknown_packages(self):
        """Run the generated script against fake apt-get / apt-cache / sudo programs."""
        snap = migrate.load(json.dumps({"format": "urpm-apps", "version": 1, "apps": [
            {"source": "apt", "name": "vlc", "id": "vlc"},
            {"source": "apt", "name": "brave-browser", "id": "brave-browser"},
            {"source": "snap", "name": "spotify", "id": "spotify"}]}))
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp, "bin")
            bin_dir.mkdir()
            log = Path(tmp, "log")
            fakes = {"sudo": 'exec "$@"',
                     "env": 'while [ "${1#*=}" != "$1" ]; do shift; done; exec "$@"',
                     "apt-get": f'echo "apt-get $*" >> {log}',
                     "apt-cache": '[ "$2" = vlc ]'}
            for name, body in fakes.items():
                path = bin_dir / name
                path.write_text(f"#!/bin/sh\n{body}\n")
                path.chmod(0o755)
            script = Path(tmp, "apps.sh")
            script.write_text(migrate.script(snap))
            # Only the fake programs are on PATH, so nothing real can be installed.
            result = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True,
                                    env={"PATH": str(bin_dir)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(log.read_text().strip(), "apt-get install -y vlc")
            self.assertIn("brave-browser", result.stdout)
            self.assertIn("Skipping Snap", result.stdout)


class InstallActionTests(unittest.TestCase):
    def names(self, *names, **extra):
        return [Package(n, "", "x", extra=dict(extra)) for n in names]

    def test_every_package_manager(self):
        displays = {}
        cases = [(DpkgSource(), self.names("gimp", "vlc")), (PacmanSource(), self.names("htop")),
                 (XbpsSource(), self.names("htop")), (ApkSource(), self.names("htop")),
                 (EopkgSource(), self.names("htop")), (BrewSource(), self.names("jq")),
                 (PipxSource(), self.names("black", "ruff")), (CargoSource(), self.names("bat")),
                 (PortageSource(), [Package("vim", "", "portage", ident="app-editors/vim")])]
        for src, pkgs in cases:
            actions = src.safe_install_actions(pkgs)
            self.assertTrue(actions, src.id)
            displays[src.id] = [a.display() for a in actions]
            self.assertEqual(src.install_actions([]), [], src.id)
        self.assertEqual(displays["apt"], ["sudo apt install gimp vlc"])
        self.assertEqual(displays["pacman"], ["sudo pacman -S --needed htop"])
        self.assertEqual(displays["pipx"], ["pipx install black", "pipx install ruff"])
        self.assertEqual(displays["portage"], ["sudo emerge --noreplace app-editors/vim"])
        self.assertEqual(DpkgSource().install_actions(self.names("gimp"))[0].env,
                         {"DEBIAN_FRONTEND": "noninteractive"})

    def test_rpm_follows_the_installed_frontend(self):
        with mock.patch.object(rpm, "frontend", return_value="zypper"):
            self.assertEqual(RpmSource().install_actions(self.names("gimp"))[0].display(),
                             "sudo zypper install gimp")
        with mock.patch.object(rpm, "frontend", return_value="dnf5"):
            self.assertEqual(RpmSource().install_actions(self.names("gimp"))[0].argv,
                             ["dnf5", "install", "-y", "gimp"])
        with mock.patch.object(rpm, "frontend", return_value=None):
            self.assertEqual(RpmSource().install_actions(self.names("gimp")), [])

    def test_flatpak_snap_and_npm(self):
        pkgs = [Package("Notes", "", "flatpak", extra={"app_id": "org.example.Notes",
                                                      "installation": "user",
                                                      "origin": "flathub"}),
                Package("Tool", "", "flatpak", extra={"app_id": "org.example.Tool",
                                                     "origin": "--evil"}),
                Package("Gnome", "", "flatpak", extra={"app_id": "org.gnome.Maps",
                                                      "origin": "gnome-nightly"})]
        argvs = [a.argv for a in FlatpakSource().install_actions(pkgs)]
        self.assertEqual(argvs[0][:4], ["flatpak", "remote-add", "--if-not-exists", "--system"])
        self.assertEqual(argvs[1][:4], ["flatpak", "remote-add", "--if-not-exists", "--user"])
        self.assertIn(["flatpak", "install", "-y", "--noninteractive", "--user", "flathub",
                       "org.example.Notes"], argvs)
        self.assertIn(["flatpak", "install", "-y", "--noninteractive", "--system", "flathub",
                       "org.example.Tool"], argvs)                      # bad remote name ignored
        self.assertIn(["flatpak", "install", "-y", "--noninteractive", "--system",
                       "gnome-nightly", "org.gnome.Maps"], argvs)

        snaps = SnapSource().install_actions([Package("vlc", "", "snap"),
                                              Package("code", "", "snap",
                                                      extra={"classic": "1"})])
        self.assertEqual([a.argv for a in snaps], [["snap", "install", "vlc"],
                                                   ["snap", "install", "--classic", "code"]])
        self.assertTrue(all(a.root for a in snaps))

        with mock.patch.object(NpmSource, "_root", return_value="/usr/lib/node_modules"), \
                mock.patch("urpm.sources.npm.can_write", return_value=False):
            self.assertTrue(NpmSource().install_actions(self.names("typescript"))[0].root)
        with mock.patch.object(NpmSource, "_root", side_effect=RuntimeError("no npm")):
            self.assertFalse(NpmSource().install_actions(self.names("typescript"))[0].root)

    def test_option_like_names_are_refused(self):
        with self.assertRaises(UnsafeCommand):
            DpkgSource().safe_install_actions([Package("-y", "", "apt")])

    def test_installable(self):
        with FakeSystem():
            self.assertEqual(DpkgSource().installable(["vlc", "brave-browser", "firefox:amd64"]),
                             {"vlc", "firefox:amd64"})
            self.assertEqual(PacmanSource().installable(["htop", "yay"]), {"htop"})
            self.assertIsNone(DpkgSource().installable([]))
        self.assertIsNone(FlatpakSource().installable(["x"]))


class InstallerPackagesTests(unittest.TestCase):
    def test_parse_initial_status(self):
        text = ("Package: nano\nStatus: install ok installed\nArchitecture: amd64\n\n"
                "Package: libc6\nArchitecture: i386\n")
        self.assertEqual(dpkg.parse_initial_status(text),
                         {"nano", "nano:amd64", "libc6", "libc6:i386"})

    def test_missing_or_broken_record(self):
        with mock.patch.object(dpkg, "INITIAL_STATUS", "/nonexistent.gz"):
            self.assertEqual(dpkg.installer_packages(), set())
        with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as fh:
            fh.write(b"not gzip at all")
        self.addCleanup(os.unlink, fh.name)
        with mock.patch.object(dpkg, "INITIAL_STATUS", fh.name):
            self.assertEqual(dpkg.installer_packages(), set())


class FlatpakPermissionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.setup, "en")
        i18n.setup("en")

    def test_plain_words_risky_first(self):
        perms = flatpak.parse_permissions("""[Context]
shared=network;ipc;
sockets=x11;wayland;pulseaudio;!ssh-auth;system-bus;
devices=all;
filesystems=host;xdg-download;xdg-pictures/Screenshots:ro;xdg-config/kdeglobals:ro;/media;
features=devel;

[Session Bus Policy]
org.freedesktop.Flatpak=talk
org.freedesktop.secrets=talk
org.freedesktop.Notifications=talk
com.example.Unknown=talk
""")
        texts = [p.text for p in perms]
        risky = [p.text for p in perms if p.risky]
        self.assertEqual(texts[:len(risky)], risky)                  # risky ones first
        self.assertIn("Every file on your computer", risky)
        self.assertIn("All devices: camera, USB, game controllers…", risky)
        self.assertIn("Can run commands outside its sandbox", risky)
        self.assertIn("Talks to every system service", risky)
        self.assertIn("Can inspect other programs (debugging)", risky)
        self.assertIn("Your Pictures folder (Screenshots) (read-only)", texts)
        self.assertIn("~/.config/kdeglobals (read-only)", texts)
        self.assertIn("/media", texts)
        self.assertIn("Your saved passwords (keyring)", texts)
        self.assertNotIn("Uses your SSH keys", texts)                # "!" means not allowed
        self.assertEqual(flatpak.parse_permissions(""), [])

    def test_source_permissions_and_data_folder(self):
        with FakeSystem() as fake:
            src = FlatpakSource()
            notes = next(p for p in src.list_packages() if p.name == "Notes")
            texts = [p.text for p in src.permissions(notes)]
            self.assertEqual(texts[0], "Your whole home folder (read-only)")
            self.assertIn("Your Documents folder", texts)
            folder = fake.home / ".var/app/org.example.Notes"
            folder.mkdir(parents=True, exist_ok=True)
            self.assertEqual(src.data_folder(notes), str(folder))
            shutil.rmtree(folder)
            self.assertIsNone(src.data_folder(notes))
            runtime = next(p for p in src.list_packages() if not p.explicit)
            self.assertIsNone(src.permissions(runtime))
            self.assertIsNone(src.data_folder(runtime))


class TranslationTests(unittest.TestCase):
    PO = r'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n > 1);\n"

#, python-brace-format
msgid "Hello {name}"
msgstr "Bonjour {name}"

#, fuzzy
msgid "Guess"
msgstr "Devine"

msgid "Untranslated"
msgstr ""

msgid "Two\n"
"lines \"quoted\""
msgstr "Deux\n"
"lignes « citées »"

msgid "{n} app"
msgid_plural "{n} apps"
msgstr[0] "{n} appli"
msgstr[1] "{n} applis"
'''

    def tearDown(self):
        i18n.setup("en")

    def test_parse_and_compile(self):
        entries = i18n.parse_po(self.PO)
        self.assertEqual(entries[("Hello {name}",)], ["Bonjour {name}"])
        self.assertNotIn(("Guess",), entries)                         # fuzzy: not trusted
        self.assertNotIn(("Untranslated",), entries)
        self.assertEqual(entries[('Two\nlines "quoted"',)], ["Deux\nlignes « citées »"])
        translations = gettext.GNUTranslations(io.BytesIO(i18n.compile_mo(entries)))
        self.assertEqual(translations.gettext("Hello {name}"), "Bonjour {name}")
        self.assertEqual(translations.gettext("Untranslated"), "Untranslated")
        self.assertEqual(translations.ngettext("{n} app", "{n} apps", 1), "{n} appli")
        self.assertEqual(translations.ngettext("{n} app", "{n} apps", 0), "{n} appli")
        self.assertEqual(translations.ngettext("{n} app", "{n} apps", 2), "{n} applis")

    def test_choosing_a_language(self):
        have = ["en", "ar", "de", "pt_BR"]
        self.assertEqual(i18n.match(["de_AT.UTF-8"], have), "de")
        self.assertEqual(i18n.match(["pt_PT"], have), "pt_BR")
        self.assertEqual(i18n.match(["pt_BR.UTF-8@euro"], have), "pt_BR")
        self.assertEqual(i18n.match(["xx", "ar_EG"], have), "ar")
        self.assertEqual(i18n.match(["en_GB"], have), "en")
        self.assertEqual(i18n.match([], have), "en")
        with mock.patch.dict(os.environ, {"LANGUAGE": "fr:de", "LANG": "es_ES.UTF-8"}):
            self.assertEqual(i18n.system_languages(), ["fr", "de"])
        with mock.patch.dict(os.environ, {"LANGUAGE": "", "LC_ALL": "C.UTF-8",
                                          "LC_MESSAGES": "", "LANG": "ar_EG.UTF-8"}):
            self.assertEqual(i18n.system_languages(), ["ar_EG.UTF-8"])
        self.assertTrue(i18n.is_rtl("ar"))
        self.assertFalse(i18n.is_rtl("de"))

    def test_setup_and_saved_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            locale = Path(tmp)
            (locale / "fr.po").write_text(self.PO)
            (locale / "xx.po").write_text('msgid "a"\nmsgstr "b" broken"\n')
            with mock.patch.object(i18n, "LOCALE_DIR", locale):
                self.assertEqual(i18n.setup("fr_FR.UTF-8"), "fr")
                self.assertEqual(i18n._("Hello {name}").format(name="Naji"), "Bonjour Naji")
                self.assertEqual(i18n.ngettext("{n} app", "{n} apps", 3), "{n} applis")
                self.assertEqual(i18n.current(), "fr")
                self.assertEqual(i18n.setup("xx"), "en")                # broken file: English
                self.assertEqual(i18n._("Hello {name}"), "Hello {name}")
            config = Path(tmp, "urpm")
            config.mkdir()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                self.assertEqual(i18n.saved_language(), "")
                (config / "settings.json").write_text('{"language": "ar"}')
                self.assertEqual(i18n.saved_language(), "ar")
                (config / "settings.json").write_text('{"language": 5}')
                self.assertEqual(i18n.saved_language(), "")
                (config / "settings.json").write_text('[1]')
                self.assertEqual(i18n.saved_language(), "")


class ShippedTranslationsTests(unittest.TestCase):
    """Every language URPM ships must be complete and must not break placeholders."""

    def test_template_is_up_to_date(self):
        result = subprocess.run([sys.executable, str(ROOT / "po" / "update.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_language_is_complete_and_safe(self):
        template = i18n.parse_po((ROOT / "po" / "urpm.pot").read_text().replace(
            'msgstr ""', 'msgstr "x"'))
        keys = [k for k in template if k != ("",)]
        languages = sorted((ROOT / "urpm" / "locale").glob("*.po"))
        self.assertTrue(languages)
        for path in languages:
            with self.subTest(language=path.stem):
                self.assertIn(path.stem, i18n.NATIVE_NAMES)
                po = i18n.parse_po(path.read_text("utf-8"))
                header = po[("",)][0]
                nplurals = int(re.search(r"nplurals=(\d+)", header).group(1))
                missing = [k for k in keys if k not in po]
                self.assertEqual(missing, [], f"untranslated in {path.name}")
                for key in keys:
                    values = po[key]
                    if len(key) == 2:
                        self.assertEqual(len(values), nplurals, key)
                    allowed = set(re.findall(r"\{\w+\}", " ".join(key)))
                    for value in values:
                        used = set(re.findall(r"\{\w+\}", value))
                        self.assertLessEqual(used, allowed, (key, value))
                        if len(key) == 1:
                            self.assertEqual(used, allowed, (key, value))
                        # it must still work with .format()
                        value.format(**{name[1:-1]: "x" for name in allowed})
                    if key[0].startswith("%"):                       # a strftime format
                        self.assertRegex(values[0], r"^[%A-Za-z ,.،-]+$")
                        self.assertEqual(set(re.findall(r"%\w", values[0])),
                                         set(re.findall(r"%\w", key[0])))
                translations = i18n.load_po(path)
                self.assertNotEqual(translations.gettext("Updates"), "")

    def test_no_fstrings_in_translated_text(self):
        sys.path.insert(0, str(ROOT / "po"))
        self.addCleanup(sys.path.remove, str(ROOT / "po"))
        import update
        entries = update.extract()
        self.assertGreater(len(entries), 300)
        self.assertIn(("Search your packages…",), entries)


if __name__ == "__main__":
    unittest.main()
