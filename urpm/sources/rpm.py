"""Fedora / openSUSE / RHEL / Rocky / Alma / Mageia packages via rpm (+ dnf / zypper)."""

from __future__ import annotations

import os

from urpm.i18n import _

from .base import (Cleanup, dir_size, SYSTEM, Action, Details, Package, Source, apply_updates, desktop_launcher,
                   parse_fields, run, which)

FORMAT = ("%{NAME}\t%{VERSION}-%{RELEASE}\t%{SIZE}\t%{GROUP}\t"
          "%{INSTALLTIME}\t%{URL}\t%{SUMMARY}\n")
INFO_FIELDS = ("Architecture", "License", "Vendor", "Packager", "Source RPM", "Build Date")
RPM_DBS = ("/var/lib/rpm", "/usr/lib/sysimage/rpm")
DNF_CACHES = ("/var/cache/dnf", "/var/cache/libdnf5")
DNF_RPM_LOG = "/var/log/dnf.rpm.log"
ZYPP_HISTORY = "/var/log/zypp/history"


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


def parse_versionlock(text: str) -> set[str]:
    """`dnf versionlock list`: NEVRA patterns (dnf4) or 'Package name: x' lines (dnf5)."""
    from urpm.history import split_nevra
    held = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Package name:"):
            held.add(line.split(":", 1)[1].strip())
        elif line and not line.startswith(("#", "Last metadata", "evr")) and "-" in line:
            held.add(split_nevra(line.removesuffix(".*"))[0])
    return held


def parse_security_advisories(text: str) -> set[str]:
    """`dnf updateinfo list --security`: 'FEDORA-2024-abc Important/Sec. firefox-131.0-1.fc40.x86_64'."""
    from urpm.history import split_nevra
    return {split_nevra(parts[-1])[0] for parts in map(str.split, text.splitlines())
            if len(parts) >= 3 and "/Sec" in parts[1]}


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

    _locks: set[str] | None = None  # None: no versionlock plugin, so no holds

    def list_packages(self) -> list[Package]:
        if not any(os.path.isdir(db) for db in RPM_DBS):
            return []  # rpm installed as a helper tool on a non-RPM system
        packages = parse_rpm_list(run("rpm", "-qa", "--queryformat", FORMAT))
        self._locks = None
        if frontend() in ("dnf5", "dnf"):
            try:
                self._locks = parse_versionlock(run(frontend(), "versionlock", "list", "-q",
                                                    timeout=60))
            except RuntimeError:
                pass  # the versionlock plugin isn't installed
        for pkg in packages:
            pkg.held = bool(self._locks) and pkg.name in self._locks
        return packages

    def details(self, pkg: Package) -> Details:
        head, _sep, desc = run("rpm", "-qi", pkg.name).partition("\nDescription :")
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

    def install_actions(self, pkgs: list[Package]) -> list[Action]:
        names = [p.name for p in pkgs]
        tool = frontend()
        if not names or not tool:
            return []
        if tool == "zypper":
            return [Action(["zypper", "--non-interactive", "install", *names], root=True,
                           pretty=f"sudo zypper install {' '.join(names)}")]
        return [Action([tool, "install", "-y", *names], root=True,
                       pretty=f"sudo {tool} install {' '.join(names)}")]

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
        found = apply_updates(packages, parse_repoquery_upgrades(text))
        try:
            security = parse_security_advisories(run(tool, "updateinfo", "list", "--security",
                                                     "-q", timeout=180, ok_codes=(0, 100)))
        except RuntimeError:
            security = set()  # no advisory data for this repo; updates still work
        for pkg in packages:
            pkg.security = bool(pkg.update) and pkg.name in security
        return found

    def required_by(self, pkg: Package) -> list[str] | None:
        tool = frontend()
        if tool not in ("dnf5", "dnf"):
            return None
        text = run(tool, "repoquery", "--installed", "--whatrequires", pkg.name,
                   "--qf", "%{name}\n", "-q", timeout=120)
        return sorted({n for n in text.split() if n != pkg.name})

    def cleanups(self, packages: list[Package]) -> list[Cleanup]:
        tool = frontend()
        if tool not in ("dnf5", "dnf"):
            return []
        found = []
        unneeded = sorted(set(run(tool, "repoquery", "--unneeded", "--qf", "%{name}\n", "-q",
                                  timeout=120).split()))
        if unneeded:
            sizes = {p.name: p.size for p in packages}
            found.append(Cleanup(
                "dnf-autoremove", _("Unused packages"),
                _("Packages that were installed as dependencies of something you've since "
                  "removed. Nothing installed needs them any more."),
                self.label, sum(sizes.get(n, 0) for n in unneeded), unneeded,
                [Action([tool, "autoremove", "-y"], root=True,
                        pretty=f"sudo {tool} autoremove")]))
        caches = [d for d in DNF_CACHES if os.path.isdir(d)]
        size = sum(dir_size(d) for d in caches)
        if size:
            found.append(Cleanup(
                "dnf-cache", _("DNF download cache"),
                _("Downloaded packages and repository data. DNF fetches fresh copies when it "
                  "needs them."), self.label, size, caches,
                [Action([tool, "clean", "all"], root=True, pretty=f"sudo {tool} clean all")],
                kind="cache"))
        return found

    def history(self) -> list:
        from urpm import history
        if frontend() == "zypper":
            return history.from_files(history.rotated(ZYPP_HISTORY), history.parse_zypper_history)
        return history.from_files(history.rotated(DNF_RPM_LOG), history.parse_dnf_rpm_log)

    def hold_action(self, pkg: Package) -> Action | None:
        if self._locks is None:
            return None
        return Action([frontend(), "versionlock", "add", pkg.name], root=True,
                      pretty=f"sudo {frontend()} versionlock add {pkg.name}")

    def unhold_action(self, pkg: Package) -> Action | None:
        if self._locks is None:
            return None
        return Action([frontend(), "versionlock", "delete", pkg.name], root=True,
                      pretty=f"sudo {frontend()} versionlock delete {pkg.name}")
