"""Debian / Ubuntu / Mint / Pop!_OS packages via dpkg (and apt for the rest)."""

from __future__ import annotations

import os
import re

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   parse_fields, run, which)

FORMAT = (
    "${binary:Package}\t${Version}\t${Installed-Size}\t${Section}\t"
    "${db:Status-Abbrev}\t${Homepage}\t${binary:Summary}\n"
)
INFO_DIR = "/var/lib/dpkg/info"
EXTRA_FIELDS = ("Maintainer", "Architecture", "Source", "Priority", "Depends", "Recommends")
NONINTERACTIVE = {"DEBIAN_FRONTEND": "noninteractive"}
# There's no terminal to answer "keep your modified config file?", so keep it.
KEEP_CONFIGS = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]
BULLETS = ("- ", "* ", "o ", "+ ", "• ")
_UPGRADABLE_RE = re.compile(r"^(?P<name>[^/\s]+)/\S+\s+(?P<version>\S+)\s.*upgradable from")


def parse_dpkg_list(text: str, manual: set[str] | None, native_arch: str = "") -> list[Package]:
    packages = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name, version, size_kib, section, status, homepage, summary = parts[:7]
        # Status-Abbrev is e.g. "ii " — the second letter is the current state.
        if len(status) < 2 or status[1] != "i":
            continue
        explicit = True
        if manual is not None:
            # apt-mark drops the ":amd64" that dpkg adds to native Multi-Arch packages.
            base, _, arch = name.partition(":")
            explicit = name in manual or (arch == native_arch and base in manual)
        packages.append(Package(
            name=name,
            version=version,
            source="apt",
            summary=summary,
            size=int(size_kib) * 1024 if size_kib.isdigit() else 0,
            section=section,
            explicit=explicit,
            homepage=homepage,
        ))
    return packages


def format_description(raw: str) -> str:
    """dpkg long descriptions: first line is the summary, ' .' marks a blank line.

    The text is hard-wrapped at ~80 columns, so lines are re-joined into
    paragraphs, keeping bullet points on their own lines.
    """
    paragraphs, current = [], []
    for line in raw.split("\n")[1:]:
        line = line.strip()
        if line == ".":
            if current:
                paragraphs.append("\n".join(current))
            current = []
        elif current and not line.startswith(BULLETS):
            current[-1] += " " + line
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return "\n\n".join(paragraphs)


def parse_simulated_removal(text: str) -> list[str]:
    """`apt-get -s remove` prints one 'Remv name [version]' line per package."""
    return [line.split()[1] for line in text.splitlines() if line.startswith("Remv ")]


def parse_upgradable(text: str) -> dict[str, str]:
    """`apt list --upgradable`: 'name/suite 1.2 amd64 [upgradable from: 1.1]'."""
    return {m["name"]: m["version"] for m in map(_UPGRADABLE_RE.match, text.splitlines()) if m}


class DpkgSource(Source):
    id = "apt"
    label = "APT"
    emoji = "📦"
    binary = "dpkg-query"
    kind = SYSTEM

    def list_packages(self) -> list[Package]:
        if not os.path.exists("/var/lib/dpkg/status"):
            return []  # dpkg installed as a helper tool on a non-Debian system
        manual = None
        if which("apt-mark"):
            manual = set(run("apt-mark", "showmanual").split())
        native_arch = run("dpkg", "--print-architecture").strip()
        packages = parse_dpkg_list(run("dpkg-query", "-W", "-f", FORMAT), manual, native_arch)
        for pkg in packages:
            try:
                pkg.installed = os.stat(f"{INFO_DIR}/{pkg.name}.list").st_mtime
            except OSError:
                pass
        return packages

    def details(self, pkg: Package) -> Details:
        fields = parse_fields(run("dpkg-query", "-s", pkg.name))
        return Details(
            description=format_description(fields.get("Description", "")),
            fields=[(k, fields[k]) for k in EXTRA_FIELDS if fields.get(k)],
        )

    def files(self, pkg: Package) -> list[str]:
        paths = run("dpkg-query", "-L", pkg.name).splitlines()
        return [p for p in paths if p.startswith("/") and p != "/." and not os.path.isdir(p)]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        tool = "apt-get" if which("apt-get") else "dpkg"
        argv = ["apt-get", "remove", "-y", pkg.name] if tool == "apt-get" else ["dpkg", "-r", pkg.name]
        return Action(argv, root=True, env=NONINTERACTIVE, pretty=f"sudo apt remove {pkg.name}")

    def simulate_remove(self, pkg: Package) -> list[str] | None:
        if not which("apt-get"):
            return None
        return parse_simulated_removal(run("apt-get", "-s", "remove", pkg.name))

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        if not which("apt-get"):
            return []
        names = [p.name for p in pkgs]
        return [Action(["apt-get", "install", "--only-upgrade", "-y", *KEEP_CONFIGS, *names],
                       root=True, env=NONINTERACTIVE,
                       pretty=f"sudo apt install --only-upgrade {' '.join(names)}")]

    def check_updates(self, packages: list[Package]) -> int:
        if not which("apt"):
            return 0
        updates = parse_upgradable(run("apt", "list", "--upgradable"))
        return apply_updates(packages, updates, key=lambda p: p.name.split(":")[0])
