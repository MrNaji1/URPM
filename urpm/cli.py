"""`urpm` on the command line.

`urpm` alone opens the window. `urpm list`, `urpm dupes`, `urpm clean` and friends print
to the terminal instead, without loading GTK, so they work over SSH and in scripts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from urpm import __version__
from urpm.i18n import _, ngettext
from urpm.sources import Package, Source, available_sources, human_size


@dataclass
class Entry:
    """A package and where it came from (what the export and duplicate code expect)."""

    pkg: Package
    source: Source


def help_text() -> str:
    return _("""URPM {version}: every package on your computer, in one place

usage: urpm [command] [options]

  (no command)          open the URPM window
  list                  list packages   --mine  --updates  --source ID  --json
  search WORDS…         find packages by name or description
  info NAME             everything about one package
  updates               check for updates (🛡 marks security fixes)
  dupes                 apps installed more than once
  clean                 what can be cleaned up and how much space it frees
                        (shows the commands; nothing is removed)
  history               what was installed, updated or removed   --days N
  export FILE           save the list as .csv, .json or .txt
  backup FILE           save the apps you installed, to move to a new computer
                        (also writes FILE.sh, a script that reinstalls them)
  restore FILE          show what's missing on this computer   --install to install it

  --check-updates       check once and notify about new updates (for timers)
  --install-desktop     add URPM to your app menu (for pip / pipx installs)
  --uninstall-desktop   remove it from your app menu again
  --version             print the version
  -h, --help            show this help
""").format(version=__version__)


# ---------------------------------------------------------------- output helpers

def color(text: str, code: str, stream=None) -> str:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or not stream.isatty():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def bold(text: str) -> str:
    return color(text, "1")


def dim(text: str) -> str:
    return color(text, "2")


def table(rows: list[list[str]], widths: list[int] | None = None) -> str:
    """Left-aligned columns; the last column takes whatever room is left."""
    if not rows:
        return ""
    count = len(rows[0])
    widths = widths or [min(max(len(r[i]) for r in rows), 40) for i in range(count - 1)]
    total = shutil.get_terminal_size((100, 20)).columns
    lines = []
    for row in rows:
        cells = [row[i][:widths[i]].ljust(widths[i]) for i in range(count - 1)]
        used = sum(widths) + 2 * (count - 1)
        last = row[-1]
        if sys.stdout.isatty() and len(last) > max(10, total - used):
            last = last[:max(10, total - used) - 1] + "…"
        lines.append("  ".join([*cells, last]).rstrip())
    return "\n".join(lines)


def status(text: str) -> None:
    """A transient line on stderr while something slow happens."""
    if sys.stderr.isatty():
        sys.stderr.write("\r\x1b[K" + text)
        sys.stderr.flush()


def clear_status() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()


# ---------------------------------------------------------------- loading

def load(with_icons: bool = False) -> tuple[list[Entry], list[str]]:
    sources = available_sources()
    status(_("Reading {sources}…").format(sources=", ".join(s.label for s in sources)))
    index = {}
    if with_icons:
        from urpm import icons
        try:
            index = icons.build_index()
        except Exception:
            index = {}

    def read(src: Source):
        try:
            packages = src.list_packages()
        except Exception as exc:
            return src, [], f"{src.label}: {exc}"
        if with_icons:
            from urpm import icons
            icons.assign_icons(src, packages, index)
        return src, packages, None

    entries, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
        for src, packages, error in pool.map(read, sources):
            entries += [Entry(p, src) for p in packages if p.name.strip()]
            if error:
                errors.append(error)
    clear_status()
    return entries, errors


def check_updates(entries: list[Entry]) -> list[str]:
    by_source: dict[Source, list[Package]] = {}
    for e in entries:
        by_source.setdefault(e.source, []).append(e.pkg)
    status(_("Checking for updates…"))

    def check(item):
        src, pkgs = item
        try:
            src.check_updates(pkgs)
            return None
        except Exception as exc:
            return f"{src.label}: {exc}"

    with ThreadPoolExecutor(max_workers=max(1, len(by_source))) as pool:
        errors = [e for e in pool.map(check, by_source.items()) if e]
    clear_status()
    return errors


def warn(errors: list[str]) -> None:
    for error in errors:
        print(color("⚠ " + _("Couldn't read {what}").format(what=error), "33", sys.stderr),
              file=sys.stderr)


def by_source(entries: list[Entry]) -> dict[Source, list[Package]]:
    grouped: dict[Source, list[Package]] = {}
    for e in entries:
        grouped.setdefault(e.source, []).append(e.pkg)
    return grouped


def option(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            value = args[i + 1]
            del args[i:i + 2]
            return value
        raise SystemExit(_("{option} needs a value").format(option=name))
    return default


def flag(args: list[str], name: str) -> bool:
    if name in args:
        args.remove(name)
        return True
    return False


# ---------------------------------------------------------------- commands

def package_rows(entries: list[Entry]) -> list[list[str]]:
    rows = []
    for e in sorted(entries, key=lambda e: (e.pkg.name.casefold(), e.source.label)):
        pkg = e.pkg
        version = f"{pkg.version} → {pkg.update}" if pkg.update else pkg.version
        marks = ("🛡 " if pkg.update and pkg.security else "") + ("📌 " if pkg.held else "")
        rows.append([pkg.name, version, e.source.label, human_size(pkg.size).replace("—", ""),
                     marks + pkg.summary])
    return rows


def as_json(entries: list[Entry]) -> str:
    from urpm.export import export_rows
    return json.dumps(export_rows(entries), indent=2, ensure_ascii=False)


def cmd_list(args: list[str]) -> int:
    mine, updates, as_json_ = flag(args, "--mine"), flag(args, "--updates"), flag(args, "--json")
    source = option(args, "--source")
    entries, errors = load()
    if source:
        entries = [e for e in entries if e.source.id == source or
                   e.source.label.lower() == source.lower()]
    if mine:
        entries = [e for e in entries if e.pkg.explicit]
    if updates:
        errors += check_updates(entries)
        entries = [e for e in entries if e.pkg.update]
    warn(errors)
    if as_json_:
        print(as_json(entries))
        return 0
    print(table(package_rows(entries)))
    print(dim(ngettext("{n} package", "{n} packages", len(entries)).format(n=f"{len(entries):,}")))
    return 0


def cmd_search(args: list[str]) -> int:
    words = [w.lower() for w in args if not w.startswith("--")]
    if not words:
        print(_("What should I search for? Try: urpm search firefox"), file=sys.stderr)
        return 2
    entries, errors = load()
    warn(errors)
    found = [e for e in entries if all(w in f"{e.pkg.name} {e.pkg.ident} {e.pkg.summary} "
                                            f"{e.source.label}".lower() for w in words)]
    if "--json" in args:
        print(as_json(found))
        return 0 if found else 1
    if not found:
        print(_("Nothing matches “{query}”.").format(query=" ".join(words)))
        return 1
    print(table(package_rows(found)))
    return 0


def cmd_info(args: list[str]) -> int:
    if not args:
        print(_("Which package? Try: urpm info firefox"), file=sys.stderr)
        return 2
    name = args[0].lower()
    entries, errors = load()
    warn(errors)
    matches = [e for e in entries if e.pkg.name.lower() == name or e.pkg.ident.lower() == name
               or e.pkg.extra.get("app_id", "").lower() == name]
    if not matches:
        print(_("“{name}” isn't installed. Try: urpm search {name}").format(name=args[0]))
        return 1
    for i, e in enumerate(matches):
        if i:
            print()
        pkg, src = e.pkg, e.source
        print(bold(pkg.name) + "  " + dim(f"{src.emoji} {src.label} · {pkg.version}"))
        if pkg.summary:
            print(pkg.summary)
        rows = [[_("Size"), human_size(pkg.size)], [_("Section"), pkg.section],
                [_("ID"), pkg.ident if pkg.ident != pkg.name else ""],
                [_("Homepage"), pkg.homepage]]
        if src.tracks_explicit:
            rows.append([_("Installed"), _("by you") if pkg.explicit else _("as a dependency")])
        try:
            details = src.details(pkg)
            have = {k for k, _v in rows}
            rows += [[k, v.replace("\n", " ")] for k, v in details.fields if k not in have]
        except Exception as exc:
            details = None
            rows.append([_("Details"), _("couldn't load: {error}").format(error=exc)])
        print(table([[k, v] for k, v in rows if v and v != "—"], widths=[14]))
        if details and details.description:
            print()
            print(details.description)
        try:
            needed = src.required_by(pkg)
        except Exception:
            needed = None
        if needed is not None:
            print()
            print(bold(_("Needed by")) + " " + (", ".join(needed[:40]) if needed
                                                 else _("nothing else installed")))
        try:
            permissions = src.permissions(pkg)
        except Exception:
            permissions = None
        if permissions:
            print()
            print(bold(_("Permissions")))
            for perm in permissions:
                line = f"  {perm.emoji} {perm.text}"
                print(color(line, "31") if perm.risky else line)
    return 0


def cmd_updates(args: list[str]) -> int:
    entries, errors = load()
    errors += check_updates(entries)
    warn(errors)
    found = [e for e in entries if e.pkg.update]
    if "--json" in args:
        print(as_json(found))
        return 0
    if not found:
        print("🎉 " + _("Everything is up to date."))
        return 0
    found.sort(key=lambda e: (not e.pkg.security, e.pkg.name.casefold()))
    print(table(package_rows(found)))
    security = sum(1 for e in found if e.pkg.security)
    summary = ngettext("{n} update available", "{n} updates available", len(found)).format(
        n=len(found))
    if security:
        summary += " · 🛡 " + ngettext("{n} security fix", "{n} security fixes",
                                       security).format(n=security)
    print(bold(summary))
    return 0


def cmd_dupes(args: list[str]) -> int:
    from urpm.duplicates import find_duplicates
    entries, errors = load()
    warn(errors)
    groups = find_duplicates(entries)
    if not groups:
        print("✨ " + _("No app is installed more than once."))
        return 0
    for group in groups:
        print(bold(group.name) + "  " + dim(_("{size} could be freed").format(
            size=human_size(group.wasted))))
        for copy in group.copies:
            print(f"  {copy.source.emoji} {copy.source.label:<10} {copy.pkg.name}  "
                  f"{dim(copy.pkg.version)}  {human_size(copy.pkg.size)}")
    print(dim(ngettext("{n} app installed more than once", "{n} apps installed more than once",
                       len(groups)).format(n=len(groups))))
    return 0


def cmd_clean(args: list[str]) -> int:
    from urpm import cleanup
    entries, errors = load()
    status(_("Looking for things to clean up…"))
    grouped = by_source(entries)
    for src in available_sources():
        grouped.setdefault(src, [])
    found, scan_errors = cleanup.scan(grouped)
    clear_status()
    warn(errors + scan_errors)
    if not found:
        print("✨ " + _("Nothing to clean up."))
        return 0
    for item in found:
        size = human_size(item.size) if item.size else ""
        print(bold(item.title) + "  " + dim(f"{item.source} · {size}".rstrip(" ·")))
        shown = ", ".join(item.items[:12]) + (" …" if len(item.items) > 12 else "")
        print("  " + dim(shown))
        for action in item.actions:
            print("  $ " + action.display())
    total = sum(item.size for item in found)
    print(bold(_("{size} can be freed.").format(size=human_size(total))) + " " +
          _("Nothing was removed: run the commands above, or use the Cleanup page in URPM."))
    return 0


def cmd_history(args: list[str]) -> int:
    days = option(args, "--days", "30")
    try:
        cutoff = time.time() - float(days) * 86400
    except ValueError:
        print(_("--days needs a number"), file=sys.stderr)
        return 2
    entries, errors = load()
    warn(errors)
    events = []
    for src in {e.source for e in entries}:
        try:
            events += src.history()
        except Exception:
            continue
    events = sorted((e for e in events if e.time >= cutoff), key=lambda e: e.time,
                    reverse=True)
    if not events:
        print(_("No changes in the last {days} days.").format(days=days))
        return 0
    from urpm.history import action_word
    rows = [[time.strftime("%Y-%m-%d %H:%M", time.localtime(e.time)), action_word(e.action),
             e.source, e.name + (f"  {e.old_version} → {e.version}" if e.old_version
                                 else f"  {e.version}" if e.version else "")]
            for e in events]
    print(table(rows))
    return 0


def cmd_export(args: list[str]) -> int:
    if not args:
        print(_("Where should I save it? Try: urpm export packages.csv"), file=sys.stderr)
        return 2
    from urpm.export import render_export
    mine = flag(args, "--mine")
    entries, errors = load()
    warn(errors)
    if mine:
        entries = [e for e in entries if e.pkg.explicit]
    entries.sort(key=lambda e: e.pkg.name.casefold())
    with open(args[0], "w", encoding="utf-8", newline="") as fh:
        fh.write(render_export(entries, args[0]))
    print(_("Saved {n} packages to {file}").format(n=f"{len(entries):,}", file=args[0]))
    return 0


def script_path(path: str) -> str:
    base = path[:-5] if path.endswith(".json") else path
    return base + ".sh"


def cmd_backup(args: list[str]) -> int:
    from urpm import migrate
    path = args[0] if args else f"my-apps-{time.strftime('%Y-%m-%d')}.json"
    entries, errors = load(with_icons=True)
    warn(errors)
    snap = migrate.snapshot(by_source(entries))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(migrate.dumps(snap))
    sh = script_path(path)
    with open(sh, "w", encoding="utf-8") as fh:
        fh.write(migrate.script(snap, os.path.basename(sh)))
    os.chmod(sh, 0o755)
    counts: dict[str, int] = {}
    for app in snap["apps"]:
        counts[app["source"]] = counts.get(app["source"], 0) + 1
    labels = {e.source.id: e.source.label for e in entries}
    print("💾 " + ngettext("Saved {n} app to {file}", "Saved {n} apps to {file}",
                          len(snap["apps"])).format(n=len(snap["apps"]), file=path))
    print(dim(", ".join(f"{labels.get(k, k)} {v}" for k, v in counts.items())))
    print(_("On your new computer, open {file} with URPM (menu → Move to a new computer), "
            "or run: sh {script}").format(file=os.path.basename(path),
                                          script=os.path.basename(sh)))
    return 0


def execute(argv: list[str]) -> int:
    """Run a command in this terminal (so password prompts and progress show up)."""
    return subprocess.run(argv, check=False).returncode


def cmd_restore(args: list[str]) -> int:
    from urpm import migrate
    install = flag(args, "--install")
    if not args:
        print(_("Which file? Try: urpm restore my-apps.json"), file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as fh:
            snap = migrate.load(fh.read())
    except (OSError, ValueError) as exc:
        print(f"😿 {exc}", file=sys.stderr)
        return 1
    entries, errors = load()
    warn(errors)
    here = available_sources()
    installed = {}
    for e in entries:
        installed.setdefault(e.source.id, []).append(e.pkg)
    plan = migrate.plan(snap, here, installed)
    system = snap.get("system", {}).get("pretty_name", "")
    print(bold(ngettext("{n} app in this list", "{n} apps in this list",
                        len(snap["apps"])).format(n=len(snap["apps"])))
          + (dim("  " + _("saved on {system}").format(system=system)) if system else ""))
    actions = []
    for group in plan.groups:
        print(f"{group.source.emoji} {group.source.label}: " + _(
            "{missing} to install, {present} already here").format(
                missing=len(group.missing), present=group.present))
        if group.missing:
            print("  " + dim(", ".join(p.name for p in group.missing)))
        if group.not_found:
            print(color("  " + _("Not found in this computer's package sources (add their "
                                 "repositories first): {names}").format(
                names=", ".join(p.name for p in group.not_found)), "33"))
            try:
                actions += group.source.safe_install_actions(group.missing)
            except Exception as exc:
                print(color(f"  🛑 {exc}", "31"))
    for label, names in plan.unavailable.items():
        print(color(_("{label} isn't on this computer, so these are skipped: {names}").format(
            label=label, names=", ".join(names)), "33"))
    for label, names in plan.manual.items():
        print(_("Install these {label} apps yourself: {names}").format(
            label=label, names=", ".join(names)))
    if not actions:
        print("🎉 " + _("Nothing to install."))
        return 0
    print()
    for action in actions:
        print("$ " + action.display())
    if not install:
        print(dim(_("Nothing was installed. Run again with --install to install them.")))
        return 0
    for action in actions:
        print(bold("$ " + action.display()))
        code = execute(action.full_argv())
        if code != 0:
            print(color(_("😿 That failed (exit code {code}). Stopping here.").format(code=code),
                        "31"))
            return code
    print("✅ " + _("All done!"))
    return 0


COMMANDS = {"list": cmd_list, "ls": cmd_list, "search": cmd_search, "info": cmd_info,
            "updates": cmd_updates, "dupes": cmd_dupes, "duplicates": cmd_dupes,
            "clean": cmd_clean, "cleanup": cmd_clean, "history": cmd_history,
            "export": cmd_export, "backup": cmd_backup, "restore": cmd_restore}


def main(argv: list[str]) -> int:
    from urpm import i18n
    i18n.setup(i18n.saved_language())
    args = argv[1:]
    if args and args[0] in COMMANDS:
        rest = [a for a in args[1:] if a != "--dry-run"]  # clean is always a dry run
        try:
            return COMMANDS[args[0]](rest)
        except KeyboardInterrupt:
            clear_status()
            return 130
        except BrokenPipeError:  # `urpm list | head`
            return 0
    if "-h" in args or "--help" in args or "help" in args[:1]:
        print(help_text(), end="")
        return 0
    if "--version" in args:
        print(f"URPM {__version__}")
        return 0
    if "--check-updates" in args or args[:1] == ["check-updates"]:
        from urpm.notify import main as check_main
        return check_main()
    if args and not args[0].startswith("-"):
        print(_("Unknown command: {command}").format(command=args[0]), file=sys.stderr)
        print(_("Run `urpm --help` to see what URPM can do."), file=sys.stderr)
        return 2
    from urpm.ui.app import main as gui_main  # only now load GTK
    return gui_main(argv)


def run() -> None:
    """Console-script entry point (see pyproject.toml)."""
    sys.exit(main(sys.argv))
