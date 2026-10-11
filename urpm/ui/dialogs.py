"""Dialogs: confirm-before-remove, live command output, and exporting the list."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from urpm.i18n import _, ngettext  # noqa: E402
from urpm.sources import Action, Package, Source, human_size  # noqa: E402
from urpm.sources.base import UnsafeCommand, root_binary, which  # noqa: E402
from urpm.ui.widgets import label  # noqa: E402

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
PKEXEC_CANCELLED, PKEXEC_DENIED = 126, 127
CANCELLED = -15          # our own code for "the user pressed Cancel"
MAX_LOG_LINES = 4000     # output kept in the command window


class Dialog(Gtk.Window):
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

    def _on_escape(self, *_args):
        self.close()
        return True

    def add_button(self, text: str, css: str, callback) -> Gtk.Button:
        button = Gtk.Button(label=text, css_classes=[css])
        button.connect("clicked", lambda _b: callback())
        self.buttons.append(button)
        return button


class ConfirmRemoveDialog(Dialog):
    """Shows exactly what will run and what else would go, before anything happens."""

    def __init__(self, parent, pkg: Package, source: Source, action: Action, on_confirm):
        super().__init__(parent, _("Remove {name}?").format(name=pkg.name))
        self._on_confirm = on_confirm

        top = Gtk.Box(spacing=14)
        top.append(label("🗑️", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(_("Remove {name}?").format(name=pkg.name), "placeholder-title",
                            xalign=0, wrap=True))
        titles.append(label(f"{source.emoji} {source.label} · {pkg.version} · "
                            f"{human_size(pkg.size)}", "dim", xalign=0, wrap=True))
        top.append(titles)
        self.body.append(top)

        self.body.append(label(_("URPM will run:"), "key", xalign=0))
        self.body.append(label(action.display(), "mono", "command", xalign=0, wrap=True,
                               selectable=True, wrap_mode=Pango.WrapMode.CHAR))
        if action.root:
            self.body.append(label(_("🔒 You'll be asked for your password."), "dim", xalign=0))

        self.sim_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.body.append(self.sim_box)

        cancel = self.add_button(_("Cancel"), "soft", self.close)
        self.confirm = self.add_button(_("Remove"), "danger", self._confirm)
        self.confirm.set_sensitive(False)
        self.set_focus(cancel)  # never start with the destructive button focused

        spinner_row = Gtk.Box(spacing=8)
        spinner_row.append(Gtk.Spinner(spinning=True))
        spinner_row.append(label(_("Checking what else would be removed…"), "dim"))
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
            self.sim_box.append(label(_("⚠️ The package manager says: {error}").format(error=error),
                                      "warning",
                                      xalign=0, wrap=True))
        elif result is None:
            self.sim_box.append(label(
                _("ℹ️ URPM can't preview what else this package manager will remove. "
                  "Packages that depend on this one may be removed too."), "dim", xalign=0,
                wrap=True))
        others = [name for name in (result or []) if name.split(":")[0] != pkg.name.split(":")[0]]
        if others:
            self.sim_box.append(label(
                ngettext("⚠️ This will also remove {n} other package:",
                         "⚠️ This will also remove {n} other packages:", len(others))
                .format(n=len(others)), "warning", xalign=0, wrap=True))
            shown = ", ".join(others[:40]) + (" " + _("… and {n} more").format(
                n=len(others) - 40) if len(others) > 40 else "")
            self.sim_box.append(label(shown, "mono", "dim", xalign=0, wrap=True, selectable=True))
            self.confirm.set_label(ngettext("Remove {n} package", "Remove {n} packages",
                                            len(others) + 1).format(n=len(others) + 1))
        self.confirm.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _confirm(self):
        self.close()
        self._on_confirm()


class ConfirmLaunchDialog(Dialog):
    """Ask before running an untrusted file, showing exactly which file it is."""

    def __init__(self, parent, name: str, path: str, reason: str, on_confirm):
        super().__init__(parent, _("Open {name}?").format(name=name))
        self._on_confirm = on_confirm
        top = Gtk.Box(spacing=14)
        top.append(label("⚠️", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(_("Open {name}?").format(name=name), "placeholder-title", xalign=0,
                            wrap=True))
        titles.append(label(reason, "dim", xalign=0, wrap=True, max_width_chars=55))
        top.append(titles)
        self.body.append(top)
        self.body.append(label(_("File:"), "key", xalign=0))
        self.body.append(label(path, "mono", "command", xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.CHAR))
        self.body.append(label(_("Only open files you downloaded on purpose, from a source you "
                                 "trust."), "warning", xalign=0, wrap=True))
        cancel = self.add_button(_("Cancel"), "soft", self.close)
        self.confirm = self.add_button(_("Open anyway"), "danger", self._confirm)
        self.set_focus(cancel)  # never start with the risky button focused

    def _confirm(self):
        self.close()
        self._on_confirm()


class ConfirmCleanupDialog(Dialog):
    """Shows everything a cleanup will remove and the exact commands, before it runs."""

    MAX_SHOWN = 60

    def __init__(self, parent, item, on_confirm):
        super().__init__(parent, item.title, width=560)
        self._on_confirm = on_confirm
        top = Gtk.Box(spacing=14)
        top.append(label("🧹", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(item.title, "placeholder-title", xalign=0, wrap=True))
        size = _("Frees {size}").format(size=human_size(item.size)) + " · " if item.size else ""
        titles.append(label(f"{size}{item.source}", "dim", xalign=0, wrap=True))
        top.append(titles)
        self.body.append(top)

        removed = {"packages": _("These packages will be removed:"),
                   "data": _("These folders will be moved to the Trash:")}.get(
                       item.kind, _("These will be emptied:"))
        self.body.append(label(removed, "key", xalign=0))
        shown = item.items[:self.MAX_SHOWN]
        text = "\n".join(shown)
        if len(item.items) > self.MAX_SHOWN:
            text += "\n" + _("… and {n} more").format(n=len(item.items) - self.MAX_SHOWN)
        listing = label(text, "mono", "command", xalign=0, wrap=True, selectable=True,
                        wrap_mode=Pango.WrapMode.CHAR)
        self.body.append(Gtk.ScrolledWindow(child=listing, max_content_height=220,
                                            propagate_natural_height=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.body.append(label(_("URPM will run:"), "key", xalign=0))
        self.body.append(label("\n".join(a.display() for a in item.actions), "mono", "dim",
                               xalign=0, wrap=True, selectable=True,
                               wrap_mode=Pango.WrapMode.CHAR))
        if any(a.root for a in item.actions):
            self.body.append(label(_("🔒 You'll be asked for your password."), "dim", xalign=0))
        cancel = self.add_button(_("Cancel"), "soft", self.close)
        css = "cute" if item.kind == "cache" else "danger"
        self.confirm = self.add_button(_("Free up {size}").format(size=human_size(item.size))
                                       if item.size else _("Clean up"), css, self._confirm)
        self.set_focus(cancel)

    def _confirm(self):
        self.close()
        self._on_confirm()


class CommandDialog(Dialog):
    """Runs one or more Actions in order and streams their output.

    Stops at the first failure, so a failed update never leads into the next one.
    """

    def __init__(self, parent, title: str, actions, on_finished=None):
        actions = actions if isinstance(actions, list) else [actions]
        super().__init__(parent, title, width=640)
        self.set_default_size(640, 420)
        self._on_finished = on_finished
        self._finished = False
        self._cancelled = False
        self._proc: subprocess.Popen | None = None
        self._pending: list[str] = []      # output waiting to be shown
        self._lock = threading.Lock()

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

        self.cancel_button = self.add_button(_("Cancel"), "soft", self.cancel)
        self.close_button = self.add_button(_("Close"), "cute", self.close)
        self.close_button.set_sensitive(False)
        self.set_focus(self.cancel_button)  # not the command text, which would get selected
        self.set_deletable(False)
        self.connect("close-request", lambda *_args: not self._finished)

        # Output is shown in batches, 10 times a second, instead of once per line.
        self._flush_source = GLib.timeout_add(100, self._flush)
        threading.Thread(target=self._run_all, args=(actions,), daemon=True).start()

    def _on_escape(self, *_args):
        if self._finished:
            self.close()
        return True

    # ---- running (worker thread)

    def _run_all(self, actions: list[Action]) -> None:
        code = 0
        for action in actions:
            if self._cancelled:
                code = CANCELLED
                break
            if len(actions) > 1:
                self._write(f"$ {action.display()}\n")
            code = self._run(action)
            if code != 0:
                break
        if self._cancelled and code != 0:
            code = CANCELLED
        GLib.idle_add(self._done, code)

    def _run(self, action: Action) -> int:
        try:
            argv = action.full_argv()
            # Its own process group, so Cancel can stop the helpers it starts too
            # (apt runs dpkg, npm runs node, `sh -c` runs whatever it was given).
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, errors="replace",
                                    bufsize=1, start_new_session=True)
        except (OSError, UnsafeCommand) as exc:
            self._write(f"{exc}\n")
            return -1
        self._proc = proc
        with proc.stdout:
            for line in proc.stdout:
                self._write(_ANSI_RE.sub("", line))
        code = proc.wait()
        self._proc = None
        return code

    def _write(self, text: str) -> None:
        with self._lock:
            self._pending.append(text)

    # ---- UI (main thread)

    def _flush(self) -> bool:
        with self._lock:
            text, self._pending = "".join(self._pending), []
        if text:
            self.buffer.insert(self.buffer.get_end_iter(), text)
            extra = self.buffer.get_line_count() - MAX_LOG_LINES
            if extra > 0:  # keep memory bounded on very chatty commands
                _found, cut = self.buffer.get_iter_at_line(extra)
                self.buffer.delete(self.buffer.get_start_iter(), cut)
            adj = self.scroller.get_vadjustment()
            GLib.idle_add(lambda: adj.set_value(adj.get_upper()) and False)
        return GLib.SOURCE_CONTINUE if not self._finished else GLib.SOURCE_REMOVE

    def cancel(self) -> None:
        if self._finished or self._cancelled:
            return
        self._cancelled = True
        self.cancel_button.set_sensitive(False)
        proc = self._proc
        try:
            if proc is not None:
                os.killpg(proc.pid, signal.SIGTERM)
                GLib.timeout_add_seconds(5, self._force_stop, proc)
            self.status.set_label(_("Stopping…"))
        except PermissionError:
            # Running as administrator: our user can't stop it, and interrupting a package
            # manager halfway could leave things broken anyway.
            self.status.set_label(_("⏳ This step runs as administrator and will finish on its "
                                    "own. URPM will skip anything after it."))

    @staticmethod
    def _force_stop(proc: subprocess.Popen) -> bool:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
        return GLib.SOURCE_REMOVE

    def _done(self, code: int) -> bool:
        self._flush()
        self._finished = True
        if self._flush_source:
            GLib.source_remove(self._flush_source)
            self._flush_source = 0
        self.spinner.stop()
        self.spinner.set_visible(False)
        messages = {0: _("✅ All done!"), PKEXEC_CANCELLED: _("🔒 Password prompt was cancelled"),
                    PKEXEC_DENIED: _("🔒 Not authorized"), CANCELLED: _("✋ Cancelled")}
        self.status.set_label(messages.get(code, _("😿 Something went wrong (exit code {code})")
                                           .format(code=code)))
        self.cancel_button.set_visible(False)
        self.close_button.set_sensitive(True)
        self.set_deletable(True)
        self.close_button.grab_focus()
        if self._on_finished:
            self._on_finished(code == 0)
        return GLib.SOURCE_REMOVE


def can_run(action: Action) -> str | None:
    """Return a reason why the action can't run from the GUI, or None if it can."""
    if action.root:
        if not root_binary("pkexec"):
            return _("pkexec (polkit) isn't installed, so URPM can't ask for your password")
        try:
            action.full_argv()
        except UnsafeCommand as exc:
            return str(exc)
        return None
    if not which(action.argv[0]) and not os.path.exists(action.argv[0]):
        return _("{program} isn't installed").format(program=action.argv[0])
    return None


# ---------------------------------------------------------------- export

def choose_file(parent: Gtk.Window, title: str, callback, save_name: str | None = None,
                patterns: tuple[str, ...] = ()) -> None:
    """Ask for a file to save (save_name given) or open, then call callback(path).

    Works on GTK 4.0 through 4.x.
    """
    file_filter = None
    if patterns:
        file_filter = Gtk.FileFilter()
        file_filter.set_name(", ".join(patterns))
        for pattern in patterns:
            file_filter.add_pattern(pattern)
    if hasattr(Gtk, "FileDialog"):  # GTK 4.10+
        props = {"title": title, "modal": True}
        if save_name:
            props["initial_name"] = save_name
        if file_filter:
            props["default_filter"] = file_filter
        dialog = Gtk.FileDialog(**props)

        def done(dlg, result):
            try:
                file = dlg.save_finish(result) if save_name else dlg.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if file and file.get_path():
                callback(file.get_path())

        if save_name:
            dialog.save(parent, None, done)
        else:
            dialog.open(parent, None, done)
        return

    action = Gtk.FileChooserAction.SAVE if save_name else Gtk.FileChooserAction.OPEN
    chooser = Gtk.FileChooserNative(title=title, transient_for=parent, action=action, modal=True)
    if save_name:
        chooser.set_current_name(save_name)
    if file_filter:
        chooser.add_filter(file_filter)

    def response(dlg, code):
        if code == Gtk.ResponseType.ACCEPT and dlg.get_file():
            callback(dlg.get_file().get_path())
        dlg.destroy()

    chooser.connect("response", response)
    parent._file_chooser = chooser  # keep a reference until it closes
    chooser.show()


def choose_export_file(parent: Gtk.Window, callback) -> None:
    choose_file(parent, _("Export package list"), callback,
                save_name=f"urpm-packages-{time.strftime('%Y-%m-%d')}.csv")


def launch(argv: list[str]) -> None:
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


__all__ = ["CommandDialog", "ConfirmCleanupDialog", "ConfirmLaunchDialog",
           "ConfirmRemoveDialog", "can_run", "choose_export_file", "choose_file", "launch"]
