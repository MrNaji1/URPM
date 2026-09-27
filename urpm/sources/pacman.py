"""Arch / Manjaro / EndeavourOS / CachyOS / Garuda packages via pacman."""

from __future__ import annotations

import time

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   parse_fields, parse_size, run, which)

INFO_FIELDS = ("Architecture", "Licenses", "Groups", "Packager", "Depends On",
               "Optional Deps", "Required By")
DATE_FORMATS = ("%a %d %b %Y %I:%M:%S %p", "%a %d %b %Y %H:%M:%S", "%a %b %d %H:%M:%S %Y")


def parse_date(text: str) -> float | None:
    words = text.split()
    if words and words[-1].isalpha() and words[-1].isupper():
        words = words[:-1]  # drop the timezone abbreviation, e.g. "CET"
    for fmt in DATE_FORMATS:
        try:
            return time.mktime(time.strptime(" ".join(words), fmt))
        except ValueError:
            continue
    return None


def parse_pacman_info(text: str) -> list[tuple[Package, dict[str, str]]]:
    results = []
    for block in text.strip().split("\n\n"):
        fields = parse_fields(block)
        if "Name" not in fields:
            continue
        homepage = fields.get("URL", "")
        results.append((Package(
            name=fields["Name"],
            version=fields.get("Version", ""),
            source="pacman",
            summary=fields.get("Description", ""),
            size=parse_size(fields.get("Installed Size", "")),
            section=fields.get("Repository", ""),
            explicit=fields.get("Install Reason", "").startswith("Explicitly"),
            installed=parse_date(fields.get("Install Date", "")),
            homepage="" if homepage == "None" else homepage,
        ), fields))
    return results


def parse_checkupdates(text: str) -> dict[str, str]:
    """`checkupdates` / `pacman -Qu`: 'name old -> new'."""
    updates = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "->":
            updates[parts[0]] = parts[3]
    return updates


class PacmanSource(Source):
    id = "pacman"
    label = "pacman"
    emoji = "👾"
    binary = "pacman"
    kind = SYSTEM

    def list_packages(self) -> list[Package]:
        return [pkg for pkg, _ in parse_pacman_info(run("pacman", "-Qi"))]

    def details(self, pkg: Package) -> Details:
        parsed = parse_pacman_info(run("pacman", "-Qi", pkg.name))
        fields = parsed[0][1] if parsed else {}
        return Details(fields=[(k, fields[k]) for k in INFO_FIELDS
                               if fields.get(k) and fields[k] != "None"])

    def files(self, pkg: Package) -> list[str]:
        return [p for p in run("pacman", "-Qlq", pkg.name).splitlines() if not p.endswith("/")]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        return Action(["pacman", "-Rs", "--noconfirm", pkg.name], root=True,
                      pretty=f"sudo pacman -Rs {pkg.name}")

    def simulate_remove(self, pkg: Package) -> list[str] | None:
        text = run("pacman", "-Rs", "--print", "--print-format", "%n", pkg.name)
        return [line.strip() for line in text.splitlines() if line.strip()]

    # No per-package update: partial upgrades are unsupported on Arch. Updates are
    # still listed so you know it's time for `sudo pacman -Syu`.
    def check_updates(self, packages: list[Package]) -> int:
        if not which("checkupdates"):  # from pacman-contrib; safe, uses a temp db
            return 0
        updates = parse_checkupdates(run("checkupdates", timeout=180, ok_codes=(0, 2)))
        return apply_updates(packages, updates)
