"""Dialogs: confirm-before-remove, live command output, and exporting the list."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

from gi.repository import GLib, Gtk, Pango

from urpm.sources import Action, Package, Source, human_size
from urpm.sources.base import which

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
PKEXEC_CANCELLED, PKEXEC_DENIED = 126, 127


def label(text: str = "", *classes: str, **props) -> Gtk.Label:
    return Gtk.Label(label=text, css_classes=list(classes), **props)


class _Dialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, title: str, width: int = 520):
        super().__init__(transient_for=parent, modal=True, title=title,
                         css_classes=["urpm", "dialog-window"], default_width=width,
                         resizable=True)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            css_classes=["dialog-body"])
        self.buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=6)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.body)
        outer.append(self.buttons)
        outer.add_css_class("dialog-outer")
        self.set_child(outer)
        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                             Gtk.CallbackAction.new(self._on_escape)))
        self.add_controller(escape)

    def _on_escape(self, *_):
        self.close()
        return True

    def add_button(self, text: str, css: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=text, css_classes=[css])
        button.connect("clicked", lambda _b: callback())
        self.buttons.append(button)
        return button


class ConfirmRemoveDialog(_Dialog):
    """Shows exactly what will run and what else would go, before anything happens."""

    def __init__(self, parent, pkg: Package, source: Source, action: Action, on_confirm):
        super().__init__(parent, f"Remove {pkg.name}?")
        self._on_confirm = on_confirm

        top = Gtk.Box(spacing=14)
        top.append(label("🗑️", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(f"Remove {pkg.name}?", "placeholder-title", xalign=0, wrap=True))
        titles.append(label(f"{source.emoji} {source.label} · {pkg.version} · "
                            f"{human_size(pkg.size)}", "dim", xalign=0, wrap=True))
        top.append(titles)
        self.body.append(top)

        self.body.append(label("URPM will run:", "key", xalign=0))
        self.body.append(label(action.display(), "mono", "command", xalign=0, wrap=True,
                               selectable=True, wrap_mode=Pango.WrapMode.CHAR))
        if action.root:
            self.body.append(label("🔒 You'll be asked for your password.", "dim", xalign=0))

        self.sim_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.append(self.sim_box)

        cancel = self.add_button("Cancel", "soft", self.close)
        self.confirm = self.add_button("Remove", "danger", self._confirm)
        self.confirm.set_sensitive(False)
        self.set_focus(cancel)  # never start with the destructive button focused

        spinner_row = Gtk.Box(spacing=8)
        spinner_row.append(Gtk.Spinner(spinning=True))
        spinner_row.append(label("Checking what else would be removed…", "dim"))
        self.sim_box.append(spinner_row)

        def worker():
            try:
                result, error = source.simulate_remove(pkg), None
            except Exception as exc:
                result, error = None, str(exc)
            GLib.idle_add(self._on_simulated, pkg, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_simulated(self, pkg: Package, result, error) -> bool:
        while child := self.sim_box.get_first_child():
            self.sim_box.remove(child)
        if error:
            self.sim_box.append(label(f"⚠️ The package manager says: {error}", "warning",
                                      xalign=0, wrap=True))
        elif result is None:
            self.sim_box.append(label(
                "ℹ️ URPM can't preview what else this package manager will remove. "
                "Packages that depend on this one may be removed too.", "dim", xalign=0,
                wrap=True))
        others = [name for name in (result or []) if name.split(":")[0] != pkg.name.split(":")[0]]
        if others:
            self.sim_box.append(label(
                f"⚠️ This will also remove {len(others)} other package"
                f"{'s' if len(others) != 1 else ''}:", "warning", xalign=0, wrap=True))
            shown = ", ".join(others[:40]) + (f" …and {len(others) - 40} more"
                                              if len(others) > 40 else "")
            self.sim_box.append(label(shown, "mono", "dim", xalign=0, wrap=True, selectable=True))
            self.confirm.set_label(f"Remove {len(others) + 1} packages")
        self.confirm.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _confirm(self):
        self.close()
        self._on_confirm()


class CommandDialog(_Dialog):
    """Runs one or more Actions in order and streams their output.

    Stops at the first failure, so a failed update never leads into the next one.
    """

    def __init__(self, parent, title: str, actions, on_finished=None):
        actions = actions if isinstance(actions, list) else [actions]
        super().__init__(parent, title, width=640)
        self.set_default_size(640, 420)
        self._on_finished = on_finished
        self._finished = False

        head = Gtk.Box(spacing=10)
        self.spinner = Gtk.Spinner(spinning=True)
        head.append(self.spinner)
        self.status = label(title, "placeholder-title", xalign=0, hexpand=True, wrap=True)
        head.append(self.status)
        self.body.append(head)
        self.body.append(label("\n".join(a.display() for a in actions), "mono", "dim",
                               xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.CHAR))

        self.buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.buffer, editable=False, monospace=True,
                            cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            css_classes=["log"])
        self.scroller = Gtk.ScrolledWindow(child=view, vexpand=True, min_content_height=220,
                                           css_classes=["log-scroller"])
        self.body.set_vexpand(True)
        self.body.append(self.scroller)

        self.close_button = self.add_button("Close", "cute", self.close)
        self.close_button.set_sensitive(False)
        self.set_deletable(False)
        self.connect("close-request", lambda *_: not self._finished)

        threading.Thread(target=self._run_all, args=(actions,), daemon=True).start()

    def _on_escape(self, *_):
        if self._finished:
            self.close()
        return True

    def _run_all(self, actions: list[Action]) -> None:
        code = 0
        for action in actions:
            if len(actions) > 1:
                GLib.idle_add(self._append, f"$ {action.display()}\n")
            code = self._run(action)
            if code != 0:
                break
        GLib.idle_add(self._done, code)

    def _run(self, action: Action) -> int:
        try:
            proc = subprocess.Popen(action.full_argv(), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, errors="replace", bufsize=1)
        except OSError as exc:
            GLib.idle_add(self._append, f"{exc}\n")
            return -1
        with proc.stdout:
            for line in proc.stdout:
                GLib.idle_add(self._append, _ANSI_RE.sub("", line))
        return proc.wait()

    def _append(self, text: str) -> bool:
        self.buffer.insert(self.buffer.get_end_iter(), text)
        adj = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()) and False)
        return GLib.SOURCE_REMOVE

    def _done(self, code: int) -> bool:
        self._finished = True
        self.spinner.stop()
        self.spinner.set_visible(False)
        messages = {0: "✅ All done!", PKEXEC_CANCELLED: "🔒 Password prompt was cancelled",
                    PKEXEC_DENIED: "🔒 Not authorized"}
        self.status.set_label(messages.get(code, f"😿 Something went wrong (exit code {code})"))
        self.close_button.set_sensitive(True)
        self.set_deletable(True)
        self.close_button.grab_focus()
        if self._on_finished:
            self._on_finished(code == 0)
        return GLib.SOURCE_REMOVE


def can_run(action: Action) -> str | None:
    """Return a reason why the action can't run from the GUI, or None if it can."""
    if action.root and not which("pkexec"):
        return "pkexec (polkit) isn't installed, so URPM can't ask for your password"
    if not which(action.argv[0]) and not os.path.exists(action.argv[0]):
        return f"{action.argv[0]} isn't installed"
    return None


# ---------------------------------------------------------------- export

def choose_export_file(parent: Gtk.Window, callback) -> None:
    """Ask for a save location, then call callback(path). Works on GTK 4.0 through 4.x."""
    name = f"urpm-packages-{time.strftime('%Y-%m-%d')}.csv"
    if hasattr(Gtk, "FileDialog"):  # GTK 4.10+
        dialog = Gtk.FileDialog(title="Export package list", initial_name=name, modal=True)

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return  # cancelled
            if file and file.get_path():
                callback(file.get_path())

        dialog.save(parent, None, done)
        return

    chooser = Gtk.FileChooserNative(title="Export package list", transient_for=parent,
                                    action=Gtk.FileChooserAction.SAVE, modal=True)
    chooser.set_current_name(name)

    def response(dlg, code):
        if code == Gtk.ResponseType.ACCEPT and dlg.get_file():
            callback(dlg.get_file().get_path())
        dlg.destroy()

    chooser.connect("response", response)
    parent._export_chooser = chooser  # keep a reference until it closes
    chooser.show()


def launch(argv: list[str]) -> None:
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


__all__ = ["CommandDialog", "ConfirmRemoveDialog", "can_run", "choose_export_file", "launch"]
