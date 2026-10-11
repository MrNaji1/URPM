"""`urpm --check-updates`: check for updates quietly and notify about new ones.

This runs from a timer (see urpm.schedule), not inside the window. It never opens a
window, never loads GTK, and only sends a notification when updates appear that it hasn't
already told you about, so it doesn't nag every few hours about the same ones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from urpm.sources import Source, available_sources
from urpm.sources.base import Package

APP_ID = "io.github.urpm"
ICON = Path(__file__).resolve().parent / "data" / "icons" / "urpm.svg"
MAX_NAMES = 4


def state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "urpm" / "notified.json"


def collect(sources: list[Source] | None = None) -> list[tuple[Source, Package]]:
    """Every package with an update, except held ones. Broken sources are skipped."""
    found = []
    for src in sources if sources is not None else available_sources():
        try:
            packages = src.list_packages()
            src.check_updates(packages)
        except Exception:  # offline, locked database… try again next time
            continue
        found.extend((src, pkg) for pkg in packages if pkg.update and not pkg.held)
    return found


def key(src: Source, pkg: Package) -> str:
    return f"{src.id}:{pkg.name}:{pkg.update}"


def message(updates: list[tuple[Source, Package]]) -> tuple[str, str]:
    """('5 updates available (2 security)', 'Firefox, Discord, Telegram and 2 more. …')."""
    count = len(updates)
    security = sum(1 for _, pkg in updates if pkg.security)
    summary = f"{count} update{'s' if count != 1 else ''} available"
    if security:
        summary += f" ({security} security)"
    # Apps you installed come first (their names mean something), security fixes next.
    ordered = sorted(updates, key=lambda u: (not u[1].explicit, not u[1].security, u[1].name))
    names = [pkg.name for _, pkg in ordered[:MAX_NAMES]]
    listed = ", ".join(names)
    if count > MAX_NAMES:
        listed += f" and {count - MAX_NAMES} more"
    return summary, f"{listed}. Open URPM to update."


def send(summary: str, body: str, urgent: bool = False) -> bool:
    """Show a desktop notification through the standard D-Bus service."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = {"desktop-entry": GLib.Variant("s", APP_ID),
                 "urgency": GLib.Variant("y", 2 if urgent else 1)}
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("URPM", 0, str(ICON), summary, body, [],
                                                       hints, -1)),
                      GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 5000, None)
        return True
    except Exception:
        notify_send = shutil.which("notify-send")
        if not notify_send:
            return False
        args = [notify_send, "--app-name=URPM", f"--icon={ICON}", summary, body]
        if urgent:
            args.insert(1, "--urgency=critical")
        return subprocess.run(args, check=False, timeout=10).returncode == 0


def check(sources: list[Source] | None = None) -> str | None:
    """Check once; returns the notification summary if one was sent."""
    updates = collect(sources)
    current = {key(src, pkg) for src, pkg in updates}
    path = state_file()
    try:
        previous = set(json.loads(path.read_text()))
    except (OSError, ValueError):
        previous = set()
    fresh = [(src, pkg) for src, pkg in updates if key(src, pkg) not in previous]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(current)))
    if not fresh:
        return None
    summary, body = message(updates)
    send(summary, body, urgent=any(pkg.security for _, pkg in fresh))
    return summary


def main() -> int:
    check()
    return 0
