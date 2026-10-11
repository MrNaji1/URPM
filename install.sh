#!/usr/bin/env bash
# Install URPM for the current user, on any Linux distro.
#
#   ./install.sh               install / update (asks before installing dependencies)
#   ./install.sh --yes         same, without asking
#   ./install.sh --uninstall   remove it again
#
# Or straight from GitHub (installs the latest release, verified by its SHA-256 checksum):
#   curl -fsSL https://raw.githubusercontent.com/MrNaji1/URPM/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/MrNaji1/URPM/main/install.sh | URPM_VERSION=v1.4.0 bash

# Everything lives in main(), which only runs on the last line. If the download is cut
# off part way, bash never reaches that line, so a half script can't run.
main() {
  set -euo pipefail

  local REPO="MrNaji1/URPM"
  local APP_ID="io.github.urpm"
  local DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
  local PREFIX="$DATA/urpm"
  local BIN="$HOME/.local/bin/urpm"
  local DESKTOP="$DATA/applications/$APP_ID.desktop"
  local ICON="$DATA/icons/hicolor/scalable/apps/urpm.svg"
  local ASSUME_YES=0

  local arg
  for arg in "$@"; do
    case "$arg" in
      -y|--yes) ASSUME_YES=1 ;;
      --uninstall)
        rm -rf "$PREFIX" "$BIN" "$DESKTOP" "$ICON"
        update-desktop-database "$DATA/applications" &>/dev/null || true
        say "URPM removed. Bye bye 👋"
        return 0 ;;
      -h|--help) usage; return 0 ;;
      *) die "Unknown option: $arg (try --help)" ;;
    esac
  done

  # --- 1. dependencies: Python 3.10+, PyGObject, GTK 4 (and polkit for removing packages)
  if ! have python3 || ! gtk_ok; then
    local cmd
    cmd="$(deps_command)"
    [ -n "$cmd" ] || die "Couldn't detect your package manager. Install Python 3, PyGObject and GTK 4, then re-run."
    warn "URPM needs Python 3 with GTK 4 bindings."
    echo "   It will run:  sudo $cmd"
    if [ "$ASSUME_YES" -eq 0 ]; then
      local reply=n
      if [ -t 0 ] || [ -r /dev/tty ]; then
        read -r -p "   Install them now? [Y/n] " reply </dev/tty || reply=n
      fi
      [[ "${reply:-y}" =~ ^[Yy]?$ ]] || die "OK! Install them yourself, then run this again."
    fi
    # shellcheck disable=SC2086
    as_root $cmd
    gtk_ok || die "GTK 4 bindings still not found. See the README for your distro."
  fi
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "URPM needs Python 3.10 or newer."

  # --- 2. find the source: this checkout, or a verified release download
  local SRC
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
  if [ ! -f "$SRC/urpm/__init__.py" ]; then
    URPM_TMP="$(mktemp -d)"
    trap 'rm -rf "$URPM_TMP"' EXIT
    SRC="$(download_release "$REPO" "$URPM_TMP")"
  fi

  # --- 3. copy files
  rm -rf "$PREFIX"
  mkdir -p "$PREFIX" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
  cp -r "$SRC/urpm" "$PREFIX/"
  find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +
  cp "$SRC/urpm/data/icons/urpm.svg" "$ICON"
  sed "s|@PREFIX@|$PREFIX|" "$SRC/urpm/data/urpm-launcher.sh.in" > "$BIN"
  chmod +x "$BIN"
  sed "s|^Exec=.*|Exec=$BIN|" "$SRC/urpm/data/$APP_ID.desktop" > "$DESKTOP"
  update-desktop-database "$DATA/applications" &>/dev/null || true
  gtk-update-icon-cache -q "$DATA/icons/hicolor" &>/dev/null || true

  have pkexec || warn "pkexec (polkit) isn't installed: URPM can list packages, but will only copy remove/update commands for you."
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin isn't on your PATH; launch URPM from your app menu or run $BIN" ;; esac
  say "URPM $(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/urpm/__init__.py") installed! Find it in your app menu, or run: urpm"
}

usage() { sed -n '2,10p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^# \{0,1\}//'; }
say()  { printf '\033[1;35m💖 %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠️  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m😿 %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" &>/dev/null; }
gtk_ok() { python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' &>/dev/null; }

deps_command() {
  if   have apt-get; then echo "apt-get install -y python3 python3-gi gir1.2-gtk-4.0 pkexec"
  elif have dnf;     then echo "dnf install -y python3 python3-gobject gtk4 polkit"
  elif have zypper;  then echo "zypper --non-interactive install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 polkit"
  elif have pacman;  then echo "pacman -S --needed --noconfirm python python-gobject gtk4 polkit"
  elif have xbps-install; then echo "xbps-install -y python3 python3-gobject gtk4 polkit"
  elif have apk;     then echo "apk add python3 py3-gobject3 gtk4.0 polkit"
  elif have emerge;  then echo "emerge --noreplace dev-python/pygobject gui-libs/gtk sys-auth/polkit"
  elif have eopkg;   then echo "eopkg install -y python3 python-gobject libgtk-4 polkit"
  fi
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"
  elif have sudo; then sudo "$@"
  elif have doas; then doas "$@"
  else die "Need sudo or doas to install dependencies."
  fi
}

fetch() {  # fetch URL FILE
  if have curl; then curl -fsSL --proto '=https' --tlsv1.2 -o "$2" "$1"
  elif have wget; then wget -q --https-only -O "$2" "$1"
  else die "Need curl or wget to download URPM."
  fi
}

# github.com/OWNER/REPO/releases/latest redirects to .../releases/tag/vX.Y.Z. Following
# that redirect avoids GitHub's API, which only allows 60 requests an hour per network
# (easy to hit on shared office, school or mobile connections).
latest_release_url() {
  python3 -c 'import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=30).geturl())' \
    "https://github.com/$1/releases/latest"
}

# Download a release tarball, check it against the release's SHA256SUMS, unpack it and
# print the folder it unpacked to.
download_release() {
  local repo="$1" tmp="$2" tag="${URPM_VERSION:-}"
  if [ -z "$tag" ]; then
    local latest
    latest="$(latest_release_url "$repo")" || die "Couldn't reach GitHub to find the latest URPM release."
    tag="${latest##*/}"
  fi
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Unexpected release name: $tag"
  local version="${tag#v}" base="https://github.com/$repo/releases/download/$tag"
  local tarball="urpm-$version.tar.gz"
  say "Downloading URPM $version…" >&2
  fetch "$base/$tarball" "$tmp/$tarball" || die "Couldn't download $tarball."
  fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS" || die "Couldn't download the checksums for $tag."
  python3 - "$tmp" "$tarball" <<'PY' || die "Checksum mismatch for $tarball: the download is broken or was tampered with. Nothing was installed."
import hashlib, sys, pathlib
folder, name = pathlib.Path(sys.argv[1]), sys.argv[2]
expected = {line.split()[1].lstrip("*"): line.split()[0]
            for line in (folder / "SHA256SUMS").read_text().splitlines() if len(line.split()) == 2}
actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
sys.exit(0 if expected.get(name) == actual else 1)
PY
  say "Checksum verified ✓" >&2
  tar -xzf "$tmp/$tarball" -C "$tmp"
  echo "$tmp/urpm-$version"
}

main "$@"
