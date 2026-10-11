"""The sidebar: library views, one row per package manager, and the "Just mine" switch."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from urpm.i18n import _, ngettext  # noqa: E402
from urpm.sources import KIND_LABELS, Source  # noqa: E402
from urpm.ui.widgets import clear, label  # noqa: E402

ALL, UPDATES, RECENT = "all", "updates", "recent"
CLEANUP, DUPLICATES, HISTORY = "cleanup", "duplicates", "history"
TOOL_VIEWS = (CLEANUP, DUPLICATES, HISTORY)  # pages that aren't package lists
RECENT_DAYS = 30


def recent_cutoff() -> float:
    return time.time() - RECENT_DAYS * 86400


class Sidebar(Gtk.Box):
    def __init__(self, on_view_selected, on_explicit_toggled):
        # hexpand=False is set explicitly: rows contain expanding labels, and without it
        # GTK would hand the sidebar spare width whenever the main area is narrow.
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         css_classes=["sidebar"], width_request=236, hexpand=False)
        self.view = ALL
        self._on_view_selected = on_view_selected
        self._rebuilding = False

        self.source_list = Gtk.ListBox(css_classes=["source-list"],
                                       selection_mode=Gtk.SelectionMode.SINGLE)
        self.source_list.connect("row-selected", self._row_selected)
        self.append(Gtk.ScrolledWindow(child=self.source_list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       css_classes=["toggle-card"], margin_top=8)
        row = Gtk.Box(spacing=10)
        row.append(label(_("Just mine 💖"), "title", xalign=0, hexpand=True))
        self.explicit_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.explicit_switch.connect("notify::active",
                                     lambda switch, _p: on_explicit_toggled(switch.get_active()))
        row.append(self.explicit_switch)
        card.append(row)
        card.append(label(_("Hide packages that only came along as dependencies"),
                          "dim", "small", xalign=0, wrap=True, max_width_chars=24))
        self.append(card)

    @property
    def only_explicit(self) -> bool:
        return self.explicit_switch.get_active()

    def populate(self, items, sources: list[Source], errors: dict[str, str],
                 tools: dict[str, str] | None = None) -> None:
        """Rebuild the rows. Counts follow "Just mine", so they match what you'll see.

        `tools` gives the badge text for the Cleanup and Duplicates rows."""
        tools = tools or {}
        self._rebuilding = True
        clear(self.source_list)
        shown = [i for i in items if i.pkg.explicit or not self.only_explicit]
        cutoff = recent_cutoff()
        per_source: dict[str, int] = {}
        for item in shown:
            per_source[item.source.id] = per_source.get(item.source.id, 0) + 1

        self._heading(_("LIBRARY"))
        rows = [self._row(ALL, "🌈", _("Everything"), len(shown)),
                self._row(UPDATES, "✨", _("Updates"), sum(1 for i in shown if i.pkg.update)),
                self._row(RECENT, "🕒", ngettext("Last {n} day", "Last {n} days",
                                                 RECENT_DAYS).format(n=RECENT_DAYS),
                          sum(1 for i in shown if (i.pkg.installed or 0) >= cutoff))]
        self._heading(_("TOOLS"))
        rows.append(self._row(CLEANUP, "🧹", _("Cleanup"), tools.get(CLEANUP, "…")))
        rows.append(self._row(DUPLICATES, "👯", _("Duplicates"), tools.get(DUPLICATES, "0")))
        rows.append(self._row(HISTORY, "🕘", _("History"), tools.get(HISTORY, "…")))
        for kind, heading in KIND_LABELS.items():
            group = [s for s in sources if s.kind == kind]
            if group:
                self._heading(_(heading))
            for src in group:
                rows.append(self._row(src.id, src.emoji, src.label, per_source.get(src.id, 0),
                                      error=errors.get(src.id)))

        target = next((r for r in rows if r.view_id == self.view), rows[0])
        self.view = target.view_id
        self.source_list.select_row(target)
        self._rebuilding = False

    def rows(self) -> dict[str, Gtk.ListBoxRow]:
        found, row = {}, self.source_list.get_first_child()
        while row:
            if hasattr(row, "view_id"):
                found[row.view_id] = row
            row = row.get_next_sibling()
        return found

    def _heading(self, text: str) -> None:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["heading-row"])
        row.set_child(label(text, "sidebar-heading", xalign=0))
        self.source_list.append(row)

    def _row(self, view_id: str, emoji: str, name: str, count: int | str,
             error: str | None = None) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.view_id = view_id
        box = Gtk.Box(spacing=10)
        box.append(label(emoji, "source-emoji"))
        box.append(label(name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if error:
            row.set_tooltip_text(_("Couldn't read {name}: {error}").format(name=name, error=error))
            box.append(label("⚠️", "source-error"))
        row.count = label(count if isinstance(count, str) else f"{count:,}", "count")
        box.append(row.count)
        row.set_child(box)
        self.source_list.append(row)
        return row

    def _row_selected(self, _list, row) -> None:
        if row is None or self._rebuilding or not hasattr(row, "view_id"):
            return
        self.view = row.view_id
        self._on_view_selected(self.view)
