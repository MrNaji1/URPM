"""Flatpak apps and runtimes."""

from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET

from .base import (Cleanup, dir_size, which, UNIVERSAL, Action, Details, Package, Source, apply_updates, parse_fields,
                   parse_size, run)

COLUMNS = "application,name,version,size,origin,installation,options,ref,runtime,description"
INSTALL_DIRS = {
    "system": "/var/lib/flatpak",
    "user": os.path.expanduser("~/.local/share/flatpak"),
}
# Per-app data folders. Uninstalling an app leaves its folder behind.
APP_DATA_DIR = "~/.var/app"
INFO_FIELDS = ("ID", "Ref", "Branch", "Origin", "Collection", "Installation",
               "Runtime", "Sdk", "License", "Date", "Commit")


def parse_flatpak_list(text: str) -> list[Package]:
    packages = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        app_id, name, version, size, origin, installation, options, ref = parts[:8]
        runtime = parts[8] if len(parts) > 8 else ""
        summary = parts[9] if len(parts) > 9 else ""
        is_runtime = "runtime" in options.split(",")
        packages.append(Package(
            name=name or app_id,
            version=version or ref.rsplit("/", 1)[-1],
            source="flatpak",
            summary=summary,
            size=parse_size(size),
            section=f"{'Runtime' if is_runtime else 'App'} · {origin} · {installation}",
            explicit=not is_runtime,
            ident=ref,
            extra={"installation": installation, "app_id": app_id, "runtime": runtime},
        ))
    return packages


def parse_updates(text: str) -> dict[str, str]:
    """`flatpak remote-ls --updates --columns=ref,version` → {ref without app/: version}."""
    updates = {}
    for line in text.splitlines():
        ref, _, version = line.partition("\t")
        if "/" in ref:
            kind, _, rest = ref.partition("/")
            updates[rest if kind in ("app", "runtime") else ref] = version.strip() or "new build"
    return updates


def parse_metainfo(xml_text: str) -> str:
    """Pull the long description out of an AppStream metainfo file."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    desc = root.find("description")
    if desc is None:
        return ""
    parts = []
    for node in desc:
        if node.tag == "p" and "{http://www.w3.org/XML/1998/namespace}lang" not in node.attrib:
            parts.append(" ".join("".join(node.itertext()).split()))
        elif node.tag in ("ul", "ol"):
            items = [" ".join("".join(li.itertext()).split()) for li in node
                     if "{http://www.w3.org/XML/1998/namespace}lang" not in li.attrib]
            parts.append("\n".join(f"• {item}" for item in items))
    return "\n\n".join(p for p in parts if p)


def installation_flag(pkg: Package) -> str:
    name = pkg.extra.get("installation", "system")
    return f"--{name}" if name in ("system", "user") else f"--installation={name}"


def unused_runtimes(packages: list[Package]) -> list[Package]:
    """Platforms and SDKs that no installed app runs on.

    Deliberately conservative: only main runtimes (…Platform, …Sdk) are considered, never
    extensions such as GL drivers or codecs, whose use Flatpak decides itself.
    """
    used = {p.extra.get("runtime") for p in packages if p.explicit}
    return [p for p in packages if not p.explicit
            and p.extra.get("app_id", "").endswith((".Platform", ".Sdk"))
            and p.ident not in used]


def deploy_time(ref: str, installation: str, explicit: bool) -> float | None:
    base = INSTALL_DIRS.get(installation)
    if not base:
        return None
    kind = "app" if explicit else "runtime"
    try:
        return os.stat(os.path.join(base, kind, ref, "active")).st_mtime
    except OSError:
        return None


class FlatpakSource(Source):
    id = "flatpak"
    label = "Flatpak"
    emoji = "🧩"
    binary = "flatpak"
    kind = UNIVERSAL

    def _list(self) -> list[Package]:
        packages = parse_flatpak_list(run("flatpak", "list", f"--columns={COLUMNS}"))
        for pkg in packages:
            pkg.installed = deploy_time(pkg.ident, pkg.extra["installation"], pkg.explicit)
        return packages

    def details(self, pkg: Package) -> Details:
        text = run("flatpak", "info", installation_flag(pkg), pkg.ident)
        fields = parse_fields(text, indented_keys=True)
        return Details(description=self._description(pkg),
                       fields=[(k, fields[k]) for k in INFO_FIELDS if fields.get(k)])

    @staticmethod
    def _files_root(pkg: Package) -> str:
        kind = "app" if pkg.explicit else "runtime"
        return os.path.join(INSTALL_DIRS.get(pkg.extra["installation"], ""), kind, pkg.ident,
                            "active", "files")

    def _description(self, pkg: Package) -> str:
        share = os.path.join(self._files_root(pkg), "share")
        app_id = pkg.extra.get("app_id", "")
        patterns = [f"metainfo/{app_id}.*.xml", f"appdata/{app_id}.*.xml"]
        if pkg.explicit:  # runtimes bundle other components' metadata too
            patterns += ["metainfo/*.xml", "appdata/*.xml"]
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(share, pattern))):
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = parse_metainfo(fh.read())
                except OSError:
                    continue
                if text:
                    return text
        return ""

    def files(self, pkg: Package) -> list[str] | None:
        root = self._files_root(pkg)
        if not os.path.isdir(root):
            return None
        return [os.path.join(d, f) for d, _, names in os.walk(root) for f in names]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        if not pkg.explicit:
            return None
        return ["flatpak", "run", installation_flag(pkg), pkg.ident]

    # System installs go through flatpak's own polkit helper, so no pkexec needed.
    def remove_action(self, pkg: Package) -> Action:
        return Action(["flatpak", "uninstall", "-y", "--noninteractive",
                       installation_flag(pkg), pkg.ident],
                      pretty=f"flatpak uninstall {installation_flag(pkg)} {pkg.ident}")

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        # One command per installation (system, user, custom ones).
        by_flag: dict[str, list[str]] = {}
        for pkg in pkgs:
            by_flag.setdefault(installation_flag(pkg), []).append(pkg.ident)
        return [Action(["flatpak", "update", "-y", "--noninteractive", flag, *refs],
                       pretty=f"flatpak update {flag} {' '.join(refs)}")
                for flag, refs in by_flag.items()]

    def check_updates(self, packages: list[Package]) -> int:
        updates = parse_updates(run("flatpak", "remote-ls", "--updates", "--columns=ref,version",
                                    timeout=60))
        return apply_updates(packages, updates, key=lambda p: p.ident, same_version_ok=True)

    def list_packages(self) -> list[Package]:
        self._installed = self._list()
        masks: dict[str, set[str]] = {}
        for pkg in self._installed:
            flag = installation_flag(pkg)
            if flag not in masks:
                try:
                    masks[flag] = set(run("flatpak", "mask", flag).split())
                except RuntimeError:
                    masks[flag] = set()  # Flatpak before 1.11 has no masks
            pkg.held = pkg.explicit and pkg.extra.get("app_id") in masks[flag]
        return self._installed

    def history(self) -> list:
        from urpm import history
        names = {p.extra.get("app_id"): p.name for p in getattr(self, "_installed", [])}
        events = history.parse_flatpak_history(run(
            "flatpak", "history", "--columns=time,change,application,branch,installation",
            timeout=60))
        for event in events:  # show "Discord", not "com.discordapp.Discord", when we can
            event.name = names.get(event.name, event.name)
        return history.newest_first(events)

    def hold_action(self, pkg: Package) -> Action | None:
        if not pkg.explicit:
            return None
        flag, app_id = installation_flag(pkg), pkg.extra["app_id"]
        return Action(["flatpak", "mask", flag, app_id], pretty=f"flatpak mask {flag} {app_id}")

    def unhold_action(self, pkg: Package) -> Action | None:
        if not pkg.explicit:
            return None
        flag, app_id = installation_flag(pkg), pkg.extra["app_id"]
        return Action(["flatpak", "mask", "--remove", flag, app_id],
                      pretty=f"flatpak mask --remove {flag} {app_id}")

    def required_by(self, pkg: Package) -> list[str] | None:
        if pkg.explicit:
            return None  # Flatpak doesn't track what uses an app
        return [p.name for p in getattr(self, "_installed", [])
                if p.explicit and p.extra.get("runtime") == pkg.ident]

    def desktop_ids(self, pkg: Package) -> list[str]:
        return [f"{pkg.extra['app_id']}.desktop"] if pkg.explicit else []

    def cleanups(self, packages: list[Package]) -> list[Cleanup]:
        found = []
        unused = unused_runtimes(packages)
        if unused:
            by_flag: dict[str, list[str]] = {}
            for pkg in unused:
                by_flag.setdefault(installation_flag(pkg), []).append(pkg.ident)
            found.append(Cleanup(
                "flatpak-unused-runtimes", "Unused Flatpak runtimes",
                "Shared platforms that none of your Flatpak apps use any more, usually "
                "older versions left behind after app updates.",
                self.label, sum(p.size for p in unused),
                [f"{p.name} ({p.ident.rsplit('/', 1)[-1]})" for p in unused],
                [Action(["flatpak", "uninstall", "-y", "--noninteractive", flag, *refs],
                        pretty=f"flatpak uninstall {flag} {' '.join(refs)}")
                 for flag, refs in by_flag.items()]))
        data_dir = os.path.expanduser(APP_DATA_DIR)
        installed = {p.extra.get("app_id") for p in packages if p.explicit}
        try:
            leftover = sorted(os.path.join(data_dir, d) for d in os.listdir(data_dir)
                              if d not in installed)
        except OSError:
            leftover = []
        if leftover and which("gio"):
            found.append(Cleanup(
                "flatpak-leftover-data", "Data from uninstalled Flatpak apps",
                "Settings and files that apps you've uninstalled left in ~/.var/app. They'll "
                "be moved to the Trash, so you can still get them back.",
                self.label, sum(dir_size(d) for d in leftover), leftover,
                [Action(["gio", "trash", *leftover])], kind="data"))
        return found
