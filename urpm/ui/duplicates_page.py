"""The Duplicates page: apps installed more than once, through different package managers."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from urpm.duplicates import DuplicateGroup  # noqa: E402
from urpm.i18n import _, ngettext  # noqa: E402
from urpm.sources import human_size  # noqa: E402
from urpm.ui.widgets import (avatar_class, clear, format_date, label, pill_classes,  # noqa: E402
                             short_label, show_icon)


class DuplicatesPage(Gtk.ScrolledWindow):
    def __init__(self, on_remove, on_show):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self._on_remove = on_remove
        self._on_show = on_show
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["tool-page"])
        header = Gtk.Box(spacing=16, css_classes=["tool-header"])
        header.append(label("👯", "big-emoji"))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.title = label("", "tool-title", xalign=0, wrap=True)
        self.subtitle = label("", "dim", xalign=0, wrap=True)
        titles.append(self.title)
        titles.append(self.subtitle)
        header.append(titles)
        box.append(header)
        self.cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.cards)
        self.set_child(box)
        self.show_groups([])

    def show_groups(self, groups: list[DuplicateGroup]) -> None:
        self.groups = groups
        clear(self.cards)
        if not groups:
            self.title.set_label(_("🎉 No duplicates"))
            self.subtitle.set_label(_("Every app is installed only once."))
            return
        wasted = sum(g.wasted for g in groups)
        self.title.set_label(ngettext("{n} app is installed more than once",
                                      "{n} apps are installed more than once",
                                      len(groups)).format(n=len(groups)))
        self.subtitle.set_label(
            _("Keeping just one copy of each could free {size}. Pick the copy you don't use "
              "and remove it.").format(size=human_size(wasted)) if wasted else
            _("Pick the copy you don't use and remove it."))
        for group in groups:
            self.cards.append(self._card(group))

    def _card(self, group: DuplicateGroup) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["tool-card"])
        heading = Gtk.Box(spacing=12)
        icon = Gtk.Image(pixel_size=40, css_classes=["app-icon"])
        icon_name = next((c.pkg.icon for c in group.copies if c.pkg.icon), "")
        if show_icon(icon, icon_name):
            heading.append(icon)
        else:
            heading.append(label(group.name[:1].upper(), "avatar", avatar_class(group.name)))
        heading.append(label(group.name, "tool-title", xalign=0, hexpand=True))
        if group.wasted:
            heading.append(label(_("{size} could be freed").format(size=human_size(group.wasted)),
                                 "pill",
                                 "pill-update", valign=Gtk.Align.CENTER))
        card.append(heading)

        for copy in group.copies:
            row = Gtk.Box(spacing=12, css_classes=["copy-row"])
            row.append(label(f"{copy.source.emoji} {short_label(copy.source)}",
                             *pill_classes(copy.source), valign=Gtk.Align.CENTER))
            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            info.append(label(copy.pkg.name, "pkg-name", xalign=0,
                              ellipsize=Pango.EllipsizeMode.END))
            info.append(label(f"{copy.pkg.version} · {human_size(copy.pkg.size)} · " +
                              _("updated {date}").format(date=format_date(copy.pkg.installed)),
                              "dim", "small",
                              xalign=0, ellipsize=Pango.EllipsizeMode.END))
            row.append(info)
            show = Gtk.Button(label=_("Details"), css_classes=["soft", "small-button"],
                              valign=Gtk.Align.CENTER)
            show.connect("clicked", lambda _b, c=copy: self._on_show(c))
            row.append(show)
            remove = Gtk.Button(label=_("Remove this copy"), css_classes=["danger", "small-button"],
                                valign=Gtk.Align.CENTER)
            remove.connect("clicked", lambda _b, c=copy: self._on_remove(c))
            row.append(remove)
            card.append(row)
        return card
