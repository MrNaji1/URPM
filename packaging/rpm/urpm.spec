# Fedora, RHEL, Rocky, AlmaLinux, CentOS Stream, openSUSE, Mageia…
#   rpmbuild -ba urpm.spec   (after: spectool -g -R urpm.spec)
Name:           urpm
Version:        1.1.0
Release:        1%{?dist}
Summary:        Cute GTK app that shows every package installed on your computer
License:        MIT
URL:            https://github.com/MrNaji1/URPM
Source0:        %{url}/archive/refs/tags/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  make
Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4
%if 0%{?suse_version}
Requires:       typelib(Gtk) = 4.0
Requires:       python3-gobject-Gdk
%endif
Recommends:     polkit

%description
URPM lists everything installed through RPM/DNF, Flatpak, Snap, AppImage,
Homebrew, Nix, pip, pipx, npm and Cargo in one searchable window. It can check
for updates, open apps, and safely remove packages after showing exactly what
would be removed.

%prep
%autosetup -n URPM-%{version}

%build

%install
make install DESTDIR=%{buildroot} PREFIX=%{_prefix}

%files
%license LICENSE
%doc README.md
%{_bindir}/urpm
%{_datadir}/urpm/
%{_datadir}/applications/io.github.urpm.desktop
%{_datadir}/icons/hicolor/scalable/apps/urpm.svg
%{_datadir}/metainfo/io.github.urpm.metainfo.xml

%changelog
* Sun Sep 27 2026 MrNaji1 - 1.1.0-1
- Update all, faster search, natural version sorting, compact layouts

* Sun Sep 27 2026 MrNaji1 - 1.0.0-1
- First public release
