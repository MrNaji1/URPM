"""Solus packages via eopkg."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   mtime, run)

DB = "/var/lib/eopkg/package"


def parse_metadata(xml_text: str) -> Package | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    pkg = root.find("Package")
    if pkg is None or pkg.findtext("Name") is None:
        return None
    update = pkg.find("History/Update")
    version = update.findtext("Version", "") if update is not None else ""
    release = update.get("release", "") if update is not None else ""
    size = pkg.findtext("InstalledSize", "0")
    return Package(
        name=pkg.findtext("Name", ""),
        version=f"{version}-{release}" if release else version,
        source="eopkg",
        summary=(pkg.findtext("Summary") or "").strip(),
        size=int(size) if size.isdigit() else 0,
        section=pkg.findtext("PartOf", ""),
        homepage=root.findtext("Source/Homepage", ""),
        extra={"description": (pkg.findtext("Description") or "").strip(),
               "license": pkg.findtext("License", "")},
    )


def parse_files(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return ["/" + path.lstrip("/") for path in (f.findtext("Path", "") for f in root.iter("File"))
            if path]


def parse_list_upgrades(text: str) -> dict[str, str]:
    """`eopkg list-upgrades`: 'name - summary' lines (no version shown)."""
    updates = {}
    for line in text.splitlines():
        name, sep, _ = line.partition(" - ")
        if sep and name.strip() and " " not in name.strip():
            updates[name.strip()] = "newer release"
    return updates


class EopkgSource(Source):
    id = "eopkg"
    label = "eopkg"
    emoji = "☀️"
    binary = "eopkg"
    kind = SYSTEM
    tracks_explicit = False

    def list_packages(self) -> list[Package]:
        packages = []
        for entry in os.listdir(DB):
            path = os.path.join(DB, entry, "metadata.xml")
            try:
                with open(path, encoding="utf-8") as fh:
                    pkg = parse_metadata(fh.read())
            except OSError:
                continue
            if pkg:
                pkg.installed = mtime(path)
                pkg.extra["dir"] = os.path.join(DB, entry)
                packages.append(pkg)
        return packages

    def details(self, pkg: Package) -> Details:
        fields = [("License", pkg.extra.get("license", ""))]
        return Details(description=pkg.extra.get("description", ""),
                       fields=[(k, v) for k, v in fields if v])

    def files(self, pkg: Package) -> list[str]:
        try:
            with open(os.path.join(pkg.extra["dir"], "files.xml"), encoding="utf-8") as fh:
                return parse_files(fh.read())
        except OSError:
            return []

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        return Action(["eopkg", "remove", "-y", pkg.name], root=True,
                      pretty=f"sudo eopkg remove {pkg.name}")

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        names = [p.name for p in pkgs]
        return [Action(["eopkg", "upgrade", "-y", *names], root=True,
                       pretty=f"sudo eopkg upgrade {' '.join(names)}")]

    def check_updates(self, packages: list[Package]) -> int:
        return apply_updates(packages, parse_list_upgrades(run("eopkg", "list-upgrades")))
