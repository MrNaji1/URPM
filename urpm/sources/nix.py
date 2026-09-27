"""Nix packages in your user profile (nix-env or the newer `nix profile`)."""

from __future__ import annotations

import json
import os

from .base import UNIVERSAL, Action, Package, Source, run, split_name_version

NIX = ["nix", "--extra-experimental-features", "nix-command flakes"]
PROFILE_MANIFEST = os.path.expanduser("~/.nix-profile/manifest.json")


def parse_nix_env(text: str) -> list[Package]:
    """`nix-env -q --json`: {attr: {"name": "hello-2.12", "pname": ..., "version": ...}}."""
    packages = []
    for attr, info in json.loads(text or "{}").items():
        name, version = info.get("pname"), info.get("version")
        if not name:
            name, version = split_name_version(info.get("name", attr))
        packages.append(Package(name=name, version=version or "", source="nix",
                                section="nix-env", ident=name))
    return packages


def parse_nix_profile(text: str) -> list[Package]:
    """`nix profile list --json`: elements is a dict (new) or a list (older Nix)."""
    elements = json.loads(text or "{}").get("elements", {})
    items = elements.items() if isinstance(elements, dict) else enumerate(elements)
    packages = []
    for key, info in items:
        store_paths = info.get("storePaths") or [""]
        # /nix/store/<32-char hash>-hello-2.12.1
        name, version = split_name_version(os.path.basename(store_paths[0])[33:])
        packages.append(Package(
            name=str(key) if isinstance(key, str) else (name or "?"),
            version=version,
            source="nix",
            section=info.get("originalUrl", "nix profile"),
            summary=info.get("attrPath", ""),
            ident=str(key),
            extra={"profile": "1"},
        ))
    return packages


class NixSource(Source):
    id = "nix"
    label = "Nix"
    emoji = "❄️"
    binary = "nix-env"
    kind = UNIVERSAL
    tracks_explicit = False

    def list_packages(self) -> list[Package]:
        if os.path.exists(PROFILE_MANIFEST):
            return parse_nix_profile(run(*NIX, "profile", "list", "--json"))
        return parse_nix_env(run("nix-env", "-q", "--json"))

    def remove_action(self, pkg: Package) -> Action:
        if pkg.extra.get("profile"):
            return Action([*NIX, "profile", "remove", pkg.ident],
                          pretty=f"nix profile remove {pkg.ident}")
        return Action(["nix-env", "-e", pkg.ident])
