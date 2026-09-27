"""Fedora / openSUSE / RHEL / Rocky / Alma / Mageia packages via rpm (+ dnf / zypper)."""

from __future__ import annotations

import os

from .base import (SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   parse_fields, run, which)

FORMAT = ("%{NAME}\t%{VERSION}-%{RELEASE}\t%{SIZE}\t%{GROUP}\t"
          "%{INSTALLTIME}\t%{URL}\t%{SUMMARY}\n")
INFO_FIELDS = ("Architecture", "License", "Vendor", "Packager", "Source RPM", "Build Date")
RPM_DBS = ("/var/lib/rpm", "/usr/lib/sysimage/rpm")


def parse_rpm_list(text: str) -> list[Package]:
    packages = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 7 or parts[0] == "gpg-pubkey":
            continue
        name, version, size, group, installtime, url, summary = parts[:7]
        packages.append(Package(
            name=name,
            version=version,
            source="rpm",
            summary=summary,
            size=int(size) if size.isdigit() else 0,
            section="" if group == "Unspecified" else group,
            installed=float(installtime) if installtime.isdigit() else None,
            homepage="" if url == "(none)" else url,
        ))
    return packages


def parse_repoquery_upgrades(text: str) -> dict[str, str]:
    """`dnf repoquery --upgrades --qf '%{name} %{evr}\\n'`: 'name [epoch:]version-release'."""
    updates = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            updates[parts[0]] = parts[1].split(":", 1)[-1]  # drop the epoch
    return updates


def parse_zypper_dry_run(text: str) -> list[str]:
    """`zypper -n rm --dry-run foo`: names listed under 'going to be REMOVED:'."""
    names, collecting = [], False
    for line in text.splitlines():
        if "going to be REMOVED" in line:
            collecting = True
        elif collecting and not line.strip():
            break
        elif collecting:
            names.extend(line.split())
    return names


def frontend() -> str | None:
    for tool in ("dnf5", "dnf", "zypper", "yum"):
        if which(tool):
            return tool
    return None


class RpmSource(Source):
    id = "rpm"
    label = "RPM"
    emoji = "🎩"
    binary = "rpm"
    kind = SYSTEM
    tracks_explicit = False

    def list_packages(self) -> list[Package]:
        if not any(os.path.isdir(db) for db in RPM_DBS):
            return []  # rpm installed as a helper tool on a non-RPM system
        return parse_rpm_list(run("rpm", "-qa", "--queryformat", FORMAT))

    def details(self, pkg: Package) -> Details:
        head, _, desc = run("rpm", "-qi", pkg.name).partition("\nDescription :")
        fields = parse_fields(head)
        return Details(description=desc.strip(),
                       fields=[(k, fields[k]) for k in INFO_FIELDS if fields.get(k)])

    def files(self, pkg: Package) -> list[str]:
        return [p for p in run("rpm", "-ql", pkg.name).splitlines() if p.startswith("/")]

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        tool = frontend()
        if tool == "zypper":
            return Action(["zypper", "--non-interactive", "remove", pkg.name], root=True,
                          pretty=f"sudo zypper remove {pkg.name}")
        if tool:
            return Action([tool, "remove", "-y", pkg.name], root=True,
                          pretty=f"sudo {tool} remove {pkg.name}")
        return Action(["rpm", "-e", pkg.name], root=True)

    def simulate_remove(self, pkg: Package) -> list[str] | None:
        tool = frontend()
        if tool == "zypper":
            return parse_zypper_dry_run(run("zypper", "-n", "rm", "--dry-run", pkg.name))
        if tool in ("dnf5", "dnf"):
            # dnf removes everything that depends on the package too.
            text = run(tool, "repoquery", "--installed", "--whatrequires", pkg.name,
                       "--recursive", "--qf", "%{name}\n", "-q", timeout=120)
            return [pkg.name, *sorted({n for n in text.split() if n != pkg.name})]
        return None

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        tool = frontend()
        names = [p.name for p in pkgs]
        if not tool:
            return []
        if tool == "zypper":
            return [Action(["zypper", "--non-interactive", "update", *names], root=True,
                           pretty=f"sudo zypper update {' '.join(names)}")]
        return [Action([tool, "upgrade", "-y", *names], root=True,
                       pretty=f"sudo {tool} upgrade {' '.join(names)}")]

    def check_updates(self, packages: list[Package]) -> int:
        tool = frontend()
        if tool not in ("dnf5", "dnf"):
            return 0
        text = run(tool, "repoquery", "--upgrades", "--qf", "%{name} %{evr}\n", "-q",
                   timeout=180)
        return apply_updates(packages, parse_repoquery_upgrades(text))
