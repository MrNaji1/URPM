"""AppImages lying around in the usual folders (~/Applications, ~/Downloads, /opt, ...)."""

from __future__ import annotations

import os
import re

from .base import UNIVERSAL, Action, Details, Package, Source, can_write

SEARCH_DIRS = ("~/Applications", "~/.local/bin", "~/AppImages", "~/.appimages", "~/Apps",
               "~/bin", "~/Downloads", "~/Desktop", "/opt", "/usr/local/bin")
_ARCH = r"(?:x86[-_]64|amd64|aarch64|arm64|armhf|x64|i[36]86|linux(?:64)?)"
_ARCH_SUFFIX_RE = re.compile(rf"[-_.]{_ARCH}(?:[-_.].*)?$", re.IGNORECASE)
_NAME_RE = re.compile(r"^(?P<name>.+?)[-_ ]v?(?P<version>\d[\w.+~-]*)$")


def split_appimage_name(filename: str) -> tuple[str, str]:
    """'Obsidian-1.4.16-x86_64.AppImage' -> ('Obsidian', '1.4.16')."""
    stem = re.sub(r"\.appimage$", "", filename, flags=re.IGNORECASE)
    stem = _ARCH_SUFFIX_RE.sub("", stem) or stem  # drop "-x86_64" and friends first
    match = _NAME_RE.match(stem)
    return (match["name"], match["version"]) if match else (stem, "")


def find_appimages(dirs=SEARCH_DIRS) -> list[str]:
    found, seen = [], set()
    for folder in dirs:
        folder = os.path.expanduser(folder)
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        # One level down too, for /opt/SomeApp/SomeApp.AppImage style installs.
        candidates = []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and folder in ("/opt",
                                                                  os.path.expanduser("~/Applications")):
                try:
                    candidates.extend(os.scandir(entry.path))
                except OSError:
                    pass
            else:
                candidates.append(entry)
        for entry in candidates:
            if entry.name.lower().endswith(".appimage") and entry.is_file():
                real = os.path.realpath(entry.path)
                if real not in seen:
                    seen.add(real)
                    found.append(entry.path)
    return found


class AppImageSource(Source):
    id = "appimage"
    label = "AppImage"
    emoji = "💿"
    kind = UNIVERSAL
    tracks_explicit = False

    def available(self) -> bool:
        return True  # nothing to install; we just look for files

    def list_packages(self) -> list[Package]:
        packages = []
        home = os.path.expanduser("~")
        for path in find_appimages():
            name, version = split_appimage_name(os.path.basename(path))
            st = os.stat(path)
            folder = os.path.dirname(path)
            packages.append(Package(
                name=name,
                version=version or "—",
                source="appimage",
                summary="Portable app",
                size=st.st_size,
                section=folder.replace(home, "~", 1) if folder.startswith(home) else folder,
                installed=st.st_mtime,
                ident=path,
            ))
        return packages

    def details(self, pkg: Package) -> Details:
        return Details(
            description="AppImages are single-file apps: no installation, no package manager. "
                        "Removing one moves the file to the trash.",
            fields=[("File", pkg.ident),
                    ("Executable", "yes" if os.access(pkg.ident, os.X_OK) else "no")],
        )

    def files(self, pkg: Package) -> list[str]:
        return [pkg.ident]

    def launch_argv(self, pkg: Package) -> list[str]:
        if os.access(pkg.ident, os.X_OK):
            return [pkg.ident]
        # Freshly downloaded AppImages usually aren't executable yet.
        return ["sh", "-c", 'chmod +x "$1" && exec "$1"', "sh", pkg.ident]

    def remove_action(self, pkg: Package) -> Action:
        if can_write(os.path.dirname(pkg.ident)):
            return Action(["gio", "trash", pkg.ident])
        return Action(["rm", "-f", pkg.ident], root=True)
