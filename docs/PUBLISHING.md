# Publishing URPM

Everything that can be prepared in this repository is ready. Each channel below needs an
account of your own, so these steps are yours to do. They're one-time setups; after that,
a release is mostly copy and paste.

| Channel | Who it reaches | Ready in the repo | You need |
|---|---|---|---|
| **PyPI** | everyone with pipx | `pyproject.toml`, release workflow job (off until you switch it on) | a PyPI account |
| **AUR** | Arch, Manjaro, EndeavourOS, CachyOS, Garuda | `packaging/arch/PKGBUILD`, `packaging/arch/urpm-git/` | an AUR account and an SSH key |
| **Ubuntu PPA** | Ubuntu, Mint, Pop!_OS, Zorin, elementary | `packaging/debian/debian/` (CI builds it on every push) | a Launchpad account and a GPG key |
| **Fedora COPR** | Fedora, RHEL, Rocky, AlmaLinux | `packaging/rpm/urpm.spec` | a Fedora account |

The names `urpm` and `urpm-git` were free on PyPI and the AUR on 2026-10-11.

Flathub isn't an option: a sandboxed app can't see the computer's package databases, which
is the whole point of URPM.

## PyPI: `pipx install urpm`

The release workflow can publish to PyPI by itself, using PyPI's *trusted publishing*, so
no password or token is ever stored in GitHub.

1. Create an account on [pypi.org](https://pypi.org/account/register/) and turn on
   two-factor authentication (PyPI requires it).
2. Go to **Your account → Publishing → Add a new pending publisher** and enter:
   - PyPI project name: `urpm`
   - Owner: `MrNaji1`, repository: `URPM`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. On GitHub, in the URPM repository:
   - **Settings → Environments → New environment**, named `pypi`.
   - **Settings → Secrets and variables → Actions → Variables → New repository
     variable**: name `PYPI_PUBLISH`, value `true`.
4. The next tagged release is published to PyPI automatically. To publish a release that
   already exists, re-run its **Release** workflow from the Actions tab.

People then install it with:

```bash
pipx install --system-site-packages urpm   # GTK comes from the distro
urpm --install-desktop                      # adds it to the app menu
```

## AUR: `yay -S urpm`

1. Create an account on [aur.archlinux.org](https://aur.archlinux.org/register) and add
   your SSH public key in **My Account**.
2. Publish the release package (on an Arch machine, or in an Arch container):

   ```bash
   git clone ssh://aur@aur.archlinux.org/urpm.git aur-urpm
   cp URPM/packaging/arch/PKGBUILD aur-urpm/
   cd aur-urpm
   makepkg --printsrcinfo > .SRCINFO
   makepkg -si          # build and install it once, to be sure it works
   git add PKGBUILD .SRCINFO && git commit -m "urpm 2.2.0" && git push
   ```

3. Do the same for `urpm-git` with `packaging/arch/urpm-git/PKGBUILD` (it builds the latest
   code from GitHub and never needs updating).

**For each new release:** set `pkgver`, run `updpkgsums` (from `pacman-contrib`), then
`makepkg --printsrcinfo > .SRCINFO`, commit and push. The repository's own
`packaging/arch/PKGBUILD` is kept up to date after every release, so you can copy it.

## Ubuntu PPA: `sudo add-apt-repository ppa:…`

1. Create an account on [launchpad.net](https://launchpad.net/), then create and upload a
   GPG key ([Launchpad's guide](https://help.launchpad.net/YourAccount/ImportingYourPGPKey)).
2. On your Launchpad page, choose **Create a new PPA**, named `urpm`.
3. Check that the name and email in `packaging/debian/debian/changelog` and `control`
   match your GPG key. Change them if they don't.
4. Build the source package and upload it (Launchpad builds the .deb):

   ```bash
   sudo apt install devscripts dput
   git clone --branch v2.2.0 https://github.com/MrNaji1/URPM.git urpm-2.2.0
   cd urpm-2.2.0
   cp -r packaging/debian/debian .
   debuild -S -sa
   dput ppa:mrnaji1/urpm ../urpm_2.2.0_source.changes
   ```

5. Each Ubuntu release needs its own upload with its own version. Edit the first line of
   `debian/changelog` before each one, for example:
   - `urpm (2.2.0~22.04) jammy; urgency=medium` for Ubuntu 22.04 and Mint 21
   - `urpm (2.2.0~24.04) noble; urgency=medium` for Ubuntu 24.04 and Mint 22
   - `urpm (2.2.0~25.04) plucky; urgency=medium` for Ubuntu 25.04

People then install it with:

```bash
sudo add-apt-repository ppa:mrnaji1/urpm
sudo apt install urpm
```

## Fedora COPR: `sudo dnf copr enable …`

1. Log in to [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) with a Fedora
   account (create one at [accounts.fedoraproject.org](https://accounts.fedoraproject.org/)).
2. **New project**, named `urpm`. Tick the Fedora releases you want (for example Fedora 41,
   42 and rawhide), and EPEL 10 for RHEL-like systems.
3. Build from the spec file. On Fedora:

   ```bash
   sudo dnf install rpmdevtools copr-cli
   cd URPM/packaging/rpm
   spectool -g -R urpm.spec
   rpmbuild -bs urpm.spec
   copr-cli build urpm ~/rpmbuild/SRPMS/urpm-2.2.0-1.*.src.rpm
   ```

   Or, without Fedora: **New build → SCM**, clone URL
   `https://github.com/MrNaji1/URPM.git`, committish `v2.2.0`, spec file
   `packaging/rpm/urpm.spec`, build method `rpkg`.

People then install it with:

```bash
sudo dnf copr enable mrnaji1/urpm
sudo dnf install urpm
```

## After publishing

Once a channel is live, its install command belongs in the README's
**Installation** section. Each release then needs:

1. Tag `vX.Y.Z` and push. GitHub builds the .deb, the tarball and checksums, and PyPI.
2. AUR: update `pkgver` and checksums, push.
3. PPA: change the version in `debian/changelog`, `debuild -S`, `dput`.
4. COPR: start a new build.

## Where to tell people

- **Reddit:** r/linux, r/linuxmint, r/Ubuntu, r/pop_os, r/Fedora, r/opensource,
  r/linuxapps. Lead with the problem it solves and the demo GIF. Spread posts over a few
  days rather than posting everywhere at once, and answer comments.
- **Linux Mint forums:** *Software & Applications*.
- **Hacker News:** a *Show HN* post.
- **Mastodon:** fosstodon.org, with the tags #linux #opensource #gtk.
- **News sites that take tips:** It's FOSS, OMG! Ubuntu, Linuxiac, 9to5Linux.
- **Directories:** add URPM to [AlternativeTo](https://alternativeto.net/) and open a pull
  request to [awesome-linux-software](https://github.com/luong-komorebi/Awesome-Linux-Software).
- **GitHub topics:** add `linux`, `package-manager`, `gtk4`, `flatpak`, `snap`,
  `appimage`, `python` and `desktop-app` to the repository, so it shows up in GitHub's
  topic pages.
