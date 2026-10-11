"""Flatpak apps and runtimes."""

from __future__ import annotations

import glob
import os
import re
import xml.etree.ElementTree as ET

from urpm.i18n import N_, _

from .base import (UNIVERSAL, Action, Cleanup, Details, Package, Permission, Source,
                   apply_updates, dir_size, parse_fields, parse_size, run, which)

COLUMNS = "application,name,version,size,origin,installation,options,ref,runtime,description"
INSTALL_DIRS = {
    "system": "/var/lib/flatpak",
    "user": os.path.expanduser("~/.local/share/flatpak"),
}
# Per-app data folders. Uninstalling an app leaves its folder behind.
APP_DATA_DIR = "~/.var/app"
FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
_REMOTE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
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
            section=f"{_('Runtime') if is_runtime else _('App')} · {origin} · {installation}",
            explicit=not is_runtime,
            ident=ref,
            extra={"installation": installation, "app_id": app_id, "runtime": runtime,
                   "origin": origin},
        ))
    return packages


def parse_updates(text: str) -> dict[str, str]:
    """`flatpak remote-ls --updates --columns=ref,version` → {ref without app/: version}."""
    updates = {}
    for line in text.splitlines():
        ref, _sep, version = line.partition("\t")
        if "/" in ref:
            kind, _sep, rest = ref.partition("/")
            updates[rest if kind in ("app", "runtime") else ref] = version.strip() or _("new build")
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


# ---------------------------------------------------------------- permissions

_XDG_DIRS = {"xdg-download": ("📥", N_("Your Downloads folder")),
             "xdg-documents": ("📄", N_("Your Documents folder")),
             "xdg-pictures": ("🖼️", N_("Your Pictures folder")),
             "xdg-music": ("🎵", N_("Your Music folder")),
             "xdg-videos": ("🎬", N_("Your Videos folder")),
             "xdg-desktop": ("🖥️", N_("Your Desktop folder")),
             "xdg-run/pipewire-0": ("🔊", N_("Sound and screen sharing (PipeWire)"))}
_SOCKETS = {"x11": ("🪟", N_("X11 display: can see what you type in other X11 apps"), True),
            "pulseaudio": ("🔊", N_("Sound and microphone"), False),
            "system-bus": ("🔓", N_("Talks to every system service"), True),
            "session-bus": ("🔓", N_("Talks to every app and service in your session"), True),
            "ssh-auth": ("🔑", N_("Uses your SSH keys"), True),
            "gpg-agent": ("🔑", N_("Uses your GPG keys"), True),
            "cups": ("🖨️", N_("Printers"), False),
            "pcsc": ("💳", N_("Smart cards"), False)}
_DEVICES = {"all": ("🎥", N_("All devices: camera, USB, game controllers…"), True),
            "dri": ("🎮", N_("Graphics card acceleration"), False),
            "kvm": ("🖥️", N_("Virtual machines (KVM)"), False),
            "input": ("🕹️", N_("Input devices"), False),
            "usb": ("🔌", N_("USB devices"), False)}
_SHARED = {"network": ("🌐", N_("Internet access"), False)}
_BUS_NAMES = {"org.freedesktop.Flatpak": ("⚠️", N_("Can run commands outside its sandbox"), True),
              "org.freedesktop.secrets": ("🔑", N_("Your saved passwords (keyring)"), False),
              "org.freedesktop.Notifications": None, "org.kde.StatusNotifierWatcher": None}


def parse_keyfile(text: str) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1], {})
        elif "=" in line and current is not None and not line.startswith("#"):
            key, _sep, value = line.partition("=")
            current[key.strip()] = value.strip()
    return groups


def _values(text: str) -> list[str]:
    return [v for v in text.split(";") if v and not v.startswith("!")]


def describe_filesystem(entry: str) -> Permission:
    path, _sep, mode = entry.partition(":")

    def read_only(text: str) -> str:
        return _("{what} (read-only)").format(what=text) if mode == "ro" else text

    if path in ("host", "host-os", "host-etc"):
        text = {"host": _("Every file on your computer"),
                "host-os": _("Your system's programs and libraries"),
                "host-etc": _("Your system settings (/etc)")}[path]
        return Permission("💽", read_only(text), True)
    if path in ("home", "~"):
        return Permission("🏠", read_only(_("Your whole home folder")), True)
    for key, (emoji, text) in _XDG_DIRS.items():
        if path == key or path.startswith(key + "/"):
            rest = path[len(key):].lstrip("/")
            return Permission(emoji, read_only(f"{_(text)} ({rest})" if rest else _(text)))
    for key, folder in (("xdg-config", "~/.config"), ("xdg-data", "~/.local/share"),
                        ("xdg-cache", "~/.cache")):
        if path == key or path.startswith(key + "/"):
            path = folder + path[len(key):]
    return Permission("📁", read_only(path))


def parse_permissions(text: str) -> list[Permission]:
    """`flatpak info --show-permissions`: a keyfile. Turn it into plain words, risky first."""
    groups = parse_keyfile(text)
    context = groups.get("Context", {})
    found: list[Permission] = []
    for key, table in (("shared", _SHARED), ("sockets", _SOCKETS), ("devices", _DEVICES)):
        for value in _values(context.get(key, "")):
            if value in table:
                emoji, words, risky = table[value]
                found.append(Permission(emoji, _(words), risky))
    sockets = _values(context.get("sockets", ""))
    if "wayland" in sockets or "fallback-x11" in sockets:
        found.append(Permission("🪟", _("Shows its own windows")))
    found += [describe_filesystem(v) for v in _values(context.get("filesystems", ""))]
    if "devel" in _values(context.get("features", "")):
        found.append(Permission("🛠️", _("Can inspect other programs (debugging)"), True))
    for name, access in groups.get("Session Bus Policy", {}).items():
        if access in ("talk", "own") and name in _BUS_NAMES and _BUS_NAMES[name]:
            emoji, words, risky = _BUS_NAMES[name]
            found.append(Permission(emoji, _(words), risky))
    # Risky first; otherwise keep Flatpak's order.
    return sorted(found, key=lambda p: not p.risky)


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
        return [os.path.join(d, f) for d, _dirs, names in os.walk(root) for f in names]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        if not pkg.explicit:
            return None
        return ["flatpak", "run", installation_flag(pkg), pkg.ident]

    # System installs go through flatpak's own polkit helper, so no pkexec needed.
    def remove_action(self, pkg: Package) -> Action:
        return Action(["flatpak", "uninstall", "-y", "--noninteractive",
                       installation_flag(pkg), pkg.ident],
                      pretty=f"flatpak uninstall {installation_flag(pkg)} {pkg.ident}")

    def install_actions(self, pkgs: list[Package]) -> list[Action]:
        """Install apps by ID, grouped by user/system and remote. Flathub is added first
        if it's missing (most distros don't set it up)."""
        groups: dict[tuple[str, str], list[str]] = {}
        for pkg in pkgs:
            flag = "--user" if pkg.extra.get("installation") == "user" else "--system"
            origin = pkg.extra.get("origin") or "flathub"
            if not _REMOTE_NAME.match(origin):
                origin = "flathub"
            groups.setdefault((flag, origin), []).append(pkg.extra.get("app_id") or pkg.ident)
        actions = []
        for flag in sorted({f for f, o in groups if o == "flathub"}):
            actions.append(Action(["flatpak", "remote-add", "--if-not-exists", flag, "flathub",
                                   FLATHUB_URL]))
        for (flag, origin), ids in groups.items():
            actions.append(Action(["flatpak", "install", "-y", "--noninteractive", flag, origin,
                                   *ids], pretty=f"flatpak install {flag} {origin} {' '.join(ids)}"))
        return actions

    def permissions(self, pkg: Package) -> list[Permission] | None:
        if not pkg.explicit:
            return None
        return parse_permissions(run("flatpak", "info", "--show-permissions",
                                     installation_flag(pkg), pkg.ident))

    def data_folder(self, pkg: Package) -> str | None:
        """~/.var/app/<id>: settings and files the app keeps for you."""
        path = os.path.join(os.path.expanduser(APP_DATA_DIR), pkg.extra.get("app_id", ""))
        return path if pkg.explicit and pkg.extra.get("app_id") and os.path.isdir(path) else None

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
                "flatpak-unused-runtimes", _("Unused Flatpak runtimes"),
                _("Shared platforms that none of your Flatpak apps use any more, usually "
                  "older versions left behind after app updates."),
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
                "flatpak-leftover-data", _("Data from uninstalled Flatpak apps"),
                _("Settings and files that apps you've uninstalled left in ~/.var/app. They'll "
                  "be moved to the Trash, so you can still get them back."),
                self.label, sum(dir_size(d) for d in leftover), leftover,
                [Action(["gio", "trash", *leftover])], kind="data"))
        return found
