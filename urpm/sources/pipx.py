"""Python apps installed with pipx."""

from __future__ import annotations

import json
import os

from .base import DEV, Action, Package, Source, dir_size, mtime, run


def parse_pipx_list(text: str) -> list[Package]:
    """`pipx list --json`."""
    packages = []
    for name, venv in json.loads(text or "{}").get("venvs", {}).items():
        metadata = venv.get("metadata") or {}
        main = metadata.get("main_package") or {}
        apps = main.get("apps") or []
        packages.append(Package(
            name=main.get("package") or name,
            version=main.get("package_version", ""),
            source="pipx",
            summary=f"Provides: {', '.join(apps)}" if apps else "",
            section=metadata.get("python_version") or "",
            ident=name,
        ))
    return packages


class PipxSource(Source):
    id = "pipx"
    label = "pipx"
    emoji = "🧪"
    binary = "pipx"
    kind = DEV

    def list_packages(self) -> list[Package]:
        packages = parse_pipx_list(run("pipx", "list", "--json"))
        venvs = run("pipx", "environment", "--value", "PIPX_LOCAL_VENVS").strip()
        for pkg in packages:
            path = os.path.join(venvs, pkg.ident)
            pkg.size, pkg.installed = dir_size(path), mtime(path)
        return packages

    def remove_action(self, pkg: Package) -> Action:
        return Action(["pipx", "uninstall", pkg.ident])
