# System-wide install, mostly for distro packagers:
#   make install PREFIX=/usr DESTDIR=/tmp/pkgroot
#   sudo make install                # -> /usr/local
#   sudo make uninstall
# For a per-user install without root, use ./install.sh instead.

PREFIX  ?= /usr/local
DESTDIR ?=
APP_ID  := io.github.urpm
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' urpm/__init__.py)

LIBDIR  := $(DESTDIR)$(PREFIX)/share/urpm
BINDIR  := $(DESTDIR)$(PREFIX)/bin
APPDIR  := $(DESTDIR)$(PREFIX)/share/applications
ICONDIR := $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps
METADIR := $(DESTDIR)$(PREFIX)/share/metainfo

.PHONY: all install uninstall test test-ui deb run clean

all:
	@echo "URPM $(VERSION): try 'make run', 'make test', 'sudo make install' or 'make deb'"

run:
	python3 -m urpm

test:
	python3 -m unittest discover -s tests -v

# The UI tests open real windows, so run them on an invisible X display (xvfb-run)
# inside their own D-Bus session. Set XVFB_RUN=/path/to/xvfb-run if it isn't on $$PATH.
XVFB_RUN ?= xvfb-run
test-ui:
	URPM_UI_TESTS=1 WAYLAND_DISPLAY= GDK_BACKEND=x11 $(XVFB_RUN) -a -s "-screen 0 1600x1000x24" \
		dbus-run-session -- python3 -m unittest discover -s tests -v

install:
	install -d $(LIBDIR) $(BINDIR) $(APPDIR) $(ICONDIR) $(METADIR)
	cp -r urpm $(LIBDIR)/
	find $(LIBDIR) -name __pycache__ -prune -exec rm -rf {} +
	printf '#!/bin/sh\nPYTHONPATH="%s" exec python3 -m urpm "$$@"\n' "$(PREFIX)/share/urpm" > $(BINDIR)/urpm
	chmod 755 $(BINDIR)/urpm
	install -m 644 urpm/data/$(APP_ID).desktop $(APPDIR)/$(APP_ID).desktop
	install -m 644 urpm/data/icons/urpm.svg $(ICONDIR)/urpm.svg
	install -m 644 urpm/data/$(APP_ID).metainfo.xml $(METADIR)/$(APP_ID).metainfo.xml

uninstall:
	rm -rf $(LIBDIR)
	rm -f $(BINDIR)/urpm $(APPDIR)/$(APP_ID).desktop $(ICONDIR)/urpm.svg \
	      $(METADIR)/$(APP_ID).metainfo.xml

deb:
	./packaging/debian/build-deb.sh

clean:
	rm -rf dist build *.egg-info
	find . -name __pycache__ -prune -exec rm -rf {} +
