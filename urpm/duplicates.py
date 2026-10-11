"""Find apps installed more than once through different package managers.

For example Firefox from APT *and* Snap *and* Flatpak, or Telegram as the
`telegram-desktop` .deb and the `org.telegram.desktop` Flatpak. No GTK in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Packaging words that don't change which app it is: telegram-desktop is Telegram.
NOISE_SUFFIXES = ("desktop", "bin", "app", "stable", "official", "appimage")
MIN_KEY_LENGTH = 3  # shorter names ("go", "vi") collide too easily


@dataclass
class DuplicateGroup:
    name: str                   # the friendliest name among the copies
    copies: list = field(default_factory=list)   # PackageItem-like: .pkg and .source

    @property
    def wasted(self) -> int:
        """Space you'd get back by keeping only the biggest copy."""
        sizes = [c.pkg.size for c in self.copies]
        return sum(sizes) - max(sizes, default=0)


def name_keys(item) -> set[str]:
    """Normalised names a package could be known by."""
    pkg = item.pkg
    candidates = {pkg.name}
    app_id = pkg.extra.get("app_id")
    if app_id:  # Flatpak: org.mozilla.firefox -> firefox, org.telegram.desktop -> telegram
        parts = app_id.split(".")
        # "desktop" in com.bitwarden.desktop isn't a name: Bitwarden and Telegram must not
        # both become "desktop" and look like copies of each other.
        if parts[-1].lower() in NOISE_SUFFIXES and len(parts) > 1:
            candidates.add(parts[-2])
        else:
            candidates.add(parts[-1])
    keys = set()
    for name in candidates:
        key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        for suffix in NOISE_SUFFIXES:
            key = re.sub(rf"-{suffix}$", "", key)
        key = key.replace("-", "")
        if len(key) >= MIN_KEY_LENGTH:
            keys.add(key)
    return keys


def is_app(item) -> bool:
    """Only things you installed count: libraries and runtimes aren't duplicates of apps."""
    return item.pkg.explicit or not item.source.tracks_explicit


def find_duplicates(items) -> list[DuplicateGroup]:
    """Groups of the same app from two or more *different* package managers, biggest
    savings first."""
    by_key: dict[str, list] = {}
    for item in items:
        if is_app(item):
            for key in name_keys(item):
                by_key.setdefault(key, []).append(item)

    groups: list[DuplicateGroup] = []
    claimed: set[int] = set()
    for copies in sorted(by_key.values(), key=len, reverse=True):
        copies = [c for c in copies if id(c) not in claimed]
        if len({c.source.id for c in copies}) < 2:
            continue
        claimed.update(id(c) for c in copies)
        # Prefer a capitalised display name ("Firefox") over a package name ("firefox").
        name = max((c.pkg.name for c in copies), key=lambda n: (n[:1].isupper(), -len(n)))
        groups.append(DuplicateGroup(name, sorted(copies, key=lambda c: c.source.label)))
    groups.sort(key=lambda g: (-g.wasted, g.name.lower()))
    return groups
