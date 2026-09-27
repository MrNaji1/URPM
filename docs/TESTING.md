# Testing URPM

This page lists every test for URPM: the automated ones that run on each push, and a manual
checklist for things that need a real desktop. Run the automated suite with:

```bash
make test        # parsers + export; never opens a window
make test-ui     # everything, including UI tests, on a hidden Xvfb display
```

The UI tests open real GTK windows, so they're **skipped unless you ask for them**
(`URPM_UI_TESTS=1`). `make test-ui` runs them on an invisible virtual display
(`xvfb-run`, from the `xvfb` package) inside a separate D-Bus session. Nothing pops up
on your screen, and a URPM window you already have open isn't touched. CI does the same.

## 1. Automated tests

### Parsers and helpers (`tests/test_sources.py`, 41 tests, no GTK needed)

| Area | What's checked |
|---|---|
| Sizes | `2.1 MB`, `511,1 MB` (comma locales), `1.1?GB` (flatpak in the C locale), non-breaking spaces, `MiB`, `KB`, `bytes`, garbage input |
| Human sizes | `—` for unknown, B / KB / MB / GB / TB rounding |
| Name/version splitting | Void `pkg-1.0_1`, Alpine `pkg-1.0-r15`, names with digits (`libfoo-2-1.0_1`) |
| Version sorting | natural order: `1.9 < 1.10.2`, `9.0 < 10.0`, epochs, `-rc`, `v3` |
| Actions | `sudo` display, `pkexec env …` for root, shell quoting of paths with spaces |
| Root safety | commands run as root ignore user-writable folders on `$PATH` |
| Update matching | new versions only, Flatpak "new build" with the same version number |
| Batched updates | one APT command for many packages, one Flatpak command **per installation** |
| APT / dpkg | status filtering (`ii`, `hi`, `rc`), multiarch "installed by you", description reflow, `apt-get -s` simulation, `apt list --upgradable`, `pkexec` + noninteractive remove |
| Flatpak | list columns, right-aligned `flatpak info`, `remote-ls --updates` refs, AppStream descriptions (skips translations) |
| Snap | base/core/snapd marked as system, `snap refresh --list` including "all up to date" |
| RPM | `rpm -qa` format, gpg-pubkey skipped, `dnf repoquery --upgrades`, `zypper rm --dry-run` preview |
| pacman | `pacman -Qi` blocks with wrapped fields, dates, explicit vs dependency, `checkupdates` |
| XBPS / apk / Portage / eopkg | list, world/manual files, updates, removal previews, CONTENTS and files.xml |
| Nix | `nix-env -q --json`, `nix profile list --json` in both the old list and new dict formats |
| AppImage | 7 real-world file names (`-x86_64`, `-x64`, `_x86_64`, no version), scanning, dedupe, trash and launch actions |
| Homebrew / pip / pipx / npm / Cargo | JSON formats, name normalization, scoped npm packages, git vs crates.io sources |
| Registry | 16 sources, unique ids, labels and emoji present |

### Export (`tests/test_export.py`, 2 tests)

CSV quoting and columns, JSON, plain text, file extension detection (case-insensitive).

### Real window (`tests/test_ui.py`, 17 tests, runs GTK 4)

These drive the real `UrpmWindow` with a fake package source and a deliberately broken one.

| Test | What's checked |
|---|---|
| lists and sorts | packages load, alphabetical by default, stat cards |
| search | matches summaries, empty state, "Nothing here!" |
| incremental search | typing, deleting and multi-word searches all give the correct rows |
| search by source | "fake beta" matches the source name plus the package |
| Just mine and views | dependency filter, Updates view, Last 30 days view |
| sidebar | views plus each source, counts follow "Just mine" |
| broken source | ⚠️ marker and tooltip on its row, other sources still load |
| natural version sort | `1.9 < 1.10 < 10.0` in the table |
| details panel | description, fields, Update and Remove buttons, file list |
| Update all | banner only in the Updates view, one batched command |
| double-click | opens apps, and shows a friendly message for non-apps |
| compact layouts | columns hide, empty details panel hides on small windows |
| window size | saved on close, other preferences kept |
| remove preview | "Remove 3 packages", destructive button not focused, confirm callback |
| commands | output streamed, failure reported, multi-step stops at the first failure |

## 2. Manual checklist

Run through this before a release, on a real desktop.

### Startup and listing
- [x] Opens from the app menu and from a terminal (`urpm`)
- [x] Packages appear in under a second (measured: **0.45 s** for 2,440 packages)
- [x] Update check finishes in the background (measured: **~1.5 s**)
- [x] Opening URPM again brings the existing window forward instead of opening a second one
- [x] `urpm --version`, `urpm --help`, `urpm --install-desktop`

### Browsing
- [x] Every sidebar view shows the right packages and counts
- [x] Sorting by each column, both directions (versions sort naturally)
- [x] Search: typing, deleting, `Esc` to clear, typing without clicking the box first
- [x] Just mine: counts in the sidebar and stat cards change
- [x] Light/dark switch, and the choice is remembered after restart
- [x] Window size is remembered after restart
- [x] Small windows: ~1200 px hides *Updated*, ~960 px also hides *Source* and the empty details panel

### Details and actions
- [x] Descriptions for APT and Flatpak (AppStream) packages
- [x] Show files lists the package's files
- [x] Open (button and double-click) launches Flatpak apps and AppImages
- [x] Remove shows the preview (7zip → also p7zip, p7zip-full) and never starts focused on Remove
- [ ] Remove and Update actually run through the polkit password prompt *(needs a throwaway package)*
- [x] Copy name and Copy uninstall command put text on the clipboard
- [x] Export: file contents for `.csv`, `.json` and `.txt` are checked by tests *(the save dialog itself was not clicked through)*

### Installing
- [x] `./install.sh` in a clean home, then `--uninstall`
- [x] `curl … | bash` from GitHub
- [x] `.deb` from the release builds, and its contents run
- [x] `pipx install --system-site-packages` in a venv, then `--install-desktop`
- [ ] PKGBUILD on Arch, RPM spec on Fedora/openSUSE *(needs those distros)*

## 3. Results of the last test round (v1.1.0)

| Found | Fix |
|---|---|
| Search lagged ~130 ms per keystroke with 2,440 packages | Sort before filtering, incremental filter updates, cached sort keys, no per-row tooltips, no stats recalculation while searching: now **~85 ms**, half as many row rebuilds |
| Versions sorted as text (`9build1` above `10.0`) and updates were forced to the top | Natural version order |
| The window couldn't shrink below ~1,290 px, and package names collapsed to "…" | Compact layouts at 1,280 px and 1,120 px, the minimum is now ~730 px |
| Stat numbers were cut off ("32.…") | Numbers never truncate, only captions do |
| A failing package manager only showed a 4-second toast | ⚠️ marker and tooltip on its sidebar row |
| Sidebar counts ignored "Just mine" | Counts follow the switch |
| Updating 8 packages took 8 clicks and 8 password prompts | **Update all** with one prompt per package manager |
| No mouse/keyboard shortcuts on rows | Double-click or Enter opens, Delete starts the remove flow |
| Searching "flatpak" didn't find Flatpak apps | Search includes the package manager's name |
| Size text touched the table edge | Spacing fixed |
