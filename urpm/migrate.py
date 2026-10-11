"""Move to a new computer: save the apps you installed, and reinstall them somewhere else.

`snapshot()` turns what's installed into a small JSON document ("my apps"). On the new
computer, `plan()` works out what's missing and which package managers can install it,
and `script()` writes a plain shell script for computers that don't have URPM yet.
No GTK in this module.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field

from urpm import __version__
from urpm.i18n import _
from urpm.sources import ALL_SOURCES, SYSTEM, Package, Source

FORMAT = "urpm-apps"
VERSION = 1
OS_RELEASE = "/etc/os-release"
# Package managers whose packages can't be reinstalled automatically: pip (distros block
# `pip install` outside a virtual environment), Nix (names aren't install attributes) and
# AppImages (downloaded files). They're listed so you know what to get by hand.
MANUAL_ONLY = {"pip", "nix", "appimage"}
MAX_APPS = 20000
_PLAIN = re.compile(r"^[A-Za-z0-9@._+:/-]+$")  # safe to leave unquoted in the script


def source_by_id(source_id: str) -> Source | None:
    return next((s for s in ALL_SOURCES if s.id == source_id), None)


def wanted(src: Source, pkg: Package) -> bool:
    """Is this something you'd want on a new computer?"""
    if src.kind == SYSTEM and not src.tracks_explicit:
        # RPM and eopkg can't tell what you installed, so only take real apps (the ones
        # with a menu entry), not all 2,000 system packages.
        return bool(pkg.icon)
    return pkg.explicit


def entry(src: Source, pkg: Package) -> dict:
    """One app, as saved in the file: enough to install it again."""
    ident, extra = pkg.name, {}
    if src.id == "flatpak":
        ident = pkg.extra.get("app_id") or pkg.name
        extra = {"installation": pkg.extra.get("installation", "system"),
                 "origin": pkg.extra.get("origin", "flathub")}
    elif src.id == "portage":
        ident = pkg.ident
    elif src.id == "snap" and pkg.extra.get("classic"):
        extra = {"classic": "1"}
    elif src.id == "appimage":
        extra = {"path": pkg.ident}
    item = {"source": src.id, "name": pkg.name, "id": ident}
    if extra:
        item["extra"] = extra
    return item


def os_release(path: str = OS_RELEASE) -> dict[str, str]:
    info = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, _sep, value = line.strip().partition("=")
                if key in ("PRETTY_NAME", "ID", "ID_LIKE", "VERSION_ID"):
                    info[key.lower()] = value.strip().strip('"')
    except OSError:
        pass
    return info


def snapshot(by_source: dict[Source, list[Package]]) -> dict:
    apps = [entry(src, pkg) for src, pkgs in by_source.items() for pkg in pkgs
            if wanted(src, pkg)]
    order = {s.id: i for i, s in enumerate(ALL_SOURCES)}
    apps.sort(key=lambda a: (order.get(a["source"], 99), a["name"].casefold()))
    return {"format": FORMAT, "version": VERSION, "urpm": __version__,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "system": os_release(),
            "apps": apps}


def dumps(snap: dict) -> str:
    return json.dumps(snap, indent=2, ensure_ascii=False) + "\n"


def _safe(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 300 and not value.startswith("-")
            and not any(ch in value for ch in "\n\r\x00\t"))


def load(text: str) -> dict:
    """Read a saved app list. Entries that look wrong are dropped, not trusted.

    Raises ValueError when it isn't an app list at all.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(_("This file isn't a URPM app list")) from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(_("This file isn't a URPM app list"))
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ValueError(_("This app list was saved by a newer URPM. Please update URPM "
                           "first."))
    apps = []
    for item in data.get("apps", [])[:MAX_APPS] if isinstance(data.get("apps"), list) else []:
        if not isinstance(item, dict):
            continue
        if not all(_safe(item.get(k)) for k in ("source", "name", "id")):
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        extra = {k: v for k, v in extra.items() if isinstance(k, str) and _safe(v)}
        apps.append({"source": item["source"], "name": item["name"], "id": item["id"],
                     "extra": extra})
    system = data.get("system") if isinstance(data.get("system"), dict) else {}
    return {**data, "apps": apps,
            "system": {k: v for k, v in system.items() if isinstance(v, str)}}


def to_package(item: dict) -> Package:
    extra = dict(item.get("extra") or {})
    if item["source"] == "flatpak":
        extra.setdefault("app_id", item["id"])
    return Package(name=item["name"], version="", source=item["source"], ident=item["id"],
                   extra=extra)


def installed_keys(src_id: str, pkgs: list[Package]) -> set[str]:
    keys = set()
    for pkg in pkgs:
        keys.add(pkg.name)
        keys.add(pkg.name.split(":")[0])
        if src_id == "flatpak":
            keys.add(pkg.extra.get("app_id", ""))
        elif src_id == "portage":
            keys.add(pkg.ident)
    return keys


@dataclass
class Group:
    """Apps from one package manager that this computer can install."""

    source: Source
    missing: list[Package] = field(default_factory=list)
    present: int = 0
    # Not offered by this computer's package sources (often from an extra repository,
    # like Google Chrome's or Brave's, that has to be added first).
    not_found: list[Package] = field(default_factory=list)


@dataclass
class Plan:
    groups: list[Group] = field(default_factory=list)
    unavailable: dict[str, list[str]] = field(default_factory=dict)  # label -> names
    manual: dict[str, list[str]] = field(default_factory=dict)       # label -> names

    @property
    def missing(self) -> int:
        return sum(len(g.missing) for g in self.groups)

    @property
    def present(self) -> int:
        return sum(g.present for g in self.groups)


def plan(snap: dict, here: list[Source], installed: dict[str, list[Package]],
         check: bool = True) -> Plan:
    """What's missing on this computer, grouped by the package manager that installs it.

    here: package managers on this computer. installed: source id -> installed packages.
    With check, asks each package manager which missing packages it can find (read-only).
    """
    result = Plan()
    by_id = {s.id: s for s in here}
    groups: dict[str, Group] = {}
    keys = {sid: installed_keys(sid, pkgs) for sid, pkgs in installed.items()}
    for item in snap.get("apps", []):
        sid = item["source"]
        known = by_id.get(sid) or source_by_id(sid)
        label = known.label if known else sid
        if sid in MANUAL_ONLY or known is None:
            result.manual.setdefault(label, []).append(item["name"])
            continue
        if sid not in by_id:
            result.unavailable.setdefault(label, []).append(item["name"])
            continue
        group = groups.setdefault(sid, Group(by_id[sid]))
        if item["id"] in keys.get(sid, set()) or item["name"] in keys.get(sid, set()):
            group.present += 1
        else:
            group.missing.append(to_package(item))
    result.groups = [g for g in groups.values() if g.missing or g.present]
    for group in result.groups if check else []:
        try:
            offered = group.source.installable([p.name for p in group.missing])
        except Exception:
            offered = None  # can't tell: try them all
        if offered is not None:
            group.not_found = [p for p in group.missing if p.name not in offered]
            group.missing = [p for p in group.missing if p.name in offered]
    return result


# ---------------------------------------------------------------- shell script

def shell_command(action) -> str:
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in action.env.items())
    command = shlex.join(action.argv)
    if action.root:
        return f"sudo {'env ' + env + ' ' if env else ''}{command}"
    return f"{env + ' ' if env else ''}{command}"


def script(snap: dict, filename: str = "my-apps.sh") -> str:
    """A shell script that reinstalls everything in the list on any Linux computer.

    Each package manager's part only runs if that package manager is installed there.
    """
    system = snap.get("system", {}).get("pretty_name") or "your old computer"
    created = str(snap.get("created", ""))[:10]
    lines = ["#!/bin/sh",
             f"# Apps saved by URPM {snap.get('urpm', '')} on {created} from {system}.",
             "# Run this on your new computer:   sh " + shlex.quote(filename),
             "# Each part only runs if that package manager is installed. Nothing is removed.",
             "", "failed=0", ""]
    by_source: dict[str, list[dict]] = {}
    for item in snap.get("apps", []):
        if _safe(item.get("name")) and _safe(item.get("id")):
            by_source.setdefault(item["source"], []).append(item)
    for sid, items in by_source.items():
        src = source_by_id(sid)
        label = src.label if src else sid
        names = " ".join(i["name"] for i in items)
        actions = [] if src is None or sid in MANUAL_ONLY else \
            src.install_actions([to_package(i) for i in items])
        lines.append(f"# {label}: {len(items)} app{'s' if len(items) != 1 else ''}")
        if not actions:
            lines.append(f"#   Install these yourself: {names}")
            lines.append("")
            continue
        tool = actions[-1].argv[0]
        lines.append(f"if command -v {shlex.quote(tool)} >/dev/null 2>&1; then")
        lines.append(f"    echo {shlex.quote(f'Installing {len(items)} {label} app(s)…')}")
        names = [i["name"] for i in items]
        check = src.exists_check if src else None
        if check and len(actions) == 1 and actions[0].argv[-len(names):] == names \
                and all(_PLAIN.match(n) for n in names):
            # Skip names this computer doesn't know (often from an extra repository),
            # instead of letting one unknown name stop the whole install.
            action = actions[0]
            lines.append('    ok=""; missing=""')
            lines.append(f"    for p in {' '.join(names)}; do")
            lines.append(f'        if {shlex.join(check)} "$p" >/dev/null 2>&1; then ok="$ok $p"; '
                         'else missing="$missing $p"; fi')
            lines.append("    done")
            prefix = shell_command(type(action)(action.argv[:-len(names)], action.root,
                                                action.env))
            lines.append(f'    [ -n "$ok" ] && {{ {prefix} $ok || failed=1; }}')
            lines.append('    [ -n "$missing" ] && echo "Not found in this computer\'s package '
                         'sources (add their repositories first):$missing"')
        else:
            for action in actions:
                lines.append(f"    {shell_command(action)} || failed=1")
        lines.append("else")
        lines.append(f"    echo {shlex.quote(f'Skipping {label}: {tool} is not installed here')}")
        lines.append("fi")
        lines.append("")
    lines += ['if [ "$failed" -ne 0 ]; then', '    echo "Some apps could not be installed. '
              'Scroll up to see which."', "    exit 1", "fi", 'echo "All done!"', ""]
    return "\n".join(lines)
