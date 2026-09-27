"""Rust tools installed with `cargo install`."""

from __future__ import annotations

import json
import os
import re

from .base import DEV, Action, Details, Package, Source, mtime, which

_KEY_RE = re.compile(r"^(?P<name>\S+) (?P<version>\S+) \((?P<source>[^)]*)\)$")


def cargo_home() -> str:
    return os.environ.get("CARGO_HOME") or os.path.expanduser("~/.cargo")


def describe_source(source: str) -> str:
    kind, _, url = source.partition("+")
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
            summary=f"Provides: {', '.join(bins)}" if bins else "",
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
        return [pkg for pkg, _ in parsed]

    def details(self, pkg: Package) -> Details:
        return Details(fields=[("Source", pkg.section), ("Binaries", pkg.extra.get("bins", ""))])

    def files(self, pkg: Package) -> list[str]:
        return [p for p in pkg.extra.get("bins", "").split("\n") if p]

    def remove_action(self, pkg: Package) -> Action | None:
        return Action(["cargo", "uninstall", pkg.name]) if which("cargo") else None
