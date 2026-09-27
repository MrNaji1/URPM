#!/usr/bin/env bash
# Build dist/urpm_<version>_all.deb (Debian, Ubuntu, Mint, Pop!_OS, elementary, Zorin…)
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' urpm/__init__.py)"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

make install DESTDIR="$ROOT" PREFIX=/usr >/dev/null
install -d "$ROOT/DEBIAN" "$ROOT/usr/share/doc/urpm"
install -m 644 LICENSE "$ROOT/usr/share/doc/urpm/copyright"

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: urpm
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0
Recommends: pkexec | policykit-1
Suggests: flatpak, snapd
Installed-Size: $(du -sk "$ROOT/usr" | cut -f1)
Maintainer: MrNaji1 <https://github.com/MrNaji1>
Homepage: https://github.com/MrNaji1/URPM
Description: cute viewer for every package installed on your computer
 URPM lists everything installed through APT, Flatpak, Snap, AppImage,
 Homebrew, Nix, pip, pipx, npm and Cargo in one searchable window. It can
 check for updates, open apps, and safely remove packages after showing
 exactly what else would be removed.
CONTROL

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/urpm_${VERSION}_all.deb" >/dev/null
echo "Built dist/urpm_${VERSION}_all.deb"
