"""Gentoo / Funtoo / Calculate packages from the Portage database."""

from __future__ import annotations

import os
import shlex

from .base import (SYSTEM, Action, Details, Package, Source, desktop_launcher, mtime,
                   split_name_version)

VDB = "/var/db/pkg"
WORLD = "/var/lib/portage/world"


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def parse_world(text: str) -> set[str]:
    """World entries look like 'app-editors/vim' or 'dev-lang/python:3.12'."""
    return {line.split(":")[0].strip() for line in text.splitlines() if line.strip()}


def parse_contents(text: str) -> list[str]:
    """CONTENTS lines: 'obj /path md5 mtime', 'sym /link -> target mtime', 'dir /path'."""
    files = []
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "obj":
            files.append(rest.rsplit(" ", 2)[0])
        elif kind == "sym":
            files.append(rest.split(" -> ")[0])
    return files


class PortageSource(Source):
    id = "portage"
    label = "Portage"
    emoji = "🐮"
    binary = "emerge"
    kind = SYSTEM

    def available(self) -> bool:
        return os.path.isdir(VDB) and super().available()

    def list_packages(self) -> list[Package]:
        world = parse_world(read(WORLD))
        packages = []
        for category in sorted(os.listdir(VDB)):
            cat_dir = os.path.join(VDB, category)
            if not os.path.isdir(cat_dir):
                continue
            for pf in os.listdir(cat_dir):
                path = os.path.join(cat_dir, pf)
                name, version = split_name_version(pf)
                size = read(os.path.join(path, "SIZE"))
                packages.append(Package(
                    name=name,
                    version=version,
                    source="portage",
                    summary=read(os.path.join(path, "DESCRIPTION")),
                    size=int(size) if size.isdigit() else 0,
                    section=category,
                    explicit=f"{category}/{name}" in world,
                    installed=mtime(os.path.join(path, "CONTENTS")),
                    homepage=read(os.path.join(path, "HOMEPAGE")).split(" ")[0],
                    ident=f"{category}/{name}",
                    extra={"path": path},
                ))
        return packages

    def details(self, pkg: Package) -> Details:
        path = pkg.extra["path"]
        fields = [("Slot", read(os.path.join(path, "SLOT"))),
                  ("Repository", read(os.path.join(path, "repository"))),
                  ("License", read(os.path.join(path, "LICENSE"))),
                  ("USE flags", read(os.path.join(path, "USE")))]
        return Details(fields=[(k, v) for k, v in fields if v])

    def files(self, pkg: Package) -> list[str]:
        return parse_contents(read(os.path.join(pkg.extra["path"], "CONTENTS")))

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return desktop_launcher(self.files(pkg))

    def remove_action(self, pkg: Package) -> Action:
        # Drop it from @world, then depclean, which refuses if something still needs it.
        atom = shlex.quote(pkg.ident)
        script = f"emerge --deselect {atom} && emerge --depclean --ask=n {atom}"
        return Action(["sh", "-c", script], root=True,
                      pretty=f"sudo emerge --deselect {atom} && sudo emerge --depclean {atom}")
