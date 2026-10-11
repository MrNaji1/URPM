"""Canned output for the fake package managers, in the real tools' formats.

Each entry is (program, pattern for the arguments, output, exit code). Patterns use
shell-style wildcards (fnmatch). "{root}" is replaced with the fake system's root folder.
"""

import json

T = "\t"

RESPONSES = [
    # ------------------------------------------------------------------ Debian / APT
    ("apt-mark", "showmanual", "firefox\n7zip\nbind9-libs\n", 0),
    ("dpkg", "--print-architecture", "amd64\n", 0),
    ("dpkg-query", "-W -f *", "".join(T.join(row) + "\n" for row in [
        ["firefox", "131.0+build1", "260000", "web", "ii ", "https://www.mozilla.org/",
         "Safe and easy web browser from Mozilla"],
        ["libgtk-4-1:amd64", "4.14.2+ds-1", "9000", "libs", "ii ", "",
         "GTK graphical user interface library"],
        ["7zip", "23.01+dfsg-11", "6158", "utils", "ii ", "https://www.7-zip.org/",
         "7-Zip file archiver"],
        # apt-mark lists this as "bind9-libs": dpkg adds the native ":amd64" suffix
        ["bind9-libs:amd64", "1:9.18.28", "3000", "libs", "ii ", "", "Shared DNS libraries"],
        ["removed-pkg", "1.0", "10", "misc", "rc ", "", "Removed, config files left"],
    ]), 0),
    ("dpkg-query", "-s firefox", """Package: firefox
Status: install ok installed
Maintainer: Ubuntu Mozilla Team <ubuntu-mozillateam@lists.ubuntu.com>
Architecture: amd64
Depends: libc6, libgtk-3-0
Description: Safe and easy web browser from Mozilla
 Firefox delivers safe, easy web browsing. A familiar user interface,
 enhanced security features and smart searching.
 .
 - Tabbed browsing
 - Private windows
""", 0),
    ("dpkg-query", "-L firefox", "/.\n/usr\n/usr/bin/firefox\n"
                                 "{root}/usr/share/applications/firefox.desktop\n", 0),
    ("apt-get", "-s remove 7zip", "NOTE: This is only a simulation!\n"
                                  "Remv p7zip-full [16.02]\nRemv 7zip [23.01+dfsg-11]\n", 0),
    ("apt-get", "-s autoremove", "NOTE: This is only a simulation!\n"
                                 "Remv libgtk-4-1 [4.14.2+ds-1]\nRemv old-helper [1.0]\n", 0),
    ("apt-cache", "rdepends --installed *libgtk-4-1*",
     "libgtk-4-1\nReverse Depends:\n  firefox\n |gnome-text-editor\n  firefox\n", 0),
    ("apt-cache", "rdepends --installed *firefox", "firefox\nReverse Depends:\n", 0),
    ("dpkg-query", "-S *", "firefox: {root}/usr/share/applications/firefox.desktop\n"
                           "dpkg-query: no path found matching pattern x\n", 1),
    ("apt", "list --upgradable",
     "Listing...\nfirefox/noble-updates 132.0+build2 amd64 [upgradable from: 131.0+build1]\n", 0),

    # ------------------------------------------------------------------ Fedora / RPM + DNF
    ("rpm", "-qa --queryformat *", "".join(T.join(row) + "\n" for row in [
        ["bash", "5.2.26-3.fc40", "8123456", "Unspecified", "1710000000",
         "https://www.gnu.org/software/bash", "The GNU Bourne Again shell"],
        ["gimp", "2.10.38-1.fc40", "120000000", "Unspecified", "1720000000",
         "https://www.gimp.org/", "GNU Image Manipulation Program"],
        ["gpg-pubkey", "a15b79cc-63d04c2c", "0", "Public Keys", "1710000000", "(none)",
         "gpg key"],
    ]), 0),
    ("rpm", "-qi gimp", """Name        : gimp
Version     : 2.10.38
Architecture: x86_64
License     : GPL-3.0-or-later
Vendor      : Fedora Project
Description :
GIMP (GNU Image Manipulation Program) is a powerful image composition and
editing program.
""", 0),
    ("rpm", "-ql gimp", "/usr/bin/gimp\n{root}/usr/share/applications/gimp.desktop\n", 0),
    ("dnf", "repoquery --installed --whatrequires gimp --recursive *",
     "gimp-data\ngimp-libs\ngimp\n", 0),
    ("dnf", "repoquery --upgrades *", "gimp 2:2.10.38-2.fc40\n", 0),
    ("dnf", "repoquery --unneeded *", "bash\nold-lib\nold-lib\n", 0),
    ("dnf", "repoquery --installed --whatrequires gimp --qf *", "gimp-plugin-x\ngimp\n", 0),
    ("zypper", "-n rm --dry-run gimp", "Loading repository data...\n\n"
     "The following 2 packages are going to be REMOVED:\n  gimp gimp-plugin-aa\n\n"
     "2 packages to remove.\n", 0),

    # ------------------------------------------------------------------ Arch / pacman
    ("pacman", "-Qi", """Name            : bash
Version         : 5.2.026-2
Description     : The GNU Bourne Again shell
Repository      : core
URL             : https://www.gnu.org/software/bash/bash.html
Installed Size  : 9.41 MiB
Install Date    : Tue 23 Jan 2024 10:11:12 AM CET
Install Reason  : Installed as a dependency for another package

Name            : firefox
Version         : 131.0-1
Description     : Standalone web browser from mozilla.org
Repository      : extra
URL             : https://www.mozilla.org/firefox/
Licenses        : MPL-2.0
Depends On      : gtk3  libxt
Installed Size  : 240.00 MiB
Install Date    : Wed 02 Oct 2024 09:00:00 AM CEST
Install Reason  : Explicitly installed
""", 0),
    ("pacman", "-Qi firefox", """Name            : firefox
Version         : 131.0-1
Licenses        : MPL-2.0
Depends On      : gtk3  libxt
Required By     : None
Install Reason  : Explicitly installed
""", 0),
    ("pacman", "-Qlq firefox", "/usr/\n/usr/bin/firefox\n"
                               "{root}/usr/share/applications/firefox.desktop\n", 0),
    ("pacman", "-Rs --print --print-format %n firefox", "firefox\nlibxt\n", 0),
    ("checkupdates", "", "firefox 131.0-1 -> 132.0-1\n", 0),
    ("pacman", "-Qi bash", "Name            : bash\nRequired By     : firefox  readline\n", 0),
    ("pacman", "-Qdtq", "bash\n", 0),
    ("paccache", "-dk1", "==> finished dry run: 3 candidates (disk space saved: 120.36 MiB)\n", 0),

    # ------------------------------------------------------------------ Void / XBPS
    ("xbps-query", "-m", "base-system-0.114_1\nvim-9.1.0_1\n", 0),
    ("xbps-query", "-l", "ii base-system-0.114_1   Void Linux base system meta package\n"
                         "ii vim-9.1.0_1           Vi IMproved\n"
                         "ii ncurses-6.4_1         System V Release 4.0 curses emulation\n", 0),
    ("xbps-query", "-S vim", "architecture: x86_64\ninstalled_size: 33MB\n"
                             "homepage: https://www.vim.org\nlicense: Vim\n", 0),
    ("xbps-query", "-f vim", "/usr/bin/vim\n/usr/bin/vi -> /usr/bin/vim\n", 0),
    ("xbps-query", "-X vim", "vim-colorschemes-1.0_1\n", 0),
    ("xbps-install", "-nu", "vim-9.1.1_1 update x86_64 https://repo-default.voidlinux.org\n", 0),

    # ------------------------------------------------------------------ Alpine / apk
    ("apk", "del -s vim", "(1/2) Purging vim (9.1.0-r0)\n(2/2) Purging xxd (9.1.0-r0)\nOK\n", 0),
    ("apk", "info -r vim", "vim-9.1.0-r0 is required by:\nvim-airline-0.11-r0\n", 0),
    ("apk", "version -l <", "Installed:  Available:\nvim-9.1.0-r0 < 9.1.1-r0\n", 0),

    # ------------------------------------------------------------------ Solus / eopkg
    ("eopkg", "list-upgrades", "nano - Small, friendly text editor\n", 0),

    # ------------------------------------------------------------------ Flatpak
    ("flatpak", "list --columns=*", "".join(T.join(row) + "\n" for row in [
        ["org.example.Notes", "Notes", "2.1", "15.8 MB", "flathub", "system",
         "system,current", "org.example.Notes/x86_64/stable",
         "org.freedesktop.Platform/x86_64/24.08", "Write things down"],
        ["org.freedesktop.Platform", "Freedesktop Platform", "24.08.5", "668.9 MB",
         "flathub", "system", "system,runtime", "org.freedesktop.Platform/x86_64/24.08", "",
         "Runtime platform"],
        # an old platform nothing uses any more, and a GL extension (never flagged)
        ["org.gnome.Platform", "GNOME Application Platform", "45", "900 MB", "flathub",
         "system", "system,runtime", "org.gnome.Platform/x86_64/45", "", "GNOME runtime"],
        ["org.freedesktop.Platform.GL.default", "Mesa", "24.0", "300 MB", "flathub",
         "system", "system,runtime", "org.freedesktop.Platform.GL.default/x86_64/23.08", "",
         "Mesa drivers"],
    ]), 0),
    ("flatpak", "info --system org.example.Notes/x86_64/stable", """
Notes - Write things down

          ID: org.example.Notes
         Ref: app/org.example.Notes/x86_64/stable
      Origin: flathub
     License: GPL-3.0
""", 0),
    ("flatpak", "remote-ls --updates --columns=ref,version",
     "app/org.example.Notes/x86_64/stable\t2.2\n", 0),

    # ------------------------------------------------------------------ Snap
    ("snap", "list --unicode=never --color=never",
     "Name     Version   Rev    Tracking       Publisher   Notes\n"
     "core22   20240111  1122   latest/stable  canonical** base\n"
     "firefox  131.0-1   4955   latest/stable  mozilla**   -\n", 0),
    ("snap", "info --unicode=never --color=never firefox",
     "name:      firefox\nsummary:   Mozilla Firefox web browser\npublisher: Mozilla**\n"
     "license:   MPL-2.0\ndescription: |\n  Firefox is a powerful, extensible web browser.\n"
     "channels:\n  latest/stable: 131.0\n", 0),
    ("snap", "list --all --unicode=never --color=never",
     "Name     Version   Rev    Tracking       Publisher   Notes\n"
     "core22   20240111  1122   latest/stable  canonical** base\n"
     "firefox  130.0-1   4800   latest/stable  mozilla**   disabled\n"
     "firefox  131.0-1   4955   latest/stable  mozilla**   -\n", 0),
    ("snap", "refresh --list", "Name     Version  Rev   Size   Publisher  Notes\n"
                               "firefox  132.0-1  5001  280MB  mozilla**  -\n", 0),

    # ------------------------------------------------------------------ Nix
    ("nix-env", "-q --json", json.dumps({
        "nixpkgs.hello": {"name": "hello-2.12.1", "pname": "hello", "version": "2.12.1"},
        "nixpkgs.ripgrep": {"name": "ripgrep-14.1.0"},
    }), 0),
    ("nix-env", "-qc", "hello-2.12.1  < 2.12.2\nripgrep-14.1.0  = 14.1.0\n", 0),
    ("nix", "--extra-experimental-features nix-command flakes profile list --json",
     json.dumps({"elements": {"hello": {"storePaths": ["/nix/store/" + "a" * 32 + "-hello-2.12.1"],
                                        "originalUrl": "flake:nixpkgs"}}}), 0),

    # ------------------------------------------------------------------ Homebrew
    ("brew", "info --json=v2 --installed", json.dumps({"formulae": [
        {"name": "gh", "desc": "GitHub command-line tool", "homepage": "https://cli.github.com",
         "versions": {"stable": "2.58.0"}, "outdated": True, "tap": "homebrew/core",
         "installed": [{"version": "2.57.0", "installed_on_request": True, "time": 1720000000}]},
        {"name": "openssl@3", "desc": "Cryptography and SSL/TLS Toolkit",
         "versions": {"stable": "3.3.2"}, "outdated": False,
         "installed": [{"version": "3.3.2", "installed_on_request": False}]},
    ], "casks": []}), 0),
    ("brew", "--cellar", "{root}/Cellar\n", 0),

    # ------------------------------------------------------------------ pipx
    ("pipx", "list --json", json.dumps({"venvs": {"black": {"metadata": {
        "python_version": "Python 3.12.3",
        "main_package": {"package": "black", "package_version": "24.8.0",
                         "apps": ["black", "blackd"]}}}}}), 0),
    ("pipx", "environment --value PIPX_LOCAL_VENVS", "{root}/pipx/venvs\n", 0),
    ("python", "-m pip list --outdated --format=json *",  # inside the pipx venv
     json.dumps([{"name": "black", "version": "24.8.0", "latest_version": "24.10.0"},
                 {"name": "click", "version": "8.1.0", "latest_version": "8.1.7"}]), 0),

    # ------------------------------------------------------------------ pip --user
    ("python3", "-m pip list --user --outdated --format=json *",
     json.dumps([{"name": "Requests", "version": "2.31.0", "latest_version": "2.32.3"}]), 0),

    ("python3", "-m pip cache dir", "{root}/pip-cache\n", 0),
    ("npm", "config get cache", "{root}/npm-cache\n", 0),
    ("brew", "cleanup -n", "Would remove: {root}/brew-cache/gh--2.50.0.bottle.tar.gz\n"
                           "==> This operation would free approximately 12.5MB of disk space.\n", 0),
    ("brew", "--cache", "{root}/brew-cache\n", 0),

    # ------------------------------------------------------------------ npm
    ("npm", "root -g", "{root}/npm-global\n", 0),
    ("npm", "outdated -g --json",
     json.dumps({"typescript": {"current": "5.6.2", "wanted": "5.6.3", "latest": "5.6.3"}}), 1),

    # ------------------------------------------------------------------ Cargo
    ("cargo", "install-update -l", "Polling registry 'https://index.crates.io/'....\n\n"
     "Package  Installed  Latest   Needs update\n"
     "ripgrep  v14.1.0    v14.1.1  Yes\n"
     "bat      v0.24.0    v0.24.0  No\n", 0),

    # ------------------------------------------------------------------ desktop
    ("gtk-launch", "*", "", 0),
]
