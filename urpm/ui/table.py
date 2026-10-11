"""The package list: data model, fast sorting, filtering, columns and cells.

Rows flow through four list models:

    store (every package) → view filter (sidebar + "Just mine") → search filter → sorted

Filtering comes first, so clicking a column header only re-sorts, and a keystroke in
the search box only re-checks rows in the current view.

Sorting is the expensive part with tens of thousands of packages. Comparing rows in
Python, or even having GTK's sorters read a Python property on every comparison, made
sorting 60,000 rows take 1.5 s. Instead each text column gets an integer *rank*,
computed in one fast Python sort when the data changes, and GTK sorts by numbers: it
reads each row's value once and does the rest in C (60,000 rows in about 40 ms).
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from urpm.sources import Package, Source, human_size  # noqa: E402
from urpm.sources.base import version_key  # noqa: E402
from urpm.ui.widgets import avatar_class, format_date, label, pill_classes, short_label  # noqa: E402

READ_ONLY = GObject.ParamFlags.READABLE
NAME, VERSION, SOURCE = range(3)  # which rank
RANK_KEYS = {  # what each ranked column sorts by (ties broken by the next fields)
    NAME: lambda i: (i.name_key, i.source.label),
    VERSION: lambda i: (i.version_key, i.name_key),
    SOURCE: lambda i: (i.source.label.casefold(), i.name_key),
}


class PackageItem(GObject.Object):
    """One row. Its read-only GObject properties are what GTK's native sorters read."""

    __gtype_name__ = "UrpmPackageItem"

    def __init__(self, pkg: Package, source: Source):
        super().__init__()
        self.pkg = pkg
        self.source = source
        # Searching "flatpak discord" or "apt python" should work too.
        self.haystack = f"{pkg.name} {pkg.ident} {pkg.summary} {source.label}".lower()
        self.name_key = pkg.name.casefold()
        self.version_key = version_key(pkg.version)
        self.ranks = [0, 0, 0]

    @property
    def key(self) -> tuple[str, str]:
        """Identifies a package across reloads."""
        return self.source.id, self.pkg.ident

    @GObject.Property(type=int, flags=READ_ONLY)
    def name_rank(self) -> int:
        return self.ranks[NAME]

    @GObject.Property(type=int, flags=READ_ONLY)
    def version_rank(self) -> int:
        return self.ranks[VERSION]

    @GObject.Property(type=int, flags=READ_ONLY)
    def source_rank(self) -> int:
        return self.ranks[SOURCE]

    @GObject.Property(type=GObject.TYPE_INT64, flags=READ_ONLY)
    def size(self) -> int:
        return max(0, self.pkg.size)

    @GObject.Property(type=float, flags=READ_ONLY)
    def installed(self) -> float:
        return self.pkg.installed or 0.0


def numeric_sorter(prop: str) -> Gtk.NumericSorter:
    return Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(PackageItem, None, prop))


class PackageTable(Gtk.Box):
    def __init__(self, view_filter, on_activate, on_delete):
        super().__init__(css_classes=["table-card"], overflow=Gtk.Overflow.HIDDEN)
        self.items: list[PackageItem] = []
        self.max_size = 1
        self.terms: list[str] = []
        self._stale_ranks = {NAME, VERSION, SOURCE}

        self.store = Gio.ListStore(item_type=PackageItem)
        self.view_filter = Gtk.CustomFilter.new(lambda item, *_: view_filter(item))
        in_view = Gtk.FilterListModel(model=self.store, filter=self.view_filter)
        self.search_filter = Gtk.CustomFilter.new(self._matches_search)
        matching = Gtk.FilterListModel(model=in_view, filter=self.search_filter)
        self.shown = Gtk.SortListModel(model=matching)
        self.selection = Gtk.SingleSelection(model=self.shown, autoselect=False,
                                             can_unselect=True)
        self.view = Gtk.ColumnView(model=self.selection, css_classes=["package-table"],
                                   vexpand=True)

        # Each column's sorter; the ranked ones say which rank they need.
        self._sorters: dict[Gtk.ColumnViewColumn, tuple[Gtk.NumericSorter, int | None]] = {}
        self.col_name = self._add_column("Package", self._setup_name, self._bind_name,
                                         "name_rank", NAME, expand=True)
        self.col_version = self._add_column("Version", self._setup_version, self._bind_version,
                                            "version_rank", VERSION, width=150)
        self.col_source = self._add_column("Source", self._setup_source, self._bind_source,
                                           "source_rank", SOURCE, width=104)
        self.col_size = self._add_column("Size", self._setup_size, self._bind_size,
                                         "size", None, width=140)
        self.col_updated = self._add_column("Updated", self._setup_date, self._bind_date,
                                            "installed", None, width=116)
        # Clicking a header changes the view's own sorter; we hand the matching fast
        # sorter to the model ourselves (GTK 4.10+). Older GTK sorts through the view's
        # sorter directly: correct, just slower on huge lists.
        self._header_sorter = self.view.get_sorter()
        self._direct = hasattr(self._header_sorter, "get_primary_sort_column")
        if self._direct:
            self._header_sorter.connect("changed", lambda *_: self._sync_sorter())
        else:
            self.shown.set_sorter(self._header_sorter)
        self.view.sort_by_column(self.col_name, Gtk.SortType.ASCENDING)

        # Double-click / Enter opens the app; Delete asks to remove it.
        self.view.connect("activate", lambda _view, pos: on_activate(self.shown.get_item(pos)))
        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Delete"),
                                           Gtk.CallbackAction.new(lambda *_: on_delete() or True)))
        self.view.add_controller(keys)

        # AUTOMATIC (not NEVER) so the table doesn't force a huge minimum window width;
        # the compact layouts hide columns before a scrollbar is ever needed.
        self.append(Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.AUTOMATIC))

    # ---------------------------------------------------------------- sorting

    def _active(self) -> tuple[Gtk.NumericSorter, int | None] | None:
        column = self._header_sorter.get_primary_sort_column() if self._direct else None
        return self._sorters.get(column)

    def _sync_sorter(self) -> None:
        active = self._active()
        if active is None:
            self.shown.set_sorter(None)
            return
        sorter, rank = active
        reranked = rank is not None and rank in self._stale_ranks
        if reranked:
            self._compute_ranks(rank)
        sorter.set_sort_order(self._header_sorter.get_primary_sort_order())
        if self.shown.get_sorter() is not sorter:
            self.shown.set_sorter(sorter)   # a different sorter reads every row afresh
        elif reranked:
            self.rows_changed()

    def _compute_ranks(self, rank: int) -> None:
        for position, item in enumerate(sorted(self.items, key=RANK_KEYS[rank])):
            item.ranks[rank] = position
        self._stale_ranks.discard(rank)

    def _rerank_after_data_change(self) -> bool:
        """Rows were added or removed; recompute the ranks the current sort reads.

        Returns True when ranks changed, so the caller has GTK re-read them."""
        self._stale_ranks = {NAME, VERSION, SOURCE}
        if not self._direct:  # the view's own sorter may read any rank
            for rank in (NAME, VERSION, SOURCE):
                self._compute_ranks(rank)
            return True
        active = self._active()
        if active and active[1] is not None:
            self._compute_ranks(active[1])
            return True
        return False  # sorted by size or date: those values never change

    # ---------------------------------------------------------------- data

    def replace_source_items(self, source_id: str, fresh: list[PackageItem]) -> None:
        """Swap one source's rows in place, leaving every other row (and the selection,
        when it's elsewhere) untouched."""
        positions = [n for n, item in enumerate(self.items) if item.source.id == source_id]
        start = positions[0] if positions else len(self.items)
        count = len(positions)  # a source's rows are always next to each other
        self.items[start:start + count] = fresh
        self.max_size = max((i.pkg.size for i in self.items), default=0) or 1
        reranked = self._rerank_after_data_change()
        self.store.splice(start, count, fresh)
        if reranked:
            # GTK caches each row's sort value and only re-reads it for rows reported as
            # changed (a plain "sorter changed" keeps the cached values). New rows shift
            # everyone's rank, so report every row.
            self.rows_changed()

    def rows_changed(self) -> None:
        """Redraw every row, e.g. after update checks added badges."""
        n = self.store.get_n_items()
        self.store.items_changed(0, n, n)

    def shown_items(self) -> list[PackageItem]:
        return [self.shown.get_item(i) for i in range(self.shown.get_n_items())]

    # ---------------------------------------------------------------- filtering

    def refilter_view(self) -> None:
        self.view_filter.changed(Gtk.FilterChange.DIFFERENT)

    def set_search(self, text: str) -> None:
        old = self.terms
        self.terms = text.lower().split()
        if self.terms == old:
            return
        # Typing more can only hide rows and deleting can only show rows. Telling GTK lets
        # it re-check a fraction of the list instead of everything.
        pairs = list(zip(self.terms, old, strict=True)) if len(self.terms) == len(old) else []
        if pairs and all(new.startswith(was) for new, was in pairs):
            change = Gtk.FilterChange.MORE_STRICT
        elif pairs and all(was.startswith(new) for new, was in pairs):
            change = Gtk.FilterChange.LESS_STRICT
        elif len(self.terms) > len(old) and self.terms[:len(old)] == old:
            change = Gtk.FilterChange.MORE_STRICT   # added a word
        elif len(self.terms) < len(old) and old[:len(self.terms)] == self.terms:
            change = Gtk.FilterChange.LESS_STRICT   # removed a word
        else:
            change = Gtk.FilterChange.DIFFERENT
        self.search_filter.changed(change)

    def _matches_search(self, item: PackageItem, *_) -> bool:
        haystack = item.haystack
        for term in self.terms:
            if term not in haystack:
                return False
        return True

    # ---------------------------------------------------------------- selection

    def selected(self) -> PackageItem | None:
        return self.selection.get_selected_item()

    def select_key(self, key, clear_if_missing: bool = True) -> None:
        if key:
            for pos in range(self.shown.get_n_items()):
                if self.shown.get_item(pos).key == key:
                    if self.selection.get_selected() != pos:
                        self.selection.set_selected(pos)
                    return
        if clear_if_missing:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)

    # ---------------------------------------------------------------- layout

    def apply_layout(self, mode: int) -> None:
        """0 = every column, 1 = hide Updated, 2 = also hide Source and slim the rest."""
        self.col_updated.set_visible(mode == 0)
        self.col_source.set_visible(mode < 2)
        self.col_version.set_fixed_width(150 if mode == 0 else 130)
        self.col_size.set_fixed_width(140 if mode == 0 else 124)

    # ---------------------------------------------------------------- columns

    def _add_column(self, title, setup, bind, prop, rank, expand=False, width=None):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(setup()))
        factory.connect("bind", lambda _f, li: bind(li.get_child(), li.get_item()))
        sorter = numeric_sorter(prop)
        column = Gtk.ColumnViewColumn(title=title, factory=factory, expand=expand,
                                      resizable=True, sorter=sorter)
        if width:
            column.set_fixed_width(width)
        self._sorters[column] = (sorter, rank)
        self.view.append_column(column)
        return column

    @staticmethod
    def _setup_name():
        box = Gtk.Box(spacing=12)
        box.avatar = label("", "avatar")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        box.title = label("", "pkg-name", xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          width_chars=12)
        box.summary = label("", "dim", "small", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        text.append(box.title)
        text.append(box.summary)
        box.append(box.avatar)
        box.append(text)
        return box

    @staticmethod
    def _bind_name(box, item):
        pkg = item.pkg
        box.avatar.set_label(pkg.name[:1].upper())
        box.avatar.set_css_classes(["avatar", avatar_class(pkg.name)])
        box.title.set_label(pkg.name)
        box.summary.set_label(pkg.summary or " ")

    @staticmethod
    def _setup_version():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        box.version = label("", "mono", "dim", xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        box.update = label("", "pill", "pill-update", halign=Gtk.Align.START,
                           ellipsize=Pango.EllipsizeMode.END)
        box.append(box.version)
        box.append(box.update)
        return box

    @staticmethod
    def _bind_version(box, item):
        pkg = item.pkg
        box.version.set_label(pkg.version)
        box.update.set_label(f"↑ {pkg.update}")
        box.update.set_visible(bool(pkg.update))

    @staticmethod
    def _setup_source():
        return label("", "pill", halign=Gtk.Align.START, valign=Gtk.Align.CENTER)

    @staticmethod
    def _bind_source(widget, item):
        widget.set_label(short_label(item.source))
        widget.set_css_classes(pill_classes(item.source))

    @staticmethod
    def _setup_size():
        box = Gtk.Box(spacing=10)
        box.bar = Gtk.ProgressBar(css_classes=["size-bar"], valign=Gtk.Align.CENTER,
                                  width_request=56)
        box.text = label("", "small", xalign=1, hexpand=True, margin_end=8)
        box.append(box.bar)
        box.append(box.text)
        return box

    def _bind_size(self, box, item):
        size = item.pkg.size
        # sqrt keeps the small packages visible next to the 1 GB giants
        box.bar.set_fraction(min(1.0, math.sqrt(size / self.max_size)) if size > 0 else 0)
        box.text.set_label(human_size(size))

    @staticmethod
    def _setup_date():
        return label("", "dim", "small", xalign=0)

    @staticmethod
    def _bind_date(widget, item):
        widget.set_label(format_date(item.pkg.installed))
