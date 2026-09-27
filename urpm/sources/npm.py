"""Node packages installed globally with `npm install -g`."""

from __future__ import annotations

import json
import os

from .base import (DEV, Action, Details, Package, Source, apply_updates, can_write, dir_size,
                   mtime, run)

BUNDLED = {"npm", "corepack"}  # ship with Node itself


def read_package_json(folder: str) -> dict:
    try:
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def package_dirs(root: str) -> list[str]:
    dirs = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return dirs
    for entry in entries:
        if entry.startswith("."):
            continue
        path = os.path.join(root, entry)
        if entry.startswith("@"):  # scoped packages: @scope/name
            try:
                dirs.extend(os.path.join(path, sub) for sub in sorted(os.listdir(path)))
            except OSError:
                pass
        else:
            dirs.append(path)
    return dirs


def parse_outdated(text: str) -> dict[str, str]:
    """`npm outdated -g --json`: {name: {current, wanted, latest}}."""
    return {name: info.get("latest", "") for name, info in json.loads(text or "{}").items()}


def homepage_of(meta: dict) -> str:
    if meta.get("homepage"):
        return meta["homepage"]
    repo = meta.get("repository")
    url = repo.get("url", "") if isinstance(repo, dict) else (repo or "")
    return url.removeprefix("git+").removesuffix(".git") if url.startswith(("git+http", "http")) else ""


class NpmSource(Source):
    id = "npm"
    label = "npm (global)"
    emoji = "🟩"
    binary = "npm"
    kind = DEV

    def _root(self) -> str:
        return run("npm", "root", "-g").strip()

    def list_packages(self) -> list[Package]:
        root = self._root()
        packages = []
        for folder in package_dirs(root):
            meta = read_package_json(folder)
            name = meta.get("name") or os.path.relpath(folder, root)
            packages.append(Package(
                name=name,
                version=meta.get("version", ""),
                source="npm",
                summary=meta.get("description") or "",
                size=dir_size(folder),
                section=root,
                explicit=name not in BUNDLED,
                installed=mtime(folder),
                homepage=homepage_of(meta),
                extra={"license": str(meta.get("license") or ""), "path": folder},
            ))
        return packages

    def details(self, pkg: Package) -> Details:
        meta = read_package_json(pkg.extra["path"])
        bins = meta.get("bin")
        bins = ", ".join(bins) if isinstance(bins, dict) else (pkg.name if bins else "")
        fields = [("License", pkg.extra.get("license", "")), ("Commands", bins),
                  ("Location", pkg.extra["path"])]
        return Details(fields=[(k, v) for k, v in fields if v])

    def files(self, pkg: Package) -> list[str]:
        return [os.path.join(d, f) for d, _, names in os.walk(pkg.extra["path"]) for f in names]

    def remove_action(self, pkg: Package) -> Action:
        return Action(["npm", "uninstall", "-g", pkg.name], root=not can_write(pkg.section))

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        root = not can_write(pkgs[0].section) if pkgs else False
        return [Action(["npm", "install", "-g", *(f"{p.name}@latest" for p in pkgs)], root=root)]

    def check_updates(self, packages: list[Package]) -> int:
        # npm outdated exits with 1 when something is outdated.
        text = run("npm", "outdated", "-g", "--json", timeout=180, ok_codes=(0, 1))
        return apply_updates(packages, parse_outdated(text))
