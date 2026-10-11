"""Move to a new computer: save your apps here, install them there (see urpm.migrate)."""

from __future__ import annotations

import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from urpm import migrate  # noqa: E402
from urpm.i18n import _, ngettext  # noqa: E402
from urpm.sources import available_sources  # noqa: E402
from urpm.sources.base import UnsafeCommand  # noqa: E402
from urpm.ui.dialogs import Dialog, choose_file  # noqa: E402
from urpm.ui.widgets import label  # noqa: E402


def script_path(path: str) -> str:
    return (path[:-5] if path.endswith(".json") else path) + ".sh"


def save_snapshot(snap: dict, path: str) -> str:
    """Write the app list and its reinstall script. Returns the script's path."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(migrate.dumps(snap))
    sh = script_path(path)
    with open(sh, "w", encoding="utf-8") as fh:
        fh.write(migrate.script(snap, os.path.basename(sh)))
    os.chmod(sh, 0o755)
    return sh


def count_line(snap: dict) -> str:
    counts: dict[str, int] = {}
    for app in snap["apps"]:
        src = migrate.source_by_id(app["source"])
        name = src.label if src else app["source"]
        counts[name] = counts.get(name, 0) + 1
    return " · ".join(f"{name} {n}" for name, n in counts.items())


class MoveDialog(Dialog):
    """Save the apps you installed, or open a list saved on another computer."""

    def __init__(self, parent, snap: dict, installed: dict, on_install, on_message):
        super().__init__(parent, _("Move to a new computer"), width=560)
        self._snap = snap
        self._installed = installed
        self._on_install = on_install
        self._on_message = on_message

        top = Gtk.Box(spacing=14)
        top.append(label("🚚", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(_("Move to a new computer"), "placeholder-title", xalign=0,
                            wrap=True))
        titles.append(label(_("Save a list of your apps here, then open it with URPM on your "
                              "new computer to install them all again."), "dim", xalign=0,
                            wrap=True, max_width_chars=52))
        top.append(titles)
        self.body.append(top)

        save = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["toggle-card"])
        count = len(snap["apps"])
        save.append(label(ngettext("💖 {n} app you installed", "💖 {n} apps you installed",
                                   count).format(n=count), "title", xalign=0))
        save.append(label(count_line(snap), "dim", "small", xalign=0, wrap=True))
        save.append(label(_("You also get a script that installs them on computers without "
                            "URPM."), "dim", "small", xalign=0, wrap=True, max_width_chars=60))
        self.save_button = Gtk.Button(label=_("💾 Save my apps…"), css_classes=["cute"],
                                      halign=Gtk.Align.START, margin_top=4,
                                      sensitive=count > 0)
        self.save_button.connect("clicked", lambda _b: self._choose_save())
        save.append(self.save_button)
        self.body.append(save)

        restore = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          css_classes=["toggle-card"])
        restore.append(label(_("📂 Install apps from another computer"), "title", xalign=0))
        restore.append(label(_("Open a list you saved with URPM. You'll see what's missing "
                               "here before anything is installed."), "dim", "small", xalign=0,
                             wrap=True, max_width_chars=60))
        self.open_button = Gtk.Button(label=_("Open a saved list…"), css_classes=["soft"],
                                      halign=Gtk.Align.START, margin_top=4)
        self.open_button.connect("clicked", lambda _b: self._choose_open())
        restore.append(self.open_button)
        self.body.append(restore)
        self.status = label("", "dim", xalign=0, wrap=True, visible=False)
        self.body.append(self.status)

        self.add_button(_("Close"), "soft", self.close)

    # ---- saving

    def _choose_save(self) -> None:
        name = f"my-apps-{time.strftime('%Y-%m-%d')}.json"
        choose_file(self, _("Save my apps"), self.save_to, save_name=name)

    def save_to(self, path: str) -> None:
        try:
            sh = save_snapshot(self._snap, path)
        except OSError as exc:
            self._show(_("😿 Couldn't save: {error}").format(error=exc.strerror or exc))
            return
        self._show(_("💾 Saved {list} and {script}. Copy both to your new computer.").format(
            list=os.path.basename(path), script=os.path.basename(sh)))

    # ---- opening

    def _choose_open(self) -> None:
        choose_file(self, _("Open a saved list"), self.open_file, patterns=("*.json",))

    def open_file(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                snap = migrate.load(fh.read())
        except (OSError, ValueError) as exc:
            self._show(f"😿 {exc}")
            return
        self.open_button.set_sensitive(False)
        self._show(_("Checking what's missing on this computer…"))
        installed = self._installed

        def worker():
            plan = migrate.plan(snap, available_sources(), installed)
            GLib.idle_add(self._planned, path, snap, plan)

        threading.Thread(target=worker, daemon=True).start()

    def _planned(self, path: str, snap: dict, plan) -> bool:
        self.open_button.set_sensitive(True)
        self.status.set_visible(False)
        self.restore_dialog = RestoreDialog(self.get_transient_for(), os.path.basename(path),
                                            snap, plan, self._on_install)
        self.restore_dialog.present()
        self.close()
        return GLib.SOURCE_REMOVE

    def _show(self, text: str) -> None:
        self.status.set_label(text)
        self.status.set_visible(True)


class RestoreDialog(Dialog):
    """What a saved list would install here, by package manager, before anything runs."""

    def __init__(self, parent, filename: str, snap: dict, plan, on_install):
        super().__init__(parent, _("Install apps from {file}").format(file=filename), width=600)
        self._plan = plan
        self._on_install = on_install
        self.checks: dict[str, Gtk.CheckButton] = {}

        top = Gtk.Box(spacing=14)
        top.append(label("📦", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        titles.append(label(_("Install apps from {file}").format(file=filename),
                            "placeholder-title", xalign=0, wrap=True))
        system = snap.get("system", {}).get("pretty_name")
        parts = [ngettext("{n} app", "{n} apps", len(snap["apps"])).format(n=len(snap["apps"])),
                 ngettext("{n} already here", "{n} already here", plan.present).format(
                     n=plan.present)]
        if system:
            parts.insert(0, _("Saved on {system}").format(system=system))
        titles.append(label(" · ".join(parts), "dim", xalign=0, wrap=True))
        top.append(titles)
        self.body.append(top)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for group in plan.groups:
            if group.missing:
                check = Gtk.CheckButton(active=True, label=ngettext(
                    "{emoji} {source}: {n} app to install", "{emoji} {source}: {n} apps to install",
                    len(group.missing)).format(emoji=group.source.emoji,
                                               source=group.source.label,
                                               n=len(group.missing)))
                check.connect("toggled", lambda *_args: self._sync())
                self.checks[group.source.id] = check
                content.append(check)
                content.append(self._names([p.name for p in group.missing]))
            if group.not_found:
                content.append(label(_("⚠️ Not found in this computer's {source} sources. They "
                                       "probably came from an extra repository that has to be "
                                       "added first:").format(source=group.source.label),
                                     "warning", xalign=0, wrap=True, max_width_chars=64))
                content.append(self._names([p.name for p in group.not_found]))
        for source, names in plan.unavailable.items():
            content.append(label(_("{source} isn't on this computer, so these are skipped:")
                                 .format(source=source), "dim", xalign=0, wrap=True))
            content.append(self._names(names))
        for source, names in plan.manual.items():
            content.append(label(_("Install these {source} apps yourself:").format(source=source),
                                 "dim", xalign=0, wrap=True))
            content.append(self._names(names))
        if not plan.missing:
            content.append(label("🎉 " + _("Everything URPM can install from this list is "
                                           "already here."), "placeholder-title", xalign=0,
                                 wrap=True))
        self.body.append(Gtk.ScrolledWindow(child=content, max_content_height=380,
                                            propagate_natural_height=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.password_note = label(_("🔒 You'll be asked for your password."), "dim", xalign=0,
                                   visible=False)
        self.body.append(self.password_note)

        cancel = self.add_button(_("Cancel") if plan.missing else _("Close"), "soft", self.close)
        self.install_button = self.add_button(_("Install"), "cute", self._install)
        self.install_button.set_visible(bool(plan.missing))
        self.set_focus(cancel)
        self._sync()

    @staticmethod
    def _names(names: list[str]) -> Gtk.Widget:
        return label(", ".join(names), "mono", "dim", xalign=0, wrap=True, selectable=True,
                     margin_start=28, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=70)

    def selected_groups(self):
        return [g for g in self._plan.groups
                if g.missing and self.checks.get(g.source.id) and
                self.checks[g.source.id].get_active()]

    def _sync(self) -> None:
        groups = self.selected_groups()
        count = sum(len(g.missing) for g in groups)
        self.install_button.set_label(ngettext("Install {n} app", "Install {n} apps",
                                               count).format(n=count))
        self.install_button.set_sensitive(count > 0)
        root = False
        for group in groups:
            try:
                root = root or any(a.root for a in group.source.install_actions(group.missing))
            except Exception:
                pass
        self.password_note.set_visible(root)

    def _install(self) -> None:
        groups = self.selected_groups()
        try:
            actions = [a for g in groups for a in g.source.safe_install_actions(g.missing)]
        except UnsafeCommand as exc:
            self.password_note.set_label(f"🛑 {exc}")
            self.password_note.set_visible(True)
            return
        count = sum(len(g.missing) for g in groups)
        self.close()
        self._on_install(actions, count)
