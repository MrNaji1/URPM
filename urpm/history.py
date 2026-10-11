"""Install history: what was installed, updated or removed, and when.

Each package manager keeps its own log; the parsers here turn them into one list of
Events. Sources call them from Source.history(). No GTK in this module.
"""

from __future__ import annotations

import calendar
import glob
import gzip
import os
import re
import time
from dataclasses import dataclass

INSTALLED, UPDATED, REMOVED, DOWNGRADED, REINSTALLED = (
    "installed", "updated", "removed", "downgraded", "reinstalled")
MAX_EVENTS = 5000  # per package manager; older history is rarely interesting


def action_word(action: str) -> str:
    """'Installed', 'Updated'… in the user's language."""
    from urpm.i18n import _
    return {INSTALLED: _("Installed"), UPDATED: _("Updated"), REMOVED: _("Removed"),
            DOWNGRADED: _("Downgraded"), REINSTALLED: _("Reinstalled")}.get(action,
                                                                           action.title())


@dataclass
class Event:
    time: float               # unix timestamp
    action: str               # INSTALLED, UPDATED, REMOVED, DOWNGRADED or REINSTALLED
    name: str
    version: str = ""         # the new version (or the removed one)
    old_version: str = ""     # for updates and downgrades
    source: str = ""          # package manager label, e.g. "APT"
    automatic: bool = False   # pulled in as a dependency


def read_text(path: str) -> str:
    """A log file, transparently unpacking rotated .gz copies. Unreadable means empty."""
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def rotated(path: str) -> list[str]:
    """path, path.1, path.2.gz … oldest first, so events come out in time order."""
    found = [p for p in glob.glob(path + ".*") if re.search(r"\.\d+(\.gz)?$", p)]
    found.sort(key=lambda p: int(re.search(r"\.(\d+)(\.gz)?$", p).group(1)), reverse=True)
    return found + [path]


def newest_first(events: list[Event]) -> list[Event]:
    events.sort(key=lambda e: e.time, reverse=True)
    return events[:MAX_EVENTS]


def parse_timestamp(text: str, formats) -> float | None:
    for fmt in formats:
        try:
            parsed = time.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.endswith("%z") or "%z" in fmt:
            return float(calendar.timegm(parsed) - (parsed.tm_gmtoff or 0))
        return time.mktime(parsed)
    return None


# ---------------------------------------------------------------- APT

_APT_ENTRY = re.compile(r"([^\s,()]+) \(([^)]*)\)")
_APT_ACTIONS = {"Install": INSTALLED, "Upgrade": UPDATED, "Remove": REMOVED, "Purge": REMOVED,
                "Downgrade": DOWNGRADED, "Reinstall": REINSTALLED}


def parse_apt_history(text: str) -> list[Event]:
    """/var/log/apt/history.log: blocks of 'Start-Date: …' then 'Install: a:amd64 (1.0), …'."""
    events: list[Event] = []
    when = None
    for line in text.splitlines():
        key, _sep, value = line.partition(": ")
        if key == "Start-Date":
            when = parse_timestamp(" ".join(value.split()), ("%Y-%m-%d %H:%M:%S",))
        elif key in _APT_ACTIONS and when is not None:
            for name, inside in _APT_ENTRY.findall(value):
                parts = [p.strip() for p in inside.split(", ")]
                automatic = "automatic" in parts
                versions = [p for p in parts if p != "automatic"]
                old, new = (versions + ["", ""])[:2] if len(versions) > 1 else ("", versions[0])
                events.append(Event(when, _APT_ACTIONS[key], name.split(":")[0], new,
                                    old if len(versions) > 1 else "", "APT", automatic))
    return events


# ---------------------------------------------------------------- pacman

_PACMAN = re.compile(r"^\[(?P<when>[^\]]+)\] \[ALPM\] (?P<action>installed|upgraded|removed|"
                     r"downgraded|reinstalled) (?P<name>\S+) \((?P<versions>[^)]*)\)")
_PACMAN_ACTIONS = {"installed": INSTALLED, "upgraded": UPDATED, "removed": REMOVED,
                   "downgraded": DOWNGRADED, "reinstalled": REINSTALLED}


def parse_pacman_log(text: str) -> list[Event]:
    """/var/log/pacman.log: '[2024-01-23T10:11:12+0100] [ALPM] upgraded foo (1.0-1 -> 1.1-1)'."""
    events = []
    for match in map(_PACMAN.match, text.splitlines()):
        if not match:
            continue
        when = parse_timestamp(match["when"], ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M"))
        if when is None:
            continue
        old, _sep, new = match["versions"].partition(" -> ")
        events.append(Event(when, _PACMAN_ACTIONS[match["action"]], match["name"],
                            new or old, old if new else "", "pacman"))
    return events


# ---------------------------------------------------------------- DNF / zypper

_DNF = re.compile(r"^(?P<when>\S+) \w+ (?P<action>Installed|Upgrade|Erase|Downgrade|Reinstall)"
                  r": (?P<nevra>\S+)$")
_DNF_ACTIONS = {"Installed": INSTALLED, "Upgrade": UPDATED, "Erase": REMOVED,
                "Downgrade": DOWNGRADED, "Reinstall": REINSTALLED}


def split_nevra(nevra: str) -> tuple[str, str]:
    """'gimp-2:2.10.38-1.fc40.x86_64' -> ('gimp', '2.10.38-1.fc40')."""
    base = nevra.rsplit(".", 1)[0] if re.search(r"\.(x86_64|i686|aarch64|noarch|\w+)$", nevra) \
        else nevra
    parts = base.rsplit("-", 2)
    if len(parts) < 3:
        return base, ""
    name, version, release = parts
    return name, f"{version.split(':', 1)[-1]}-{release}"


def parse_dnf_rpm_log(text: str) -> list[Event]:
    """/var/log/dnf.rpm.log: '2024-01-23T10:11:12+0100 SUBDEBUG Upgrade: firefox-131.0-1.fc40.x86_64'."""
    events = []
    for match in map(_DNF.match, text.splitlines()):
        if not match:
            continue
        when = parse_timestamp(match["when"], ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"))
        if when is None:
            continue
        name, version = split_nevra(match["nevra"])
        events.append(Event(when, _DNF_ACTIONS[match["action"]], name, version, "", "RPM"))
    return events


def parse_zypper_history(text: str) -> list[Event]:
    """/var/log/zypp/history: 'date|install|name|version|arch|…' and 'date|remove |…'."""
    events = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 4 or fields[1].strip() not in ("install", "remove"):
            continue
        when = parse_timestamp(fields[0], ("%Y-%m-%d %H:%M:%S",))
        if when is not None:
            action = INSTALLED if fields[1].strip() == "install" else REMOVED
            events.append(Event(when, action, fields[2], fields[3], "", "RPM"))
    return events


# ---------------------------------------------------------------- Snap / Flatpak

_SNAP_SUMMARY = re.compile(r'^(Install|Refresh|Remove|Revert) "([^"]+)" snap')
_SNAP_ACTIONS = {"Install": INSTALLED, "Refresh": UPDATED, "Remove": REMOVED,
                 "Revert": DOWNGRADED}


def parse_snap_changes(text: str) -> list[Event]:
    """`snap changes --abs-time`: 'ID Status Spawn Ready Summary' with ISO timestamps."""
    events = []
    for line in text.splitlines()[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5 or parts[1] != "Done":
            continue
        when = parse_timestamp(parts[2].replace("Z", "+0000"),
                               ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"))
        summary = parts[4]
        if when is None:
            continue
        match = _SNAP_SUMMARY.match(summary)
        if match:
            events.append(Event(when, _SNAP_ACTIONS[match.group(1)], match.group(2), "", "",
                                "Snap"))
        elif summary.startswith("Auto-refresh snap"):
            for name in re.findall(r'"([^"]+)"', summary):
                events.append(Event(when, UPDATED, name, "", "", "Snap"))
    return events


_FLATPAK_ACTIONS = {"deploy install": INSTALLED, "deploy update": UPDATED,
                    "uninstall": REMOVED}


def parse_flatpak_history(text: str, now: float | None = None) -> list[Event]:
    """`flatpak history --columns=time,change,application,branch,installation`.

    The time has no year ('Sep  5 12:01:39'): it's this year, unless that would be in the
    future, in which case it was last year.
    """
    now = time.time() if now is None else now
    year = time.localtime(now).tm_year
    events = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 3 or fields[1] not in _FLATPAK_ACTIONS:
            continue
        stamp = " ".join(fields[0].split())
        when = parse_timestamp(f"{year} {stamp}", ("%Y %b %d %H:%M:%S",))
        if when is None:
            continue
        if when > now + 86400:
            when = parse_timestamp(f"{year - 1} {stamp}", ("%Y %b %d %H:%M:%S",))
        branch = fields[3] if len(fields) > 3 else ""
        events.append(Event(when, _FLATPAK_ACTIONS[fields[1]], fields[2], branch, "",
                            "Flatpak"))
    return events


def from_files(paths: list[str], parser) -> list[Event]:
    events: list[Event] = []
    for path in paths:
        if os.path.exists(path):
            events.extend(parser(read_text(path)))
    return newest_first(events)
