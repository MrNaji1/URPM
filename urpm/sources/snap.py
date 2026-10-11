"""Snap packages."""

from __future__ import annotations

import os

from .base import (UNIVERSAL, Action, Cleanup, Details, Package, Source, apply_updates,
                   parse_fields, run)

SNAP_DIR = "/var/lib/snapd/snaps"
DESKTOP_DIR = "/var/lib/snapd/desktop/applications"
INFO_FIELDS = ("publisher", "store-url", "contact", "license", "tracking", "refresh-date")
SYSTEM_NOTES = {"base", "core", "snapd", "gadget", "kernel"}


def parse_snap_list(text: str) -> list[Package]:
    packages = []
    for line in text.splitlines()[1:]:  # first line is the header
        parts = line.split()
        if len(parts) < 5:
            continue
        name, version, rev, tracking, publisher = parts[:5]
        notes = set(parts[5].split(",")) if len(parts) > 5 else set()
        packages.append(Package(
            name=name,
            version=version,
            source="snap",
            section=f"{tracking} · {publisher.rstrip('*✓')}",
            explicit=not (notes & SYSTEM_NOTES),
            ident=f"{name}_{rev}",
        ))
    return packages


def parse_disabled_revisions(text: str) -> list[tuple[str, str]]:
    """`snap list --all`: (name, revision) of every disabled (old, kept-for-rollback) revision."""
    found = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and "disabled" in parts[5].split(","):
            found.append((parts[0], parts[2]))
    return found


def parse_refresh_list(text: str) -> dict[str, str]:
    """`snap refresh --list`: header line, then 'name version rev size publisher notes'."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Name"):
        return {}
    return {parts[0]: parts[1] for parts in map(str.split, lines[1:]) if len(parts) >= 2}


class SnapSource(Source):
    id = "snap"
    label = "Snap"
    emoji = "🐦"
    binary = "snap"
    kind = UNIVERSAL

    def list_packages(self) -> list[Package]:
        packages = parse_snap_list(run("snap", "list", "--unicode=never", "--color=never"))
        for pkg in packages:
            try:
                st = os.stat(os.path.join(SNAP_DIR, f"{pkg.ident}.snap"))
            except OSError:
                continue
            pkg.size, pkg.installed = st.st_size, st.st_mtime
        return packages

    def details(self, pkg: Package) -> Details:
        text = run("snap", "info", "--unicode=never", "--color=never", pkg.name)
        fields = parse_fields(text.split("\nchannels:", 1)[0])
        if fields.get("summary") and not pkg.summary:
            pkg.summary = fields["summary"]
        desc = fields.get("description", "").lstrip("|").strip()
        return Details(description=desc,
                       fields=[(k.title(), fields[k]) for k in INFO_FIELDS if fields.get(k)])

    def launch_argv(self, pkg: Package) -> list[str] | None:
        if not pkg.explicit:
            return None
        try:
            entries = os.listdir(DESKTOP_DIR)
        except OSError:
            return None
        if any(e.startswith(f"{pkg.name}_") for e in entries):
            return ["snap", "run", pkg.name]
        return None

    def remove_action(self, pkg: Package) -> Action:
        return Action(["snap", "remove", pkg.name], root=True,
                      pretty=f"sudo snap remove {pkg.name}")

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        names = [p.name for p in pkgs]
        return [Action(["snap", "refresh", *names], root=True,
                       pretty=f"sudo snap refresh {' '.join(names)}")]

    def check_updates(self, packages: list[Package]) -> int:
        updates = parse_refresh_list(run("snap", "refresh", "--list", timeout=60))
        return apply_updates(packages, updates)

    def desktop_ids(self, pkg: Package) -> list[str]:
        try:
            return sorted(e for e in os.listdir(DESKTOP_DIR) if e.startswith(f"{pkg.name}_"))
        except OSError:
            return []

    def cleanups(self, packages: list[Package]) -> list[Cleanup]:
        old = parse_disabled_revisions(run("snap", "list", "--all", "--unicode=never",
                                           "--color=never"))
        if not old:
            return []
        size = 0
        for name, rev in old:
            try:
                size += os.path.getsize(os.path.join(SNAP_DIR, f"{name}_{rev}.snap"))
            except OSError:
                pass
        return [Cleanup(
            "snap-old-revisions", "Old Snap versions",
            "Snap keeps the previous version of every app so it can roll back. These old "
            "copies aren't running and can go.",
            self.label, size, [f"{name} (revision {rev})" for name, rev in old],
            [Action(["snap", "remove", name, f"--revision={rev}"], root=True)
             for name, rev in old])]
