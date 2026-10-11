"""Phase 4: history, security updates, holds, and the background update check."""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeSystem  # noqa: E402

from urpm import history, notify, schedule  # noqa: E402
from urpm.sources import (BrewSource, DpkgSource, FlatpakSource, PacmanSource,  # noqa: E402
                          RpmSource, SnapSource)
from urpm.sources.base import Package  # noqa: E402


def day(event):
    return time.strftime("%Y-%m-%d", time.localtime(event.time))


class HistoryParserTests(unittest.TestCase):
    def test_apt(self):
        text = ("Start-Date: 2026-09-05  11:25:18\nRequested-By: me (1000)\n"
                "Install: a:amd64 (1.0), b:amd64 (2.0, automatic)\n"
                "Upgrade: c:amd64 (1:1.0-1, 1:1.1-1), d:i386 (0.1, 0.2)\n"
                "Downgrade: e:amd64 (2.0, 1.9)\nReinstall: f:amd64 (3.0)\n"
                "Remove: g:amd64 (4.0)\nPurge: h:amd64 (5.0)\nEnd-Date: 2026-09-05  11:25:49\n")
        events = {e.name: e for e in history.parse_apt_history(text)}
        self.assertEqual((events["a"].action, events["a"].version, events["a"].automatic),
                         ("installed", "1.0", False))
        self.assertTrue(events["b"].automatic)
        self.assertEqual((events["c"].action, events["c"].old_version, events["c"].version),
                         ("updated", "1:1.0-1", "1:1.1-1"))
        self.assertEqual(events["e"].action, "downgraded")
        self.assertEqual(events["f"].action, "reinstalled")
        self.assertEqual({events["g"].action, events["h"].action}, {"removed"})
        self.assertEqual(day(events["a"]), "2026-09-05")

    def test_pacman_dnf_zypper_snap_flatpak(self):
        pacman = history.parse_pacman_log(
            "[2026-01-02T03:04:05+0100] [ALPM] upgraded foo (1.0-1 -> 1.1-1)\n"
            "[2019-05-06 07:08] [ALPM] installed old (0.1)\n[junk line]\n")
        self.assertEqual([(e.action, e.name, e.old_version, e.version) for e in pacman],
                         [("updated", "foo", "1.0-1", "1.1-1"), ("installed", "old", "", "0.1")])
        self.assertEqual(time.strftime("%H:%M", time.gmtime(pacman[0].time)), "02:04")  # UTC
        dnf = history.parse_dnf_rpm_log(
            "2026-01-02T03:04:05+0000 SUBDEBUG Upgraded: x-1.0-1.fc40.x86_64\n"
            "2026-01-02T03:04:05+0000 SUBDEBUG Upgrade: x-1.1-1.fc40.x86_64\n"
            "2026-01-02T03:04:05Z INFO Erase: kernel-core-6.10-200.fc40.x86_64\n")
        self.assertEqual([(e.action, e.name, e.version) for e in dnf],
                         [("updated", "x", "1.1-1.fc40"), ("removed", "kernel-core", "6.10-200.fc40")])
        zypper = history.parse_zypper_history(
            "# comment\n2026-01-02 03:04:05|install|vim|9.1|x86_64||repo|h|\n"
            "2026-01-03 03:04:05|remove |vim|9.1|x86_64||\n2026-01-03|patch|x|1|\n")
        self.assertEqual([e.action for e in zypper], ["installed", "removed"])
        snap = history.parse_snap_changes(
            "ID Status Spawn Ready Summary\n"
            '1 Done 2026-01-02T03:04:05Z 2026-01-02T03:05:00Z Install "a" snap\n'
            '2 Done 2026-01-03T03:04:05.123456Z 2026-01-03T03:05:00Z Auto-refresh snaps "a", "b"\n'
            '3 Undone 2026-01-04T03:04:05Z 2026-01-04T03:05:00Z Remove "a" snap\n')
        self.assertEqual([(e.action, e.name) for e in snap],
                         [("installed", "a"), ("updated", "a"), ("updated", "b")])
        now = time.mktime(time.strptime("2026-01-10 12:00", "%Y-%m-%d %H:%M"))
        flatpak = history.parse_flatpak_history(
            "Jan  5 10:00:00\tdeploy install\torg.a.App\tstable\tsystem\n"
            "Dec 30 10:00:00\tuninstall\torg.b.App\tstable\tuser\n"
            "Jan  6 10:00:00\tdeploy pull\torg.c.App\tstable\tsystem\n", now=now)
        self.assertEqual([(e.action, e.name, day(e)) for e in flatpak],
                         [("installed", "org.a.App", "2026-01-05"),
                          ("removed", "org.b.App", "2025-12-30")])    # no year: last year

    def test_rotated_and_gzipped_logs(self):
        import gzip
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp, "history.log")
            log.write_text("Start-Date: 2026-03-01  10:00:00\nInstall: new:amd64 (2)\n")
            with gzip.open(f"{log}.1.gz", "wt") as fh:
                fh.write("Start-Date: 2026-02-01  10:00:00\nInstall: mid:amd64 (1)\n")
            with gzip.open(f"{log}.10.gz", "wt") as fh:
                fh.write("Start-Date: 2025-01-01  10:00:00\nInstall: old:amd64 (0)\n")
            Path(f"{log}.2.gz").write_bytes(b"not gzip at all")   # a broken file is skipped
            self.assertEqual(history.rotated(str(log))[-1], str(log))
            events = history.from_files(history.rotated(str(log)), history.parse_apt_history)
        self.assertEqual([e.name for e in events], ["new", "mid", "old"])  # newest first


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)

    def test_history_from_each_package_manager(self):
        apt = DpkgSource().history()
        self.assertEqual([(e.action, e.name) for e in apt],
                         [("updated", "firefox"), ("removed", "old-tool"),
                          ("removed", "older-tool"), ("installed", "7zip"), ("installed", "libfoo1")])
        self.assertTrue(apt[-1].automatic)
        self.assertEqual([e.action for e in PacmanSource().history()],
                         ["removed", "updated", "installed"])
        self.assertEqual([(e.action, e.name) for e in RpmSource().history()],
                         [("removed", "old-lib"), ("updated", "bash"), ("installed", "gimp")])
        self.assertEqual(len(SnapSource().history()), 3)           # the failed change is skipped
        flatpak = FlatpakSource()
        flatpak.list_packages()
        self.assertEqual([(e.action, e.name) for e in flatpak.history()],
                         [("removed", "org.old.Removed"), ("updated", "Notes"),
                          ("installed", "Notes")])                 # app ids shown as names

    def test_zypper_history(self):
        with mock.patch("urpm.sources.rpm.frontend", return_value="zypper"):
            events = RpmSource().history()
        self.assertEqual([(e.action, e.name) for e in events], [("removed", "nano"), ("installed", "vim")])

    def test_security_updates(self):
        apt = DpkgSource()
        pkgs = apt.list_packages()
        apt.check_updates(pkgs)
        self.assertEqual({p.name for p in pkgs if p.security}, {"firefox"})
        rpm = RpmSource()
        pkgs = rpm.list_packages()
        rpm.check_updates(pkgs)
        self.assertEqual({p.name for p in pkgs if p.security}, {"gimp"})
        pacman = PacmanSource()
        pkgs = pacman.list_packages()
        pacman.check_updates(pkgs)
        self.assertEqual({p.name for p in pkgs if p.security}, {"firefox"})

    def test_holds(self):
        apt = DpkgSource()
        pkgs = {p.name: p for p in apt.list_packages()}
        self.assertTrue(pkgs["7zip"].held)
        self.assertFalse(pkgs["firefox"].held)
        self.assertEqual(apt.hold_action(pkgs["firefox"]).display(), "sudo apt-mark hold firefox")
        self.assertEqual(apt.unhold_action(pkgs["7zip"]).display(), "sudo apt-mark unhold 7zip")
        rpm = RpmSource()
        pkgs = {p.name: p for p in rpm.list_packages()}
        self.assertTrue(pkgs["bash"].held)
        self.assertEqual(rpm.hold_action(pkgs["gimp"]).argv, ["dnf", "versionlock", "add", "gimp"])
        self.assertEqual(rpm.unhold_action(pkgs["bash"]).argv,
                         ["dnf", "versionlock", "delete", "bash"])
        flatpak = FlatpakSource()
        pkgs = {p.name: p for p in flatpak.list_packages()}
        self.assertTrue(pkgs["Notes"].held)
        self.assertEqual(flatpak.unhold_action(pkgs["Notes"]).display(),
                         "flatpak mask --remove --system org.example.Notes")
        self.assertIsNone(flatpak.hold_action(pkgs["Freedesktop Platform"]))   # runtimes: no
        brew = BrewSource()
        pkgs = {p.name: p for p in brew.list_packages()}
        self.assertTrue(pkgs["openssl@3"].held)
        self.assertEqual(brew.hold_action(pkgs["gh"]).argv, ["brew", "pin", "gh"])

    def test_no_versionlock_plugin_means_no_holds(self):
        from urpm.sources import rpm as rpm_module
        real_run = rpm_module.run

        def without_plugin(*args, **kw):
            if "versionlock" in args:
                raise RuntimeError("No such command: versionlock")
            return real_run(*args, **kw)
        with mock.patch.object(rpm_module, "run", side_effect=without_plugin):
            src = RpmSource()
            pkgs = src.list_packages()
        self.assertFalse(any(p.held for p in pkgs))
        self.assertIsNone(src.hold_action(pkgs[0]))


def update(name, new="2", security=False, explicit=True, source="apt", held=False):
    src = SimpleNamespace(id=source)
    pkg = Package(name, "1", source, explicit=explicit, update=new, held=held)
    pkg.security = security
    return src, pkg


class NotifyTests(unittest.TestCase):
    def test_message(self):
        updates = [update("libssl3", security=True, explicit=False), update("firefox"),
                   update("discord"), update("zlib1g", explicit=False), update("vlc", security=True),
                   update("gimp")]
        summary, body = notify.message(updates)
        self.assertEqual(summary, "6 updates available (2 security)")
        self.assertEqual(body, "vlc, discord, firefox, gimp and 2 more. Open URPM to update.")
        self.assertEqual(notify.message([update("x")])[0], "1 update available")

    def test_only_new_updates_notify(self):
        import tempfile
        sent = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), \
                mock.patch.object(notify, "send", side_effect=lambda s, b, urgent=False:
                                  sent.append((s, urgent))):
            with mock.patch.object(notify, "collect", return_value=[update("firefox")]):
                self.assertEqual(notify.check(), "1 update available")
                self.assertIsNone(notify.check())                    # same update: quiet
            with mock.patch.object(notify, "collect",
                                   return_value=[update("firefox"), update("vlc", security=True)]):
                self.assertEqual(notify.check(), "2 updates available (1 security)")
            with mock.patch.object(notify, "collect", return_value=[]):
                self.assertIsNone(notify.check())
            stored = json.loads(Path(tmp, "urpm", "notified.json").read_text())
        self.assertEqual(stored, [])
        self.assertEqual(sent, [("1 update available", False),
                                ("2 updates available (1 security)", True)])

    def test_collect_skips_held_and_broken_sources(self):
        class Broken:
            def list_packages(self):
                raise RuntimeError("locked")
        with FakeSystem() as _fake:
            found = notify.collect([DpkgSource(), Broken()])
        self.assertEqual([pkg.name for _, pkg in found], ["firefox"])

    def test_end_to_end_on_the_fake_system(self):
        import tempfile
        with FakeSystem() as fake, tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), \
                mock.patch("urpm.notify.available_sources", return_value=[DpkgSource()]), \
                mock.patch.object(notify, "send") as send:
            self.assertEqual(notify.main(), 0)
            calls = [" ".join(c[:2]) for c in fake.calls()]
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "1 update available (1 security)")
        self.assertTrue(send.call_args.kwargs["urgent"])
        self.assertNotIn("apt-get install", calls)                   # checking never updates

    def test_send_uses_dbus(self):
        try:
            from gi.repository import Gio
        except ImportError:
            self.skipTest("PyGObject isn't installed")
        bus = mock.Mock()
        with mock.patch.object(Gio, "bus_get_sync", return_value=bus):
            self.assertTrue(notify.send("Hello", "World", urgent=True))
        args = bus.call_sync.call_args.args
        self.assertEqual(args[3], "Notify")
        payload = args[4].unpack()
        self.assertEqual(payload[3:5], ("Hello", "World"))
        self.assertEqual(payload[6]["urgency"], 2)

    def test_send_falls_back_to_notify_send(self):
        # No PyGObject (or no session bus): use notify-send, or give up quietly.
        with mock.patch.dict(sys.modules, {"gi": None}), \
                mock.patch("urpm.notify.shutil.which", return_value="/usr/bin/notify-send"), \
                mock.patch("urpm.notify.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(notify.send("Hi", "There", urgent=True))
        self.assertEqual(run.call_args.args[0][-2:], ["Hi", "There"])
        self.assertIn("--urgency=critical", run.call_args.args[0])
        with mock.patch.dict(sys.modules, {"gi": None}), \
                mock.patch("urpm.notify.shutil.which", return_value=None):
            self.assertFalse(notify.send("Hi", "There"))


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.config = Path(holder.name)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": holder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_systemd_timer(self):
        ran = []
        with mock.patch.object(schedule, "has_systemd", return_value=True), \
                mock.patch.object(schedule, "which", return_value="/usr/bin/systemctl"), \
                mock.patch.object(schedule, "run", side_effect=lambda *a, **k: ran.append(a) or ""), \
                mock.patch.object(schedule, "command",
                                  return_value=(["/home/me/.local/bin/urpm", "--check-updates"], {})):
            self.assertEqual(schedule.enable(), "systemd")
            self.assertTrue(schedule.is_enabled())
            service = (self.config / "systemd/user/urpm-update-check.service").read_text()
            timer = (self.config / "systemd/user/urpm-update-check.timer").read_text()
            schedule.disable()
        self.assertIn("ExecStart=/home/me/.local/bin/urpm --check-updates", service)
        self.assertIn("OnUnitActiveSec=6h", timer)
        self.assertIn(("systemctl", "--user", "enable", "--now", "urpm-update-check.timer"), ran)
        self.assertIn(("systemctl", "--user", "disable", "--now", "urpm-update-check.timer"), ran)
        self.assertFalse(schedule.is_enabled())

    def test_autostart_without_systemd(self):
        with mock.patch.object(schedule, "has_systemd", return_value=False), \
                mock.patch.object(schedule, "which", return_value=None), \
                mock.patch.object(schedule, "command",
                                  return_value=(["/usr/bin/python3", "-m", "urpm", "--check-updates"],
                                                {"PYTHONPATH": "/src/URPM"})):
            self.assertEqual(schedule.enable(), "autostart")
            entry = schedule.autostart_file().read_text()
            self.assertTrue(schedule.is_enabled())
            schedule.disable()
        self.assertIn("sleep 600; exec PYTHONPATH=/src/URPM /usr/bin/python3 -m urpm --check-updates",
                      entry)
        self.assertFalse(schedule.is_enabled())

    def test_command_and_systemd_detection(self):
        with mock.patch.object(schedule.sys, "argv", ["/nonexistent/thing"]), \
                mock.patch.object(schedule, "which", return_value=None):
            argv, env = schedule.command()
        self.assertEqual(argv[1:], ["-m", "urpm", "--check-updates"])
        self.assertTrue(Path(env["PYTHONPATH"], "urpm", "__init__.py").exists())
        with mock.patch.object(schedule, "which", return_value="/usr/bin/systemctl"):
            with mock.patch.object(schedule, "run", return_value="degraded\n"):
                self.assertTrue(schedule.has_systemd())
            with mock.patch.object(schedule, "run", side_effect=RuntimeError("no bus")):
                self.assertFalse(schedule.has_systemd())
        service = schedule.service_text(["/usr/bin/python3", "-m", "urpm"], {"PYTHONPATH": "/a b"})
        self.assertIn('Environment="PYTHONPATH=/a b"', service)


if __name__ == "__main__":
    unittest.main()
