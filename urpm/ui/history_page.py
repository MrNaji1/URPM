"""The History page: what was installed, updated or removed, newest first, by day."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from urpm.history import DOWNGRADED, INSTALLED, REINSTALLED, REMOVED, UPDATED, Event  # noqa: E402
from urpm.ui.widgets import clear, label  # noqa: E402

PAGE_SIZE = 250
ACTION_LOOK = {  # emoji, verb, css class
    INSTALLED: ("🆕", "Installed", "pill-mint"),
    UPDATED: ("⬆️", "Updated", "pill-blue"),
    REMOVED: ("🗑️", "Removed", "pill-rose"),
    DOWNGRADED: ("⬇️", "Downgraded", "pill-peach"),
    REINSTALLED: ("🔁", "Reinstalled", "pill-gray"),
}
FILTERS = (("all", "All"), (INSTALLED, "Installed"), (UPDATED, "Updated"), (REMOVED, "Removed"))


def day_label(when: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    day, today = time.localtime(when), time.localtime(now)
    days_ago = (time.mktime(today[:3] + (0, 0, 0) + today[6:]) -
                time.mktime(day[:3] + (0, 0, 0) + day[6:])) // 86400
    if days_ago == 0:
        return "Today"
    if days_ago == 1:
        return "Yesterday"
    return time.strftime("%A, %b %d, %Y", day)


class HistoryPage(Gtk.ScrolledWindow):
    def __init__(self, on_show_package):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._on_show_package = on_show_package
        self.events: list[Event] = []
        self.action = "all"
        self.query: list[str] = []
        self.hide_automatic = False
        self.limit = PAGE_SIZE

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["tool-page"])
        header = Gtk.Box(spacing=16, css_classes=["tool-header"])
        header.append(label("🕘", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         hexpand=True)
        self.title = label("Reading your package history…", "tool-title", xalign=0, wrap=True)
        self.subtitle = label("From your package managers' own logs", "dim", xalign=0, wrap=True)
        titles.append(self.title)
        titles.append(self.subtitle)
        header.append(titles)
        chips = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, css_classes=["chips"])
        self.chips: dict[str, Gtk.ToggleButton] = {}
        group = None
        for key, text in FILTERS:
            chip = Gtk.ToggleButton(label=text, css_classes=["chip"], active=key == "all")
            if group:
                chip.set_group(group)
            group = group or chip
            chip.connect("toggled", lambda b, k=key: b.get_active() and self.set_action(k))
            chips.append(chip)
            self.chips[key] = chip
        header.append(chips)
        box.append(header)

        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self.list)
        self.more = Gtk.Button(label="Show more", css_classes=["soft"], halign=Gtk.Align.CENTER,
                               visible=False)
        self.more.connect("clicked", lambda _b: self._show_more())
        box.append(self.more)
        self.set_child(box)

    # ---------------------------------------------------------------- data

    def set_events(self, events: list[Event]) -> None:
        self.events = sorted(events, key=lambda e: e.time, reverse=True)
        self.limit = PAGE_SIZE
        self.render()

    def set_action(self, action: str) -> None:
        self.action, self.limit = action, PAGE_SIZE
        self.render()

    def set_query(self, text: str) -> None:
        self.query, self.limit = text.lower().split(), PAGE_SIZE
        self.render()

    def set_hide_automatic(self, hide: bool) -> None:
        if hide != self.hide_automatic:
            self.hide_automatic, self.limit = hide, PAGE_SIZE
            self.render()

    def matching(self) -> list[Event]:
        return [e for e in self.events
                if (self.action == "all" or e.action == self.action or
                    (self.action == UPDATED and e.action in (DOWNGRADED, REINSTALLED)))
                and not (self.hide_automatic and e.automatic)
                and all(term in e.name.lower() for term in self.query)]

    def _show_more(self) -> None:
        self.limit += PAGE_SIZE
        self.render()

    # ---------------------------------------------------------------- drawing

    def render(self) -> None:
        clear(self.list)
        matching = self.matching()
        total = len(self.events)
        if not total:
            self.title.set_label("No history found")
            self.subtitle.set_label("Your package managers haven't logged any changes yet, or "
                                    "their logs aren't readable.")
        else:
            month = sum(1 for e in self.events if e.time >= time.time() - 30 * 86400)
            self.title.set_label(f"{total:,} change{'s' if total != 1 else ''}")
            self.subtitle.set_label(f"{month:,} in the last 30 days · from your package "
                                    "managers' own logs")
        if total and not matching:
            self.list.append(label("Nothing matches these filters.", "dim", margin_top=12))
        current_day = None
        for event in matching[:self.limit]:
            day = day_label(event.time)
            if day != current_day:
                current_day = day
                self.list.append(label(day.upper(), "section-heading", xalign=0, margin_top=10))
            self.list.append(self._row(event))
        self.more.set_visible(len(matching) > self.limit)
        self.more.set_label(f"Show more ({len(matching) - self.limit:,} left)")

    def _row(self, event: Event) -> Gtk.Widget:
        emoji, verb, css = ACTION_LOOK.get(event.action, ("•", event.action.title(), "pill-gray"))
        row = Gtk.Box(spacing=12, css_classes=["history-row"])
        row.append(label(time.strftime("%H:%M", time.localtime(event.time)), "dim", "small",
                         "mono", valign=Gtk.Align.CENTER))
        row.append(label(f"{emoji} {verb}", "pill", css, valign=Gtk.Align.CENTER))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                       valign=Gtk.Align.CENTER)
        text.append(label(event.name, "pkg-name", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        version = (f"{event.old_version} → {event.version}" if event.old_version
                   else event.version)
        detail = " · ".join(p for p in (version, "as a dependency" if event.automatic else "")
                            if p)
        if detail:
            text.append(label(detail, "dim", "small", "mono", xalign=0,
                              ellipsize=Pango.EllipsizeMode.MIDDLE))
        row.append(text)
        row.append(label(event.source, "pill", "pill-gray", valign=Gtk.Align.CENTER))
        if event.action != REMOVED:
            show = Gtk.Button(label="Details", css_classes=["soft", "small-button"],
                              valign=Gtk.Align.CENTER)
            show.connect("clicked", lambda _b: self._on_show_package(event))
            row.append(show)
        return row
