# Arch Linux GUI Installer + Control Panel

A Windows-Setup-style graphical installer for Arch Linux, and a control panel
that manages the installed system afterwards. Both are native desktop
applications (Python + Tk) — no browser, no local web server, nothing listening
on a port.

The installer follows the [official installation guide](https://wiki.archlinux.org/title/Installation_guide)
step for step: partition, format, mount, `pacstrap`, `genfstab`, `arch-chroot`
configuration, boot loader. You end up with a normal Arch system, maintained with
`pacman`.

```
installer/            the installation wizard
  __main__.py         CLI: --simulate, --dry-run, --kiosk, --check, --selftest
  context.py          logging, command execution, target files, redaction
  hardware.py         CPU/RAM/firmware/GPU/disk/network detection
  disk.py             GPT layout planning, sgdisk, mkfs, mounts
  catalog.py          the package catalogue (categories, multilib flags)
  packages.py         pacman: pacstrap, [multilib], repository search
  config.py           the installation request and its validation
  system.py           locale, keyboard, hostname, accounts, services, panel
  network.py          NetworkManager Wi-Fi profile for the new system
  bootloader.py       systemd-boot (UEFI) and GRUB (UEFI + BIOS)
  orchestrator.py     the 11-phase pipeline and its progress model
  options.py          desktops, kernels, keymaps, locales, time zones
  gui/                the window
    app.py            shell, navigation rail, install thread, power actions
    pages.py          one class per wizard step
    widgets.py        scrolling sheet, cards, step rail, banners, option cards
    theme.py          palette, fonts, ttk styles
panel/                the control panel installed with the system
  core.py             system facts, pkexec/sudo elevation
  pages.py            Overview, Software, Services, Users, Network, About
  app.py              the panel window
  __main__.py         CLI: --kiosk, --check
iso/                  the bootable medium
  build-iso.sh        mkarchiso build (Arch host)
  build-in-docker.sh  the same from any Linux host with Docker
  profile/            mkarchiso profile, packages, pacman.conf
    airootfs/         live system: autologin, X session, installers, launchers
docs/ARCHITECTURE.md  how the pieces fit together
.github/workflows/build-iso.yml   CI: builds the ISO and attaches it to a release
```

## The installation wizard

| Step | What it does |
| --- | --- |
| Welcome | What will happen, plus this machine's CPU, memory, firmware and disks |
| Hardware | CPU, RAM, firmware/Secure Boot, GPU, every disk and its partitions |
| Disk | Target disk, filesystem (ext4/btrfs/XFS), swap, EFI partition size, separate `/home`, live layout preview, erase confirmation |
| System | Hostname, time zone, locale, keyboard, boot loader, root password, daily account, optional Wi-Fi |
| Software | Desktop, kernel, microcode, **multilib switch**, package picker with categories and repository search |
| Review | Every choice, the exact partition table and the selected software |
| Install | 11 phases, weighted percentage, per-phase state, live log |
| Finish | Restart or power off |

The window draws its own layout and is the whole user interface: **the installer
is the only thing that starts on the medium**.

### Multilib (32-bit software)

Arch is 64-bit only out of the box. The Software page has one switch:

- **off** — `[multilib]` stays commented out, `steam`, `wine` and every
  `lib32-*` package are refused by validation, and the installed system is
  slimmer.
- **on** — the live medium enables `[multilib]` so the picker can list 32-bit
  packages, the target's `/etc/pacman.conf` gets the section written, and the
  chosen `lib32-*` packages are installed inside the chroot after the base
  system exists (they cannot be pacstrapped from a repository the live medium
  does not have).

The switch can be flipped later in the control panel; enabling it installs
nothing by itself, disabling it leaves already-installed 32-bit packages in
place (removing them is the `pacman -R` command the ArchWiki documents, not
something this installer does behind your back).

### Package picker

- Ten categories — desktop/browsers, office, internet, development, multimedia,
  gaming, virtualisation, fonts, maintenance, hardware support — around 120
  packages that exist in `core`/`extra`/`multilib`.
- Clicking a package marks it; the counter at the top shows how many extra
  packages the installation will add.
- **Search repositories** queries `pacman -Ss` on the live medium and adds the
  results as a temporary category, so anything in the official repositories can
  be installed, not only the catalogue.
- The AUR is not used. Package names are validated (`^[a-z0-9][a-z0-9@._+-]*$`)
  before they reach a command.

## How the medium boots

`mkarchiso` builds a normal archiso image with a squashfs live root, bootable on
UEFI (systemd-boot) and legacy BIOS (syslinux). The installer starts by itself:

```
tty1 autologin (root, systemd getty override)
   └─ /root/.bash_profile  (only on tty1, only without $DISPLAY)
        └─ /usr/local/bin/arch-installer-session
             └─ xinit :0 vt1  (no window manager, no browser)
                  └─ /root/.xinitrc
                       └─ /usr/local/bin/arch-installer-gui
                            └─ python3 -m installer --kiosk
```

tty2–tty6 remain root shells. If X cannot start, tty1 falls back to a login
shell instead of a black screen, and the message it prints says why. When the
window closes normally the console gets the shell back.

## The control panel

Installed into the new system at `/opt/arch-installer/panel`, launched as
`arch-control-panel` or from the desktop menu ("Arch Control Panel"):

| Page | What it does |
| --- | --- |
| Overview | Distro, kernel, CPU, memory and disk usage, uptime, package count, multilib state, update check and full upgrade |
| Software | Same categories as the installer, marked with what is installed; install/remove through `pacman`, repository search, and the multilib switch |
| Services | Every systemd service with active/boot state; start, stop, enable, disable |
| Users | Accounts and groups, password change, add to `wheel`, create an account |
| Network | Interfaces and addresses, active connections, Wi-Fi scan and connect |
| About | What the panel does and does not do |

Privileged work (pacman, systemctl, chpasswd, gpasswd, useradd, nmcli) is
executed as an argument list through `pkexec` (the polkit prompt) with `sudo -n`
as the fallback. The panel runs as your user and never asks for the root
password itself.

## Running it

### Try the interface (any system with Python and Tk, no root)

```bash
cd arch
python3 -m installer --simulate     # the wizard with a simulated laptop, dry run
python3 -m panel --kiosk            # the panel, on any machine with pacman
```

`--simulate` uses a built-in laptop profile and enables dry run, so no disk is
touched and no command is executed (they are logged, not run).

### Inspect this machine / verify the wiring

```bash
python3 -m installer --check       # hardware, layout, package list
python3 -m installer --selftest    # whole pipeline, simulated hardware, dry run
python3 -m installer --dry-run     # real hardware, every command logged only
python3 -m panel --check           # panel's view of the system
```

### Run the installer for real (from the live medium, as root)

```bash
python3 -m installer          # window with real hardware detection
python3 -m installer --kiosk  # full screen, used by the medium
```

### Build the bootable ISO

On an Arch host:

```bash
sudo pacman -S --needed archiso
sudo ./iso/build-iso.sh                 # about 6 GiB free, ~10-20 minutes
```

From any Linux host with Docker:

```bash
./iso/build-in-docker.sh
```

Write it to a stick or boot it:

```bash
sudo dd if=arch-gui-installer-<date>-x86_64.iso of=/dev/sdX bs=4M status=progress oflag=sync
qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd \
  -cdrom arch-gui-installer-<date>-x86_64.iso
```

### Prebuilt image from GitHub

`.github/workflows/build-iso.yml` builds the image on an Ubuntu runner inside an
`archlinux:latest` container (`--privileged`, because `mkarchiso` needs loop
devices) and attaches it to a GitHub Release:

```bash
git tag v1.0.0 && git push origin v1.0.0        # builds and publishes the release
gh workflow run build-iso.yml -f release_tag=v1.0.0   # or run it by hand
```

The `.iso` and its `.sha256` appear under **Releases**, and every run keeps the
same files as a 30-day artifact. The image is deliberately **not** committed:
a multi-gigabyte binary does not belong in git history.

## Requirements

- x86_64 machine with UEFI or legacy BIOS, at least 20 GiB of disk (32 GiB for a
  desktop), 2 GiB of RAM to boot the live medium, wired or wireless internet.
- Secure Boot must be **off** to boot the medium (Arch install media do not
  support it; keys can be enrolled after installation).
- Installation erases the selected disk completely. There is no dual-boot or
  shrink-an-existing-partition mode in this version.

## What is not implemented

- LUKS encryption, LVM, RAID and Btrfs subvolume layouts.
- Installing alongside an existing system (Windows dual boot).
- Secure Boot signing.
- AUR packages.
- Non-x86_64 architectures.
- Mirror selection UI: the live medium's mirrorlist is used (reflector is
  installed, so `reflector --latest 20 --sort rate --save /etc/pacman.d/mirrorlist`
  can be run before starting the installer).

## Safety

- One process: the window and the engine are the same application, nothing
  listens on a port.
- Every field is validated in `config.py` (device paths, hostname, locale, zone,
  keymap, usernames, groups, package names) before it reaches a command; the
  target disk must be one the hardware scan found, and removable disks are not
  offered.
- Commands are executed as argument lists, never through a shell, apart from
  fixed pipelines.
- Passwords are sent to `chpasswd` over stdin and redacted in every log line.
- The erase confirmation is required, the button is a two-step flow (checkbox,
  then **Install** on the review page), and the layout is shown before anything
  is written.
- The medium runs as root and cannot be locked: do not leave it unattended.

## Verification status

Checked in the authoring environment (Windows, Python 3.13, no Arch, no root, no
X display):

- `python3 -m compileall` over `installer/` and `panel/` — passes.
- `python3 -m installer --selftest` — the 11 phases complete against simulated
  hardware with every command in dry run.
- `python3 -m panel --check` — runs (it reports "unknown" for facts that do not
  exist on Windows).
- `bash -n` on every shell script in `iso/`.

**Not** executed here, and therefore unproven: the Tk window on a real display,
partman on real disks, `pacstrap`, the boot loader install, and the mkarchiso
build. Test in a VM before using a real machine:

```bash
qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd \
  -cdrom arch-gui-installer-<date>-x86_64.iso
```

and check, in order: the window appearing, the disk being listed, the layout
preview, and the log at `/var/log/arch-installer.log` after the first phase.
