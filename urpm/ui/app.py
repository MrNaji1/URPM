"""Gtk.Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from urpm import APP_ID, __version__  # noqa: E402
from urpm.ui.theme import Theme  # noqa: E402
from urpm.ui.window import UrpmWindow  # noqa: E402

ICON_DIR = Path(__file__).resolve().parents[1] / "data" / "icons"


class UrpmApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", 0))
        self.theme = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        display = Gdk.Display.get_default()
        # Makes the icon work even when URPM isn't installed system-wide.
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICON_DIR))
        Gtk.Window.set_default_icon_name("urpm")
        self.theme = Theme(display)

        for name, callback in (("about", self._on_about), ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("win.refresh", ["<Ctrl>r", "F5"])
        self.set_accels_for_action("win.search", ["<Ctrl>f"])
        self.set_accels_for_action("win.check-updates", ["<Ctrl>u"])
        self.set_accels_for_action("win.export", ["<Ctrl>e"])

    def do_activate(self):
        window = self.props.active_window or UrpmWindow(self)
        window.present()

    def _on_about(self, *_):
        Gtk.AboutDialog(
            transient_for=self.props.active_window,
            modal=True,
            program_name="URPM",
            logo_icon_name="urpm",
            version=__version__,
            comments="A cute little window into every package on your Linux machine.",
            website="https://github.com/MrNaji1/URPM",
            license_type=Gtk.License.MIT_X11,
        ).present()


HELP = f"""URPM {__version__} — see every package installed on your computer

usage: urpm [options]

  --version             print the version and exit
  --install-desktop     add URPM to your app menu (for pip / pipx installs)
  --uninstall-desktop   remove it from your app menu again
  -h, --help            show this help
"""


def desktop_integration(install: bool) -> None:
    """Write (or remove) the .desktop entry and icon under ~/.local/share."""
    import shutil

    data_dir = Path(GLib.get_user_data_dir())
    desktop = data_dir / "applications" / f"{APP_ID}.desktop"
    icon = data_dir / "icons" / "hicolor" / "scalable" / "apps" / "urpm.svg"
    if not install:
        for path in (desktop, icon):
            path.unlink(missing_ok=True)
        print("URPM removed from your app menu.")
        return
    source = Path(__file__).resolve().parents[1] / "data"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    # Point at the urpm that is running right now (e.g. inside a pipx venv), not
    # whichever one happens to come first on $PATH.
    script = Path(sys.argv[0]).resolve()
    exe = str(script) if script.name == "urpm" and script.is_file() else f"{sys.executable} -m urpm"
    entry = (source / f"{APP_ID}.desktop").read_text()
    desktop.write_text(entry.replace("Exec=urpm", f"Exec={exe}"))
    shutil.copy(source / "icons" / "urpm.svg", icon)
    print(f"✨ Added URPM to your app menu ({desktop})")


def main(argv) -> int:
    args = argv[1:]
    if "-h" in args or "--help" in args:
        print(HELP, end="")
        return 0
    if "--version" in args:
        print(f"URPM {__version__}")
        return 0
    if "--install-desktop" in args or "--uninstall-desktop" in args:
        desktop_integration("--install-desktop" in args)
        return 0
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("URPM")
    return UrpmApp().run(argv)


def run() -> None:
    """Console-script entry point (see pyproject.toml)."""
    sys.exit(main(sys.argv))
