"""The Cleanup centre page: everything that can go, biggest first."""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from urpm.sources import Cleanup, human_size  # noqa: E402
from urpm.ui.widgets import clear, label  # noqa: E402

KIND_EMOJI = {"packages": "📦", "cache": "🗂️", "data": "📁"}


class CleanupPage(Gtk.ScrolledWindow):
    def __init__(self, on_review, on_rescan):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._on_review = on_review
        self.cleanups: list[Cleanup] = []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["tool-page"])

        header = Gtk.Box(spacing=16, css_classes=["tool-header"])
        header.append(label("🧹", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         hexpand=True)
        self.title = label("Looking for things to clean up…", "tool-title", xalign=0, wrap=True)
        self.subtitle = label("Unused packages, old versions and download caches", "dim",
                              xalign=0, wrap=True)
        titles.append(self.title)
        titles.append(self.subtitle)
        header.append(titles)
        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        header.append(self.spinner)
        self.rescan_button = Gtk.Button(label="Scan again", css_classes=["soft"],
                                        valign=Gtk.Align.CENTER)
        self.rescan_button.connect("clicked", lambda _b: on_rescan())
        header.append(self.rescan_button)
        box.append(header)

        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.cards)
        self.errors = label("", "dim", "small", xalign=0, wrap=True, visible=False)
        box.append(self.errors)
        self.set_child(box)

    def show_scanning(self) -> None:
        self.spinner.set_visible(True)
        self.spinner.start()
        self.rescan_button.set_sensitive(False)
        if not self.cleanups:
            self.title.set_label("Looking for things to clean up…")

    def show_results(self, cleanups: list[Cleanup], errors: list[str]) -> None:
        self.cleanups = cleanups
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.rescan_button.set_sensitive(True)
        total = sum(c.size for c in cleanups)
        checked = time.strftime("%H:%M")
        if cleanups:
            self.title.set_label(f"You can free up {human_size(total)}" if total
                                 else "Some things can be cleaned up")
            self.subtitle.set_label(f"{len(cleanups)} thing{'s' if len(cleanups) != 1 else ''} "
                                    f"found · checked at {checked}. Nothing is removed until "
                                    "you review it.")
        else:
            self.title.set_label("🎉 Nothing to clean up")
            self.subtitle.set_label(f"Your system is tidy · checked at {checked}")
        clear(self.cards)
        for item in cleanups:
            self.cards.append(self._card(item))
        self.errors.set_label("Couldn't check: " + "; ".join(errors) if errors else "")
        self.errors.set_visible(bool(errors))

    def _card(self, item: Cleanup) -> Gtk.Widget:
        card = Gtk.Box(spacing=16, css_classes=["tool-card"])
        card.append(label(KIND_EMOJI.get(item.kind, "🧹"), "stat-emoji", valign=Gtk.Align.START))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        heading = Gtk.Box(spacing=8)
        heading.append(label(item.title, "pkg-name", xalign=0, wrap=True))
        heading.append(label(item.source, "pill", "pill-gray", valign=Gtk.Align.CENTER))
        text.append(heading)
        text.append(label(item.description, "dim", xalign=0, wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR))
        count = len(item.items)
        what = {"packages": "package", "data": "folder"}.get(item.kind, "location")
        text.append(label(f"{count} {what}{'s' if count != 1 else ''}", "small", "dim",
                          xalign=0))
        card.append(text)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        side.append(label(human_size(item.size) if item.size else "—", "tool-size", xalign=1))
        review = Gtk.Button(label="Review…", css_classes=["cute"])
        review.connect("clicked", lambda _b: self._on_review(item))
        side.append(review)
        card.append(side)
        card.cleanup = item
        return card
