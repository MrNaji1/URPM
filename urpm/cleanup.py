"""The Cleanup centre's data: everything that can be removed to free disk space.

Package managers report their own cleanups (unused packages, old versions, download
caches) through Source.cleanups(). Developer tools' caches live here, because a cache can
exist even when URPM doesn't list that tool's packages. Every Cleanup says exactly what
will be removed and which commands will run, so the UI can preview it before anything
happens. No GTK in this module.
"""

from __future__ import annotations

import os
import re

from urpm.sources.base import (Action, Cleanup, Package, Source, UnsafeCommand, dir_size,
                               parse_size, run, which)


def pip_cache() -> Cleanup | None:
    if not which("python3"):
        return None
    try:
        folder = run("python3", "-m", "pip", "cache", "dir", timeout=30).strip()
    except RuntimeError:
        return None  # no pip, or its cache is turned off
    size = dir_size(folder) if os.path.isdir(folder) else 0
    if not size:
        return None
    return Cleanup("pip-cache", "pip download cache",
                   "Python packages pip downloaded before. They'll be downloaded again if "
                   "you install them later.", "pip", size, [folder],
                   [Action(["python3", "-m", "pip", "cache", "purge"])], kind="cache")


def npm_cache() -> Cleanup | None:
    if not which("npm"):
        return None
    folder = os.path.join(run("npm", "config", "get", "cache", timeout=30).strip(), "_cacache")
    size = dir_size(folder) if os.path.isdir(folder) else 0
    if not size:
        return None
    return Cleanup("npm-cache", "npm download cache",
                   "Copies of every package npm has downloaded. npm fetches them again when "
                   "a project needs them.", "npm", size, [folder],
                   [Action(["npm", "cache", "clean", "--force"])], kind="cache")


def cargo_cache() -> Cleanup | None:
    home = os.environ.get("CARGO_HOME") or os.path.expanduser("~/.cargo")
    folders = [d for d in (os.path.join(home, "registry", "cache"),
                           os.path.join(home, "registry", "src")) if os.path.isdir(d)]
    size = sum(dir_size(d) for d in folders)
    if not size:
        return None
    return Cleanup("cargo-cache", "Cargo download cache",
                   "Downloaded crates and their unpacked sources. Cargo downloads them again "
                   "when a project needs them.", "Cargo", size, folders,
                   [Action(["rm", "-rf", "--", *folders])], kind="cache")


def parse_brew_cleanup_dry_run(text: str) -> int:
    """`brew cleanup -n`: '…would free approximately 1.2GB of disk space.'"""
    match = re.search(r"free approximately ([\d.]+\s*[KMGT]?B)", text)
    return parse_size(match.group(1)) if match else 0


def brew_cache() -> Cleanup | None:
    if not which("brew"):
        return None
    size = parse_brew_cleanup_dry_run(run("brew", "cleanup", "-n", timeout=120))
    if not size:
        return None
    return Cleanup("brew-cleanup", "Old Homebrew versions and downloads",
                   "Older versions of upgraded formulae and cached downloads.", "Homebrew",
                   size, [run("brew", "--cache").strip()], [Action(["brew", "cleanup"])],
                   kind="cache")


CACHES = (pip_cache, npm_cache, cargo_cache, brew_cache)


def check(item: Cleanup) -> None:
    """Refuse a cleanup whose package names could be read as command options."""
    for action in item.actions:
        for name in item.items:
            if name in action.argv and (name.startswith("-") or "\n" in name):
                raise UnsafeCommand(f"refusing to pass {name!r} to {action.argv[0]}")


def scan(by_source: dict[Source, list[Package]]) -> tuple[list[Cleanup], list[str]]:
    """Everything that can be cleaned, biggest first, and any errors along the way."""
    found: list[Cleanup] = []
    errors: list[str] = []
    for src, packages in by_source.items():
        try:
            found.extend(src.cleanups(packages))
        except Exception as exc:  # one broken tool shouldn't hide the others
            errors.append(f"{src.label}: {exc}")
    for check in CACHES:
        try:
            item = check()
        except Exception as exc:
            errors.append(f"{check.__name__.replace('_', ' ')}: {exc}")
            continue
        if item:
            found.append(item)
    found.sort(key=lambda c: c.size, reverse=True)
    return found, errors
