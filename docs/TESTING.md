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

**129 tests, 92% code coverage.** GitHub runs all of them on every push (the window tests
under Xvfb), fails if coverage drops below 90%, and lints the code with `ruff`.

| File | Tests | What it covers |
|---|---:|---|
| `test_sources.py` | 45 | Parsers and helpers for every package manager (no GTK needed) |
| `test_e2e_sources.py` | 21 | **Every package manager end to end** on a fake system |
| `test_ui.py` | 37 | The real window, run on a hidden display |
| `test_app.py` | 11 | Command-line options, saved settings, shared helpers |
| `test_install.py` | 6 | The launcher and `install.sh` |
| `test_theme.py` | 6 | Theme colours, readability, theme choice at startup |
| `test_export.py` | 3 | CSV / JSON / text export |

### End to end on a fake system (`tests/test_e2e_sources.py`)

`tests/fakes/` builds a throwaway fake Linux system for each test:

- **Fake programs.** `pacman`, `rpm`, `dnf`, `zypper`, `apk`, `xbps-*`, `eopkg`, `flatpak`,
  `snap`, `nix-env`, `nix`, `brew`, `pipx`, `npm`, `cargo` and more, each printing
  realistic output in the real tool's format (`tests/fakes/responses.py`). `$PATH`
  contains *only* these, so no real tool can run by accident.
- **Fake databases.** dpkg's status and info files, Alpine's `installed` and `world`,
  Gentoo's `/var/db/pkg`, Solus's `metadata.xml`, a Flatpak install with AppStream data,
  Snap files, a Homebrew Cellar, a pipx environment, global npm packages, Cargo's
  `.crates2.json`, pip's `site-packages`, and AppImages in `~/Applications` and
  `~/Downloads`.

URPM's real, unmodified code then lists packages, reads details and files, checks for
updates, previews removals and builds remove/update commands for **all 16 package
managers**, with RPM tested through both DNF and Zypper. Every command URPM runs is
logged, and each test checks that only read-only commands ran.

The tests were themselves checked by deliberately breaking the fake output (a different
architecture, no updates reported, a package changing from "installed by you" to
"dependency") and the code (Gentoo marking everything as yours): every break made a test
fail. One of these checks found a gap: nothing tested Debian's ":amd64" suffix handling
for packages you installed. It's covered now.

### Parsers and helpers (`tests/test_sources.py`)

| Area | What's checked |
|---|---|
| Sizes | `2.1 MB`, `511,1 MB` (comma locales), `1.1?GB` (flatpak in the C locale), non-breaking spaces, `MiB`, `KB`, `bytes`, garbage input |
| Version sorting | natural order: `1.9 < 1.10.2`, `9.0 < 10.0`, epochs, `-rc`, `v3` |
| Root safety | `pkexec`, `env` and the program all come from system folders; refuses instead of falling back |
| Option injection | names like `--purge` or with newlines are refused; npm names come from folders |
| Untrusted AppImages | files in Downloads/Desktop or not yet executable need confirmation |
| Batched updates | one APT command for many packages, one Flatpak command per installation |
| Every package manager | parsing of real captured output, including update lists and removal previews |

### Launcher and installer (`tests/test_install.py`)

The launcher can't be hijacked from the current folder. A verified release unpacks. A
tampered download, odd release names and a cut-off script install nothing. The latest
release is found without GitHub's rate-limited API.

### Themes (`tests/test_theme.py`)

Every theme defines every colour the stylesheet uses. All colours are valid hex. All five
themes are registered. Text stays readable (WCAG contrast: body text 7:1, update badges
4.5:1, bold button labels 3:1). The starting theme comes from your saved choice, an old
1.x light/dark setting, or the system. The stylesheet has no hardcoded colours.

### Real window (`tests/test_ui.py`)

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
| settings | picking a theme applies it, marks its card, saves it and keeps other settings |
| remove preview | "Remove 3 packages", destructive button not focused, confirm callback |
| commands | output streamed, failure reported, multi-step stops at the first failure |
| progressive loading | a slow package manager doesn't hold back the others; status shows what's still loading |
| nameless packages | packages without a name are skipped |
| untrusted apps | opening one shows a confirmation; nothing runs until "Open anyway" |
| unsafe names in the UI | a refused name shows a message and never opens the remove dialog |
| cancel | a running command (and the helpers it started) stops within seconds |
| output limit | a command printing 20,000 lines keeps only the last 4,000 |
| sorting | every column sorts both ways; versions sort naturally |
| sorting while loading | rows that arrive while sorted land in the right place |
| older GTK | sorting is still correct on GTK 4.6–4.8 (Ubuntu 22.04, Debian 12) |
| export | the shown packages are written; errors are reported; the file dialog works |
| copy, shortcuts, update | copy buttons, the shortcuts window, the Update button |
| commands that can't run | they're copied to the clipboard with an explanation instead |
| update-check errors | a package manager that fails its update check is named |
| remove preview errors | a failing or unsupported preview says so instead of staying quiet |

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
- [x] Each theme in Settings looks right (checked with screenshots of all five themes)
- [x] The chosen theme is remembered after restart, and old light/dark settings carry over
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

## 3. Results of the last test rounds

### v1.5.0 (Phase 2: strong foundations)

| Found | Fix |
|---|---|
| Fedora, Arch, Alpine, Void, Gentoo and Solus code had only been tested against sample text, never by actually running the tools | A fake Linux system with fake `pacman`, `rpm`, `apk` and the rest; all 16 package managers now run end to end in tests |
| Coverage was 74% | **92%**, enforced in CI (fails below 90%) |
| Sorting 60,000 packages took 1.5–2.0 s | Integer ranks and GTK's native sorter: **0.26–0.40 s** (10,000 packages: 0.22 s → 0.06 s) |
| A new test found that rows arriving while sorted could land out of order | GTK caches sort values, so every row is now reported as changed when ranks shift |
| No Debian test covered the ":amd64" suffix on packages you installed | Added, after deliberately breaking the fake data showed the gap |
| Cargo, pipx and Nix couldn't check for updates | All three now can |
| `window.py` was one 1,161-line file | Split into the window (638 lines), table, sidebar, details panel and shared widgets |

Known trade-off: at 60,000 packages, searching got slower (0.3–0.5 s per search, from
0.2–0.3 s), because filtering now happens before sorting so that sorting is fast. Below
10,000 packages there's no difference. GTK's search box waits for a pause in typing, so
fast typing triggers one search, not one per letter.

### v1.1.0

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
