"""Void Linux packages via xbps."""

from __future__ import annotations

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   parse_fields, parse_size, run, split_name_version)

INFO_FIELDS = ("architecture", "license", "maintainer", "install-date", "repository",
               "run_depends")


def parse_xbps_list(text: str, manual: set[str]) -> list[Package]:
    """`xbps-query -l`: 'ii pkgname-1.2_1   short description'."""
    packages = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[0] != "ii":
            continue
        pkgver = parts[1]
        name, version = split_name_version(pkgver)
        packages.append(Package(
            name=name,
            version=version,
            source="xbps",
            summary=parts[2].strip() if len(parts) > 2 else "",
            explicit=pkgver in manual,
        ))
    return packages


def parse_xbps_updates(text: str) -> dict[str, str]:
    """`xbps-install -nu`: 'pkgver action arch repo ...' per line."""
    updates = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "update":
            name, version = split_name_version(parts[0])
            updates[name] = version
    return updates


class XbpsSource(Source):
    id = "xbps"
    label = "XBPS"
    emoji = "🌀"
    binary = "xbps-query"
    kind = SYSTEM

    def list_packages(self) -> list[Package]:
        manual = set(run("xbps-query", "-m").split())
        return parse_xbps_list(run("xbps-query", "-l"), manual)

    def details(self, pkg: Package) -> Details:
        fields = parse_fields(run("xbps-query", "-S", pkg.name))
        if not pkg.size:
            pkg.size = parse_size(fields.get("installed_size", ""))
        pkg.homepage = pkg.homepage or fields.get("homepage", "")
        return Details(fields=[(k.replace("_", " ").title(), fields[k])
                               for k in INFO_FIELDS if fields.get(k)])

    def files(self, pkg: Package) -> list[str]:
        return [line.split(" -> ")[0] for line in run("xbps-query", "-f", pkg.name).splitlines()]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        return Action(["xbps-remove", "-Ry", pkg.name], root=True,
                      pretty=f"sudo xbps-remove -R {pkg.name}")

    def update_action(self, pkg: Package) -> Action | None:
        if not pkg.update:
            return None
        return Action(["xbps-install", "-uy", pkg.name], root=True,
                      pretty=f"sudo xbps-install -u {pkg.name}")

    def check_updates(self, packages: list[Package]) -> int:
        text = run("xbps-install", "-nu", timeout=120, ok_codes=(0, 6))
        return apply_updates(packages, parse_xbps_updates(text))
