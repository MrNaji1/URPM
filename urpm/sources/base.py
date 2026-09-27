"""Shared building blocks for package sources.

Everything in ``urpm.sources`` is plain Python with no GTK imports, so the
parsers can be tested (and reused) without a display.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

# Source kinds, used to group the sidebar.
SYSTEM, UNIVERSAL, DEV = "system", "universal", "dev"


@dataclass
class Package:
    name: str
    version: str
    source: str                     # id of the Source it came from, e.g. "apt"
    summary: str = ""
    size: int = 0                   # bytes on disk, 0 when unknown
    section: str = ""
    explicit: bool = True           # installed on purpose, not pulled in as a dependency
    installed: float | None = None  # unix timestamp of install / last update
    homepage: str = ""
    ident: str = ""                 # what the package manager calls it (flatpak ref, path, ...)
    extra: dict[str, str] = field(default_factory=dict)  # source-specific bits
    update: str = ""                # newer version available, filled in by check_updates()

    def __post_init__(self):
        if not self.ident:
            self.ident = self.name


@dataclass
class Details:
    description: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Action:
    """A command URPM can run for the user, optionally as root via pkexec."""

    argv: list[str]
    root: bool = False
    env: dict[str, str] = field(default_factory=dict)
    pretty: str = ""  # friendlier version for copy/paste, e.g. "sudo apt remove foo"

    def display(self) -> str:
        """The command as the user would type it in a terminal."""
        if self.pretty:
            return self.pretty
        prefix = "sudo " if self.root else ""
        env = "".join(f"{k}={shlex.quote(v)} " for k, v in self.env.items())
        return prefix + env + shlex.join(self.argv)

    def full_argv(self) -> list[str]:
        if self.root:
            # Never run something from a user-writable folder as root.
            exe = shutil.which(self.argv[0], path=SYSTEM_PATH) or self.argv[0]
        else:
            exe = which(self.argv[0]) or self.argv[0]
        argv = [exe, *self.argv[1:]]
        env = [f"{k}={v}" for k, v in self.env.items()]
        if self.root:
            # pkexec wipes the environment, so variables go through `env`.
            return ["pkexec", "env", *env, *argv] if env else ["pkexec", *argv]
        return argv


class Source:
    """One package manager. Subclasses fill in the class attributes and methods."""

    id = ""
    label = ""
    emoji = ""
    binary = ""
    kind = SYSTEM
    # False when the source can't tell explicit installs from dependencies.
    tracks_explicit = True

    def available(self) -> bool:
        return which(self.binary) is not None

    def list_packages(self) -> list[Package]:
        raise NotImplementedError

    def details(self, pkg: Package) -> Details:
        return Details()

    def files(self, pkg: Package) -> list[str] | None:
        """Files owned by the package, or None if the source can't tell."""
        return None

    def remove_action(self, pkg: Package) -> Action | None:
        return None

    def simulate_remove(self, pkg: Package) -> list[str] | None:
        """Everything that would be removed along with pkg, or None if unknown."""
        return None

    def update_actions(self, pkgs: list[Package]) -> list[Action]:
        """Commands that update all of pkgs at once (usually one per source)."""
        return []

    def update_action(self, pkg: Package) -> Action | None:
        actions = self.update_actions([pkg]) if pkg.update else []
        return actions[0] if len(actions) == 1 else None

    def check_updates(self, packages: list[Package]) -> int:
        """Set ``pkg.update`` on outdated packages; return how many were found."""
        return 0

    def launch_argv(self, pkg: Package) -> list[str] | None:
        return None

    def remove_command(self, pkg: Package) -> str:
        action = self.remove_action(pkg)
        return action.display() if action else ""


SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EXTRA_PATHS = ("~/.local/bin", "~/.cargo/bin", "~/.nix-profile/bin", "/nix/var/nix/profiles/default/bin",
               "/home/linuxbrew/.linuxbrew/bin", "~/.linuxbrew/bin", "/usr/sbin", "/sbin")


def which(binary: str) -> str | None:
    """shutil.which, plus a few places GUI sessions often leave off $PATH."""
    if not binary:
        return None
    found = shutil.which(binary)
    if found:
        return found
    for folder in EXTRA_PATHS:
        path = os.path.join(os.path.expanduser(folder), binary)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def run(*args: str, timeout: int = 120, ok_codes: tuple[int, ...] = (0,)) -> str:
    """Run a command with a predictable locale and return its stdout."""
    env = {**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
    exe = which(args[0]) or args[0]
    try:
        result = subprocess.run(
            [exe, *args[1:]], capture_output=True, text=True, env=env,
            timeout=timeout, check=False, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{args[0]}: not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{args[0]}: timed out after {timeout}s") from exc
    if result.returncode not in ok_codes:
        lines = result.stderr.strip().splitlines()
        raise RuntimeError(f"{args[0]}: {lines[-1] if lines else f'exited with {result.returncode}'}")
    return result.stdout


def can_write(path: str) -> bool:
    return os.access(path, os.W_OK)


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def mtime(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


_UNITS = {
    "b": 1, "byte": 1, "bytes": 1,
    "k": 1024, "m": 1024**2, "g": 1024**3,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
    "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
}
# Tolerates "1.2 GB", "1,2 GB", "1.2 GB" and flatpak's "1.2?GB" in the C locale.
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)[^\dA-Za-z]*([A-Za-z]+)")


def parse_size(text: str) -> int:
    """Turn a human size like '511.1 MB' or '12.4 MiB' into bytes (0 if unparseable)."""
    match = _SIZE_RE.search(text or "")
    if not match:
        return 0
    number = float(match.group(1).replace(",", "."))
    factor = _UNITS.get(match.group(2).lower())
    return int(number * factor) if factor else 0


def parse_fields(text: str, indented_keys: bool = False) -> dict[str, str]:
    """Parse 'Key: value' records where indented lines continue the previous value.

    Works for `dpkg-query -s`, `pacman -Qi`, `rpm -qi` and `snap info`. Keys may be
    padded before the colon (``Name            : foo``). With ``indented_keys``
    (`flatpak info` right-aligns its keys) every line containing a colon is a key.
    """
    fields: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            continue
        continues = ":" not in line or (not indented_keys and line.startswith((" ", "\t")))
        if key and continues:
            fields[key] += "\n" + line.strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        fields[key] = value.strip()
    return fields


_NAME_VERSION_RE = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*(?:-r?\d+)?)$")


def split_name_version(text: str) -> tuple[str, str]:
    """'hello-2.12' -> ('hello', '2.12'); 'python3-pip-24.0-r1' -> ('python3-pip', '24.0-r1')."""
    match = _NAME_VERSION_RE.match(text)
    return (match["name"], match["version"]) if match else (text, "")


def desktop_launcher(files: list[str]) -> list[str] | None:
    """If a package ships a visible .desktop entry, return a command that opens it."""
    launcher = which("gtk-launch") or which("gtk4-launch")
    if not launcher:
        return None
    for path in files:
        if "/share/applications/" in path and path.endswith(".desktop"):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    entry = fh.read()
            except OSError:
                continue
            if "NoDisplay=true" not in entry and "Type=Application" in entry:
                return [launcher, os.path.basename(path)]
    return None


def apply_updates(packages: list[Package], updates: dict[str, str], key=lambda p: p.name,
                  same_version_ok: bool = False) -> int:
    """Mark packages whose key appears in ``updates`` (key -> newer version).

    ``same_version_ok`` is for sources like Flatpak that ship new builds of the
    same version number.
    """
    found = 0
    for pkg in packages:
        new = updates.get(key(pkg))
        if not new or (new == pkg.version and not same_version_ok):
            continue
        pkg.update = new if new != pkg.version else "new build"
        found += 1
    return found


_VERSION_PART_RE = re.compile(r"(\d+)")


def version_key(version: str) -> tuple:
    """Natural ordering, so 1.10 sorts after 1.9 and 10.0 after 9.0."""
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower())
                 for part in _VERSION_PART_RE.split(version) if part)


def human_size(num: int) -> str:
    if num <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1000:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1000
    return f"{num:.1f} TB"
