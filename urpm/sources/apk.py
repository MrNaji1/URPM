"""Alpine Linux / postmarketOS packages via apk."""

from __future__ import annotations

import re

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher, run,
                   split_name_version)

DB = "/lib/apk/db/installed"
WORLD = "/etc/apk/world"
_WORLD_NAME_RE = re.compile(r"^([^<>=~@]+)")


def parse_apk_db(text: str, world: set[str]) -> list[tuple[Package, list[str]]]:
    """The installed db is blocks of 'X:value' lines; F: is a folder, R: a file in it."""
    results = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        files, folder = [], ""
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            if key == "F":
                folder = value
            elif key == "R":
                files.append(f"/{folder}/{value}" if folder else f"/{value}")
            else:
                fields.setdefault(key, value)
        if "P" not in fields:
            continue
        results.append((Package(
            name=fields["P"],
            version=fields.get("V", ""),
            source="apk",
            summary=fields.get("T", ""),
            size=int(fields["I"]) if fields.get("I", "").isdigit() else 0,
            section=fields.get("o", ""),
            explicit=fields["P"] in world,
            homepage=fields.get("U", ""),
            extra={"license": fields.get("L", ""), "maintainer": fields.get("m", "")},
        ), files))
    return results


def parse_world(text: str) -> set[str]:
    return {m.group(1) for m in map(_WORLD_NAME_RE.match, text.split()) if m}


def parse_apk_version(text: str) -> dict[str, str]:
    """`apk version -l '<'`: 'name-1.0-r0 < 1.1-r0'."""
    updates = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "<":
            updates[split_name_version(parts[0])[0]] = parts[2]
    return updates


def parse_apk_simulation(text: str) -> list[str]:
    """`apk del -s foo`: '(1/2) Purging foo (1.0-r0)'."""
    return [line.split("Purging ", 1)[1].split(" (")[0]
            for line in text.splitlines() if "Purging " in line]


class ApkSource(Source):
    id = "apk"
    label = "apk"
    emoji = "🏔️"
    binary = "apk"
    kind = SYSTEM

    def _read(self) -> list[tuple[Package, list[str]]]:
        try:
            with open(WORLD, encoding="utf-8") as fh:
                world = parse_world(fh.read())
        except OSError:
            world = set()
        with open(DB, encoding="utf-8", errors="replace") as fh:
            return parse_apk_db(fh.read(), world)

    def list_packages(self) -> list[Package]:
        return [pkg for pkg, _ in self._read()]

    def details(self, pkg: Package) -> Details:
        fields = [("License", pkg.extra.get("license", "")),
                  ("Maintainer", pkg.extra.get("maintainer", ""))]
        return Details(fields=[(k, v) for k, v in fields if v])

    def files(self, pkg: Package) -> list[str]:
        return next((files for p, files in self._read() if p.name == pkg.name), [])

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        return Action(["apk", "del", pkg.name], root=True, pretty=f"sudo apk del {pkg.name}")

    def simulate_remove(self, pkg: Package) -> list[str] | None:
        return parse_apk_simulation(run("apk", "del", "-s", pkg.name))

    def update_action(self, pkg: Package) -> Action | None:
        if not pkg.update:
            return None
        return Action(["apk", "add", "-u", pkg.name], root=True,
                      pretty=f"sudo apk add -u {pkg.name}")

    def check_updates(self, packages: list[Package]) -> int:
        return apply_updates(packages, parse_apk_version(run("apk", "version", "-l", "<")))
