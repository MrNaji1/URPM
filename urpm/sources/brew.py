"""Homebrew on Linux (Linuxbrew)."""

from __future__ import annotations

import json
import os

from .base import UNIVERSAL, Action, Package, Source, dir_size, run


def parse_brew_info(text: str) -> list[Package]:
    """`brew info --json=v2 --installed`."""
    packages = []
    for formula in json.loads(text or "{}").get("formulae", []):
        installed = formula.get("installed") or [{}]
        current = installed[-1]
        latest = (formula.get("versions") or {}).get("stable", "")
        packages.append(Package(
            name=formula.get("name", "?"),
            version=current.get("version", ""),
            source="brew",
            summary=formula.get("desc") or "",
            section=formula.get("tap", ""),
            explicit=bool(current.get("installed_on_request", True)),
            installed=float(current["time"]) if current.get("time") else None,
            homepage=formula.get("homepage") or "",
            update=latest if formula.get("outdated") else "",
        ))
    return packages


class BrewSource(Source):
    id = "brew"
    label = "Homebrew"
    emoji = "🍺"
    binary = "brew"
    kind = UNIVERSAL

    def list_packages(self) -> list[Package]:
        packages = parse_brew_info(run("brew", "info", "--json=v2", "--installed", timeout=180))
        cellar = run("brew", "--cellar").strip()
        for pkg in packages:
            pkg.size = dir_size(os.path.join(cellar, pkg.name))
        return packages

    def files(self, pkg: Package) -> list[str]:
        root = os.path.join(run("brew", "--cellar").strip(), pkg.name)
        return [os.path.join(d, f) for d, _, names in os.walk(root) for f in names]

    def remove_action(self, pkg: Package) -> Action:
        return Action(["brew", "uninstall", pkg.name])

    def update_action(self, pkg: Package) -> Action | None:
        return Action(["brew", "upgrade", pkg.name]) if pkg.update else None

    def check_updates(self, packages: list[Package]) -> int:
        # `brew info` already reports "outdated", so this is filled in while listing.
        return sum(1 for pkg in packages if pkg.update)
