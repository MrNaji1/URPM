"""Turn the background update check on and off.

With systemd (most desktops) it's a user timer that runs `urpm --check-updates` 15 minutes
after login and then every 6 hours. Without systemd (Void, Alpine…) it's an autostart
entry that checks once, 10 minutes after login. Nothing here needs root.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from urpm.sources.base import run, which

NAME = "urpm-update-check"
PACKAGE_DIR = Path(__file__).resolve().parent


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))


def unit_dir() -> Path:
    return config_home() / "systemd" / "user"


def autostart_file() -> Path:
    return config_home() / "autostart" / f"{NAME}.desktop"


def command() -> tuple[list[str], dict[str, str]]:
    """How to run `urpm --check-updates` with this same copy of URPM, plus environment."""
    launcher = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if launcher and launcher.name == "urpm" and launcher.is_file():
        return [str(launcher), "--check-updates"], {}
    found = which("urpm")
    if found:
        return [found, "--check-updates"], {}
    # Running from a source checkout: point Python at it.
    return ([sys.executable, "-m", "urpm", "--check-updates"],
            {"PYTHONPATH": str(PACKAGE_DIR.parent)})


def has_systemd() -> bool:
    if not which("systemctl"):
        return False
    try:
        state = run("systemctl", "--user", "is-system-running", ok_codes=(0, 1)).strip()
    except RuntimeError:
        return False
    return state in ("running", "degraded", "starting", "initializing")


def service_text(argv: list[str], env: dict[str, str]) -> str:
    lines = ["[Unit]", "Description=URPM: check for package updates", "",
             "[Service]", "Type=oneshot", f"ExecStart={shlex.join(argv)}"]
    lines += [f'Environment="{k}={v}"' for k, v in env.items()]
    lines += ["Nice=10", "IOSchedulingClass=idle", ""]
    return "\n".join(lines)


TIMER_TEXT = """[Unit]
Description=URPM: check for package updates every 6 hours

[Timer]
OnStartupSec=15min
OnUnitActiveSec=6h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
"""


def autostart_text(argv: list[str], env: dict[str, str]) -> str:
    env_part = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
    inner = f"sleep 600; exec {env_part}{shlex.join(argv)}"
    return ("[Desktop Entry]\nType=Application\nName=URPM update check\n"
            f"Exec=sh -c {shlex.quote(inner)}\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")


def enable() -> str:
    """Turn background checks on. Returns "systemd" or "autostart"."""
    argv, env = command()
    if has_systemd():
        unit_dir().mkdir(parents=True, exist_ok=True)
        (unit_dir() / f"{NAME}.service").write_text(service_text(argv, env))
        (unit_dir() / f"{NAME}.timer").write_text(TIMER_TEXT)
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "--now", f"{NAME}.timer")
        return "systemd"
    autostart_file().parent.mkdir(parents=True, exist_ok=True)
    autostart_file().write_text(autostart_text(argv, env))
    return "autostart"


def disable() -> None:
    timer = unit_dir() / f"{NAME}.timer"
    if timer.exists() and which("systemctl"):
        try:
            run("systemctl", "--user", "disable", "--now", f"{NAME}.timer")
        except RuntimeError:
            pass  # already stopped
    for path in (timer, unit_dir() / f"{NAME}.service", autostart_file()):
        path.unlink(missing_ok=True)
    if which("systemctl"):
        try:
            run("systemctl", "--user", "daemon-reload")
        except RuntimeError:
            pass


def is_enabled() -> bool:
    return (unit_dir() / f"{NAME}.timer").exists() or autostart_file().exists()
