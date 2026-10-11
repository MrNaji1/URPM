"""All the package managers URPM knows how to read.

To teach URPM a new one, subclass ``Source`` and add it to ``ALL_SOURCES``.
"""

from urpm.i18n import N_

from .apk import ApkSource
from .appimage import AppImageSource
from .base import DEV, SYSTEM, UNIVERSAL, Action, Cleanup, Details, Package, Source, human_size
from .brew import BrewSource
from .cargo import CargoSource
from .dpkg import DpkgSource
from .eopkg import EopkgSource
from .flatpak import FlatpakSource
from .nix import NixSource
from .npm import NpmSource
from .pacman import PacmanSource
from .pip import PipSource
from .pipx import PipxSource
from .portage import PortageSource
from .rpm import RpmSource
from .snap import SnapSource
from .xbps import XbpsSource

ALL_SOURCES: list[Source] = [
    # distro package managers
    DpkgSource(), RpmSource(), PacmanSource(), XbpsSource(), ApkSource(), PortageSource(),
    EopkgSource(),
    # universal / sandboxed formats
    FlatpakSource(), SnapSource(), AppImageSource(), NixSource(), BrewSource(),
    # language tooling
    PipSource(), PipxSource(), NpmSource(), CargoSource(),
]

# Sidebar headings, translated where they are shown.
KIND_LABELS = {SYSTEM: N_("SYSTEM"), UNIVERSAL: N_("APPS"), DEV: N_("DEVELOPER")}


def available_sources() -> list[Source]:
    return [src for src in ALL_SOURCES if src.available()]


__all__ = ["ALL_SOURCES", "KIND_LABELS", "Action", "Cleanup", "Details", "Package", "Source",
           "available_sources", "human_size", "DEV", "SYSTEM", "UNIVERSAL"]
