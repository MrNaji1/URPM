"""Rust tools installed with `cargo install`."""

from __future__ import annotations

import json
import os
import re

from urpm.i18n import _

from .base import DEV, Action, Details, Package, Source, apply_updates, mtime, run, which

_KEY_RE = re.compile(r"^(?P<name>\S+) (?P<version>\S+) \((?P<source>[^)]*)\)$")


def cargo_home() -> str:
    return os.environ.get("CARGO_HOME") or os.path.expanduser("~/.cargo")


def parse_install_update(text: str) -> dict[str, str]:
    """`cargo install-update -l` (from the cargo-update add-on):

        Package  Installed  Latest   Needs update
        ripgrep  v14.1.0    v14.1.1  Yes
    """
    updates = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-1] == "Yes":
            updates[parts[0]] = parts[2].lstrip("v")
    return updates


def describe_source(source: str) -> str:
    kind, _sep, url = source.partition("+")
    if "crates.io" in url or "index.crates.io" in url:
        return "crates.io"
    return url.split("#")[0] if kind in ("git", "registry") else source


def parse_crates2(text: str) -> list[tuple[Package, list[str]]]:
    """~/.cargo/.crates2.json: {"installs": {"name ver (source)": {"bins": [...]}}}."""
    results = []
    for key, info in json.loads(text or "{}").get("installs", {}).items():
        match = _KEY_RE.match(key)
        if not match:
            continue
        bins = info.get("bins") or []
        results.append((Package(
            name=match["name"],
            version=match["version"],
            source="cargo",
            summary=_("Provides: {commands}").format(commands=", ".join(bins)) if bins else "",
            section=describe_source(match["source"]),
        ), bins))
    return results


class CargoSource(Source):
    id = "cargo"
    label = "Cargo"
    emoji = "🦀"
    kind = DEV
    tracks_explicit = False

    def _manifest(self) -> str:
        return os.path.join(cargo_home(), ".crates2.json")

    def available(self) -> bool:
        return os.path.exists(self._manifest())

    def list_packages(self) -> list[Package]:
        with open(self._manifest(), encoding="utf-8") as fh:
            parsed = parse_crates2(fh.read())
        bin_dir = os.path.join(cargo_home(), "bin")
        for pkg, bins in parsed:
            paths = [os.path.join(bin_dir, b) for b in bins]
            pkg.extra["bins"] = "\n".join(paths)
            pkg.size = sum(os.path.getsize(p) for p in paths if os.path.exists(p))
            pkg.installed = max((mtime(p) or 0 for p in paths), default=0) or None
        return [pkg for pkg, _extra in parsed]

    def details(self, pkg: Package) -> Details:
        return Details(fields=[("Source", pkg.section), ("Binaries", pkg.extra.get("bins", ""))])

    def files(self, pkg: Package) -> list[str]:
        return [p for p in pkg.extra.get("bins", "").split("\n") if p]

    def remove_action(self, pkg: Package) -> Action | None:
        return Action(["cargo", "uninstall", pkg.name]) if which("cargo") else None

    def check_updates(self, packages: list[Package]) -> int:
        # Cargo itself can't list updates; the popular cargo-update add-on can.
        if not (which("cargo") and which("cargo-install-update")):
            return 0
        return apply_updates(packages, parse_install_update(run("cargo", "install-update", "-l",
                                                                 timeout=180)))

    def install_actions(self, pkgs: list[Package]) -> list[Action]:
        return [Action(["cargo", "install", *(p.name for p in pkgs)])] if pkgs else []

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        # `cargo install` replaces an installed crate when a newer version exists.
        return [Action(["cargo", "install", *(p.name for p in pkgs)])] if which("cargo") else []
