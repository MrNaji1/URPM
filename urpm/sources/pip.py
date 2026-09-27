"""Python packages installed with `pip install --user` (system ones are left to the distro)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import site
import sysconfig

from .base import DEV, Action, Details, Package, Source, apply_updates, mtime, run


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def externally_managed() -> bool:
    """PEP 668: distros like Debian 12+ / Ubuntu 23.04+ block pip without a flag."""
    return os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED"))


def parse_outdated(text: str) -> dict[str, str]:
    """`pip list --outdated --format=json`."""
    return {normalize(row["name"]): row["latest_version"] for row in json.loads(text or "[]")}


class PipSource(Source):
    id = "pip"
    label = "pip (user)"
    emoji = "🐍"
    kind = DEV

    def __init__(self):
        self.site = site.getusersitepackages()

    def available(self) -> bool:
        return os.path.isdir(self.site)

    def _dists(self):
        return importlib.metadata.distributions(path=[self.site])

    def list_packages(self) -> list[Package]:
        packages, dists = [], list(self._dists())
        # pip >= 20.2 drops a REQUESTED file next to packages you asked for by name.
        tracks = any(d.read_text("REQUESTED") is not None for d in dists)
        for dist in dists:
            meta = dist.metadata
            name = meta["Name"]
            if not name:
                continue
            path = getattr(dist, "_path", None)
            homepage = meta.get("Home-page") or ""
            if not homepage:
                urls = meta.get_all("Project-URL") or []
                homepage = urls[0].split(",", 1)[-1].strip() if urls else ""
            packages.append(Package(
                name=name,
                version=dist.version,
                source="pip",
                summary=meta.get("Summary") or "",
                size=sum(f.size or 0 for f in dist.files or []),
                section="user site-packages",
                explicit=(dist.read_text("REQUESTED") is not None) if tracks else True,
                installed=mtime(str(path)) if path else None,
                homepage=homepage,
                ident=normalize(name),
            ))
        return packages

    def _dist(self, pkg: Package):
        return next((d for d in self._dists() if normalize(d.metadata["Name"] or "") == pkg.ident),
                    None)

    def details(self, pkg: Package) -> Details:
        dist = self._dist(pkg)
        if not dist:
            return Details()
        meta = dist.metadata
        fields = [("Requires", ", ".join(dist.requires or [])),
                  ("License", meta.get("License") or ""),
                  ("Author", meta.get("Author") or meta.get("Author-email") or "")]
        body = meta.get_payload() if hasattr(meta, "get_payload") else ""
        return Details(description=(body or meta.get("Description") or "").strip()[:4000],
                       fields=[(k, v) for k, v in fields if v and len(v) < 400])

    def files(self, pkg: Package) -> list[str]:
        dist = self._dist(pkg)
        return [str(dist.locate_file(f)) for f in dist.files or []] if dist else []

    def _pip(self, *args: str) -> list[str]:
        extra = ["--break-system-packages"] if externally_managed() else []
        return ["python3", "-m", "pip", *args, *extra]

    def remove_action(self, pkg: Package) -> Action | None:
        if not importlib.util.find_spec("pip"):
            return None
        return Action(self._pip("uninstall", "-y", pkg.name),
                      pretty=f"python3 -m pip uninstall {pkg.name}")

    def update_action(self, pkg: Package) -> Action | None:
        if not pkg.update or not importlib.util.find_spec("pip"):
            return None
        return Action(self._pip("install", "--user", "--upgrade", pkg.name),
                      pretty=f"python3 -m pip install --user --upgrade {pkg.name}")

    def check_updates(self, packages: list[Package]) -> int:
        if not importlib.util.find_spec("pip"):
            return 0
        text = run("python3", "-m", "pip", "list", "--user", "--outdated", "--format=json",
                   "--disable-pip-version-check", timeout=180)
        return apply_updates(packages, parse_outdated(text), key=lambda p: p.ident)
