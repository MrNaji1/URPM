"""Find each package's icon through the desktop entries (.desktop files) it installs.

Every app menu entry is a .desktop file with an Icon= line. URPM indexes all of them once
per reload, then asks each package manager which entries a package ships
(Source.desktop_ids). No GTK in this module: icons are names or file paths, and the UI
turns them into images.
"""

from __future__ import annotations

import os

from urpm.sources.base import Package, Source


def application_dirs() -> list[str]:
    """Where desktop entries live (XDG data dirs, plus Flatpak and Snap exports)."""
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    roots = [data_home, *data_dirs, "/var/lib/flatpak/exports/share",
             os.path.expanduser("~/.local/share/flatpak/exports/share")]
    dirs = [os.path.join(root, "applications") for root in roots if root]
    dirs.append("/var/lib/snapd/desktop/applications")
    seen, unique = set(), []
    for folder in dirs:
        if folder not in seen:
            seen.add(folder)
            unique.append(folder)
    return unique


def read_icon(path: str) -> str:
    """The Icon= value from a desktop entry's main section."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            in_main = False
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    in_main = line == "[Desktop Entry]"
                elif in_main and line.startswith("Icon="):
                    return line[5:].strip()
    except OSError:
        pass
    return ""


def build_index(dirs: list[str] | None = None) -> dict[str, str]:
    """desktop id (e.g. "firefox.desktop") -> icon. Earlier folders win, like in the
    desktop's own app menu."""
    index: dict[str, str] = {}
    for root in dirs or application_dirs():
        for folder, _subdirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".desktop"):
                    continue
                path = os.path.join(folder, name)
                # applications/kde4/foo.desktop has the id "kde4-foo.desktop"
                desktop_id = os.path.relpath(path, root).replace(os.sep, "-")
                if desktop_id not in index:
                    icon = read_icon(path)
                    if icon:
                        index[desktop_id] = icon
    return index


def assign_icons(source: Source, packages: list[Package], index: dict[str, str]) -> None:
    """Fill in pkg.icon for every package whose desktop entry we can find."""
    for pkg in packages:
        try:
            ids = source.desktop_ids(pkg)
        except Exception:  # an odd package shouldn't stop the others getting icons
            continue
        for desktop_id in ids:
            icon = index.get(desktop_id)
            if icon:
                pkg.icon = icon
                break
