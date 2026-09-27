#!/usr/bin/env bash
# Install URPM for the current user, on any Linux distro.
#
#   ./install.sh               install / update (asks before installing dependencies)
#   ./install.sh --yes         same, without asking
#   ./install.sh --uninstall   remove it again
#
# Or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/MrNaji1/URPM/main/install.sh | bash
set -euo pipefail

REPO="MrNaji1/URPM"
APP_ID="io.github.urpm"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA/urpm"
BIN="$HOME/.local/bin/urpm"
DESKTOP="$DATA/applications/$APP_ID.desktop"
ICON="$DATA/icons/hicolor/scalable/apps/urpm.svg"
ASSUME_YES=0

say()  { printf '\033[1;35m💖 %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠️  %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m😿 %s\033[0m\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --uninstall)
      rm -rf "$PREFIX" "$BIN" "$DESKTOP" "$ICON"
      update-desktop-database "$DATA/applications" &>/dev/null || true
      say "URPM removed. Bye bye 👋"
      exit 0 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $arg (try --help)" ;;
  esac
done

have() { command -v "$1" &>/dev/null; }
gtk_ok() { python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' &>/dev/null; }

# --- 1. dependencies: Python 3.10+, PyGObject, GTK 4 (and polkit for removing packages)
deps_command() {
  if   have apt-get; then echo "apt-get install -y python3 python3-gi gir1.2-gtk-4.0 pkexec"
  elif have dnf;     then echo "dnf install -y python3 python3-gobject gtk4 polkit"
  elif have zypper;  then echo "zypper --non-interactive install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 polkit"
  elif have pacman;  then echo "pacman -S --needed --noconfirm python python-gobject gtk4 polkit"
  elif have xbps-install; then echo "xbps-install -y python3 python3-gobject gtk4 polkit"
  elif have apk;     then echo "apk add python3 py3-gobject3 gtk4.0 polkit"
  elif have emerge;  then echo "emerge --noreplace dev-python/pygobject gui-libs/gtk sys-auth/polkit"
  elif have eopkg;   then echo "eopkg install -y python3 python-gobject libgtk-4 polkit"
  elif have nix-env; then echo ""   # NixOS: use the flake/shell, see README
  fi
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"
  elif have sudo; then sudo "$@"
  elif have doas; then doas "$@"
  else die "Need sudo or doas to install dependencies."
  fi
}

if ! have python3 || ! gtk_ok; then
  cmd="$(deps_command)"
  [ -n "$cmd" ] || die "Couldn't detect your package manager. Install Python 3, PyGObject and GTK 4, then re-run."
  warn "URPM needs Python 3 with GTK 4 bindings."
  echo "   It will run:  sudo $cmd"
  if [ "$ASSUME_YES" -eq 0 ]; then
    if [ -t 0 ] || [ -r /dev/tty ]; then
      read -r -p "   Install them now? [Y/n] " reply </dev/tty || reply=n
    else
      reply=n
    fi
    [[ "${reply:-y}" =~ ^[Yy]?$ ]] || die "OK! Install them yourself, then run this again."
  fi
  # shellcheck disable=SC2086
  as_root $cmd
  gtk_ok || die "GTK 4 bindings still not found. See the README for your distro."
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "URPM needs Python 3.10 or newer."

# --- 2. find the source (this checkout, or download it when piped from curl)
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ ! -f "$SRC/urpm/__init__.py" ]; then
  have curl || have wget || die "Need curl or wget to download URPM."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  say "Downloading URPM…"
  url="https://github.com/$REPO/archive/refs/heads/main.tar.gz"
  if have curl; then curl -fsSL "$url" | tar -xz -C "$TMP"; else wget -qO- "$url" | tar -xz -C "$TMP"; fi
  SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1)"
fi

# --- 3. copy files
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp -r "$SRC/urpm" "$PREFIX/"
find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +
cp "$SRC/urpm/data/icons/urpm.svg" "$ICON"

cat > "$BIN" <<EOF
#!/bin/sh
PYTHONPATH="$PREFIX" exec python3 -m urpm "\$@"
EOF
chmod +x "$BIN"

sed "s|^Exec=.*|Exec=$BIN|" "$SRC/urpm/data/$APP_ID.desktop" > "$DESKTOP"
update-desktop-database "$DATA/applications" &>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" &>/dev/null || true

have pkexec || warn "pkexec (polkit) isn't installed: URPM can list packages, but will only copy remove/update commands for you."
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin isn't on your PATH; launch URPM from your app menu or run $BIN" ;; esac
say "URPM $(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/urpm/__init__.py") installed! Find it in your app menu, or run: urpm"
