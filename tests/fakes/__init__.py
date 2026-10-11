"""A fake Linux system for testing every package manager end to end.

    with FakeSystem() as fake:
        packages = DpkgSource().list_packages()
        fake.calls()   # every command URPM ran, e.g. [["dpkg-query", "-W", ...], ...]

Inside the block, $PATH contains only fake programs (see dispatch.py and responses.py),
$HOME is a throwaway folder, and every file path URPM reads (dpkg's database, Alpine's
"installed" file, Gentoo's /var/db/pkg …) points into a fake root folder. Nothing on the
real machine is read, run or changed.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DISPATCH = HERE / "dispatch.py"

TOOLS = (
    "dpkg-query", "dpkg", "apt-mark", "apt-get", "apt",            # Debian / Ubuntu
    "rpm", "dnf",                                                  # Fedora
    "pacman", "checkupdates",                                      # Arch
    "xbps-query", "xbps-install", "xbps-remove",                   # Void
    "apk",                                                         # Alpine
    "emerge",                                                      # Gentoo
    "eopkg",                                                       # Solus
    "flatpak", "snap", "nix-env", "nix", "brew", "pipx", "npm", "cargo",
    "cargo-install-update", "python3", "gtk-launch", "gio", "apt-cache", "paccache",
    "arch-audit",
)

DESKTOP_ENTRY = "[Desktop Entry]\nType=Application\nName={name}\nExec={name}\n"


def write(path: Path, text: str = "", mode: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path


def shim(path: Path, tool: str) -> None:
    """A tiny program that hands its arguments to dispatch.py as `tool`."""
    write(path, f'#!/bin/sh\nexec "{sys.executable}" "{DISPATCH}" "{tool}" "$@"\n', 0o755)


class FakeSystem:
    def __init__(self, tools=TOOLS):
        self.tools = tools

    def __enter__(self) -> "FakeSystem":
        self._tmp = tempfile.TemporaryDirectory(prefix="urpm-fake-")
        self.root = root = Path(self._tmp.name)
        self.bin = root / "bin"
        self.home = root / "home"
        self.log = root / "calls.log"
        self.log.touch()
        for tool in self.tools:
            shim(self.bin / tool, tool)
        self._build_files()

        from urpm.sources import apk, base, dpkg, eopkg, flatpak, nix, pacman, portage, rpm, snap
        self._patches = [
            mock.patch.dict(os.environ, {
                "PATH": str(self.bin), "HOME": str(self.home),
                "URPM_FAKE_ROOT": str(root), "URPM_FAKE_LOG": str(self.log),
                "CARGO_HOME": str(root / "cargo"),
            }),
            mock.patch.object(base, "EXTRA_PATHS", ()),
            mock.patch.object(dpkg, "STATUS_FILE", str(root / "dpkg" / "status")),
            mock.patch.object(dpkg, "INFO_DIR", str(root / "dpkg" / "info")),
            mock.patch.object(dpkg, "ARCHIVES", str(root / "apt-archives")),
            mock.patch.object(dpkg, "APPLICATIONS", str(root / "usr/share/applications")),
            mock.patch.object(rpm, "DNF_CACHES", (str(root / "dnf-cache"),)),
            mock.patch.object(dpkg, "APT_HISTORY", str(root / "logs/apt/history.log")),
            mock.patch.object(dpkg, "INITIAL_STATUS", str(root / "dpkg/initial-status.gz")),
            mock.patch.object(rpm, "DNF_RPM_LOG", str(root / "logs/dnf.rpm.log")),
            mock.patch.object(rpm, "ZYPP_HISTORY", str(root / "logs/zypp/history")),
            mock.patch.object(pacman, "PACMAN_LOG", str(root / "logs/pacman.log")),
            mock.patch.object(rpm, "RPM_DBS", (str(root / "rpmdb"),)),
            mock.patch.object(apk, "DB", str(root / "apk" / "installed")),
            mock.patch.object(apk, "WORLD", str(root / "apk" / "world")),
            mock.patch.object(portage, "VDB", str(root / "vdb")),
            mock.patch.object(portage, "WORLD", str(root / "portage-world")),
            mock.patch.object(eopkg, "DB", str(root / "eopkg")),
            mock.patch.object(snap, "SNAP_DIR", str(root / "snaps")),
            mock.patch.object(snap, "DESKTOP_DIR", str(root / "snap-desktop")),
            mock.patch.object(nix, "PROFILE_MANIFEST", str(root / "nix-profile-manifest.json")),
            mock.patch.dict(flatpak.INSTALL_DIRS, {"system": str(root / "flatpak"),
                                                   "user": str(root / "flatpak-user")}),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc) -> None:
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    # ------------------------------------------------------------------ fake files

    def _build_files(self) -> None:
        r = self.root
        for name in ("firefox", "gimp"):
            write(r / "usr/share/applications" / f"{name}.desktop",
                  DESKTOP_ENTRY.format(name=name))

        # Debian
        write(r / "dpkg/status", "Package: firefox\nStatus: install ok installed\n")
        write(r / "dpkg/info/firefox.list", "/usr/bin/firefox\n")
        # What the distro installer put on the system (Ubuntu keeps a copy of it)
        with gzip.open(r / "dpkg/initial-status.gz", "wt") as fh:
            fh.write("Package: nano\nStatus: install ok installed\nArchitecture: amd64\n\n"
                     "Package: libc6\nArchitecture: amd64\n")
        (r / "rpmdb").mkdir()

        # Alpine: the installed database, and the "world" file of what you asked for
        write(r / "apk/installed",
              "P:vim\nV:9.1.0-r0\nI:3400000\nT:Improved vi-style text editor\n"
              "U:https://www.vim.org/\nL:Vim\nF:usr/bin\nR:vim\n"
              "F:usr/share/applications\nR:vim.desktop\n\n"
              "P:musl\nV:1.2.5-r0\nI:600000\nT:the musl c library\n")
        write(r / "apk/world", "alpine-base\nvim\n")

        # Gentoo: /var/db/pkg/<category>/<name>-<version>/
        vim = r / "vdb/app-editors/vim-9.1.0"
        for key, value in {"DESCRIPTION": "Vim, an improved vi-style text editor",
                           "SIZE": "33000000", "HOMEPAGE": "https://www.vim.org/ x",
                           "SLOT": "0", "repository": "gentoo", "LICENSE": "vim",
                           "USE": "acl nls"}.items():
            write(vim / key, value + "\n")
        write(vim / "CONTENTS", "dir /usr/bin\nobj /usr/bin/vim 0123abcd 1700000000\n"
                                "sym /usr/bin/vi -> vim 1700000000\n")
        write(r / "vdb/sys-libs/ncurses-6.4/DESCRIPTION", "console display library\n")
        write(r / "portage-world", "app-editors/vim\n")

        # Solus
        write(r / "eopkg/nano/metadata.xml",
              "<PISI><Source><Homepage>https://nano-editor.org</Homepage></Source>"
              "<Package><Name>nano</Name><Summary>Small, friendly text editor</Summary>"
              "<Description>GNU nano is an easy to use text editor.</Description>"
              "<License>GPL-3.0</License><PartOf>system.base</PartOf>"
              "<InstalledSize>2600000</InstalledSize><History><Update release=\"42\">"
              "<Version>8.2</Version></Update></History></Package></PISI>")
        write(r / "eopkg/nano/files.xml", "<Files><File><Path>usr/bin/nano</Path></File></Files>")

        # Flatpak: an app with AppStream metadata, and a runtime
        notes = r / "flatpak/app/org.example.Notes/x86_64/stable/active/files"
        write(notes / "share/metainfo/org.example.Notes.metainfo.xml",
              "<component><description><p>Notes is a tiny notebook.</p>"
              "<ul><li>Markdown</li><li>Sync</li></ul></description></component>")
        write(notes / "bin/notes", "")
        (r / "flatpak/runtime/org.freedesktop.Platform/x86_64/24.08/active/files").mkdir(
            parents=True)

        # History logs, in each package manager's own format (APT's oldest one rotated + gzipped)
        logs = r / "logs"
        write(logs / "apt/history.log",
              "Start-Date: 2026-10-02  09:30:00\nCommandline: apt upgrade\n"
              "Upgrade: firefox:amd64 (131.0+build1, 132.0+build2)\n"
              "End-Date: 2026-10-02  09:31:00\n")
        with gzip.open(logs / "apt/history.log.1.gz", "wt") as fh:
            fh.write("Start-Date: 2026-09-01  18:00:00\nRequested-By: me (1000)\n"
                     "Install: 7zip:amd64 (23.01+dfsg-11), libfoo1:amd64 (1.0, automatic)\n"
                     "End-Date: 2026-09-01  18:00:10\n\n"
                     "Start-Date: 2026-09-15  12:00:00\nRemove: old-tool:amd64 (2.0)\n"
                     "Purge: older-tool:amd64 (1.0)\nEnd-Date: 2026-09-15  12:00:05\n")
        write(logs / "pacman.log",
              "[2026-09-01T10:00:00+0200] [PACMAN] Running 'pacman -S firefox'\n"
              "[2026-09-01T10:00:05+0200] [ALPM] installed firefox (130.0-1)\n"
              "[2026-09-20T10:00:05+0200] [ALPM] upgraded firefox (130.0-1 -> 131.0-1)\n"
              "[2026-09-21T10:00:05+0200] [ALPM] removed old-thing (1.0-1)\n")
        write(logs / "dnf.rpm.log",
              "2026-09-01T10:00:00+0000 SUBDEBUG Installed: gimp-2:2.10.38-1.fc40.x86_64\n"
              "2026-09-20T10:00:00+0000 SUBDEBUG Upgrade: bash-5.2.26-3.fc40.x86_64\n"
              "2026-09-20T10:00:00+0000 SUBDEBUG Upgraded: bash-5.2.26-2.fc40.x86_64\n"
              "2026-09-21T10:00:00+0000 SUBDEBUG Erase: old-lib-1.0-1.fc40.noarch\n")
        write(logs / "zypp/history",
              "# 2026-09-01 10:00:00 vim-9.1.0-1.1.x86_64.rpm installed ok\n"
              "2026-09-01 10:00:00|install|vim|9.1.0-1.1|x86_64|root@pc|repo-oss|abc|\n"
              "2026-09-02 11:00:00|remove |nano|8.0-1.1|x86_64|root@pc|\n")

        # Caches and leftovers for the Cleanup centre
        write(r / "apt-archives/firefox_132.0_amd64.deb", "x" * 5000)
        write(r / "apt-archives/partial/ignored.txt", "x")
        write(r / "dnf-cache/updates/packages/gimp.rpm", "x" * 7000)
        write(r / "pip-cache/http/abc", "x" * 3000)
        write(r / "npm-cache/_cacache/content/xyz", "x" * 9000)
        write(r / "cargo/registry/cache/index/ripgrep-14.1.1.crate", "x" * 4000)
        write(r / "cargo/registry/src/index/ripgrep-14.1.1/lib.rs", "x" * 1000)
        write(self.home / ".var/app/org.example.Notes/config/notes.conf", "kept: installed")
        write(self.home / ".var/app/org.old.Removed/data/big.db", "x" * 6000)

        # Snap
        write(r / "snaps/firefox_4800.snap", "x" * 1900)  # old, disabled revision
        write(r / "snaps/firefox_4955.snap", "x" * 2048)
        write(r / "snaps/core22_1122.snap", "x" * 1024)
        write(r / "snap-desktop/firefox_firefox.desktop", DESKTOP_ENTRY.format(name="firefox"))

        # Homebrew
        write(r / "Cellar/gh/2.57.0/bin/gh", "x" * 4096)

        # pipx: one virtual environment with its own python
        shim(r / "pipx/venvs/black/bin/python", "python")

        # npm: global packages, one scoped; one package.json lies about its name
        write(r / "npm-global/typescript/package.json", json.dumps({
            "name": "typescript", "version": "5.6.2", "description": "TypeScript language",
            "license": "Apache-2.0", "bin": {"tsc": "bin/tsc"},
            "repository": {"url": "git+https://github.com/microsoft/TypeScript.git"}}))
        write(r / "npm-global/@vue/cli/package.json", json.dumps({
            "name": "-rf", "version": "5.0.8", "description": "Vue CLI"}))
        write(r / "npm-global/npm/package.json", json.dumps({"name": "npm", "version": "10.9.0"}))

        # Cargo
        write(r / "cargo/.crates2.json", json.dumps({"installs": {
            "ripgrep 14.1.0 (registry+https://github.com/rust-lang/crates.io-index)":
                {"bins": ["rg"]},
            "bat 0.24.0 (registry+https://github.com/rust-lang/crates.io-index)":
                {"bins": ["bat"]}}}))
        write(r / "cargo/bin/rg", "x" * 5000, 0o755)
        write(r / "cargo/bin/bat", "x" * 7000, 0o755)

        # pip --user site-packages
        site = r / "site"
        for name, dist, version, requested in (("requests", "Requests", "2.31.0", True),
                                               ("urllib3", "urllib3", "2.2.1", False)):
            info = site / f"{name}-{version}.dist-info"
            write(info / "METADATA", f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n"
                                     f"Summary: {name} for Python\n"
                                     f"Home-page: https://example.org/{name}\n")
            write(info / "RECORD", f"{name}/__init__.py,,1200\n")
            write(site / name / "__init__.py", "x" * 1200)
            if requested:  # pip writes REQUESTED for packages you asked for by name
                write(info / "REQUESTED", "")

        # AppImages: one trusted install, one fresh browser download
        write(self.home / "Applications/Tool-1.0-x86_64.AppImage", "x" * 3000, 0o755)
        write(self.home / "Downloads/Fresh-2.0.AppImage", "x" * 1000, 0o644)


__all__ = ["FakeSystem", "TOOLS"]
