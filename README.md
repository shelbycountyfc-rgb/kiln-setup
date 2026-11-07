# Kiln Setup + Control Panel

> **Unofficial project.** Kiln Setup is not affiliated with, endorsed by, or
> supported by the Arch Linux project, and this is not an official Arch Linux
> installation medium. "Arch Linux" is a trademark of the Arch Linux project and
> is used here only to say what this software installs.

A Windows-Setup-style graphical installer that installs Arch Linux, and a control
panel that manages the installed system afterwards. Both are native desktop
applications (Python + Tk) — no browser, no local web server, nothing listening
on a port.

**[Download the installer image](https://github.com/shelbycountyfc-rgb/kiln-setup/releases/latest)**
— built by CI and attached to the release together with its SHA-256 checksum.

The installer follows the [official installation guide](https://wiki.archlinux.org/title/Installation_guide)
step for step: partition, format, mount, `pacstrap`, `genfstab`, `arch-chroot`
configuration, boot loader. You end up with a normal Arch system, maintained with
`pacman`.

```
installer/            the installation wizard
  __main__.py         CLI: --simulate, --dry-run, --kiosk, --check, --selftest
  brand.py            the project name, ISO label and the trademark disclaimer
  context.py          logging, command execution, target files, redaction
  hardware.py         CPU/RAM/firmware/GPU/disk/network detection
  disk.py             GPT layout planning, sgdisk, mkfs, mounts
  catalog.py          the package catalogue (categories, multilib flags)
  packages.py         pacman: pacstrap, [multilib], repository search
  aur.py              AUR recipes: dependency order, makepkg build user
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
| Disk | Target disk (internal or removable), filesystem (ext4/Btrfs/XFS/f2fs/bcachefs), swap, EFI partition size, separate `/home`, live layout preview, erase confirmation |
| System | Hostname, time zone, locale, keyboard, boot loader, root password, daily account, optional Wi-Fi |
| Software | Desktop, kernel, microcode, **firmware set (matched to this machine or complete)**, **multilib switch**, package picker with categories and repository search |
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
        └─ /usr/local/bin/kiln-setup-session
             └─ xinit :0 vt1  (no window manager, no browser)
                  └─ /root/.xinitrc
                       └─ /usr/local/bin/kiln-setup
                            └─ python3 -m installer --kiosk
```

tty2–tty6 remain root shells. If X cannot start, tty1 falls back to a login
shell instead of a black screen, and the message it prints says why. When the
window closes normally the console gets the shell back.

## The control panel

Installed into the new system at `/opt/kiln-setup/panel`, launched as
`kiln-panel` or from the desktop menu ("Arch Control Panel"):

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

### If selecting the boot entry returns to the boot menu

That bounce is the firmware refusing the medium and falling through to the next
device, usually the installed disk's own GRUB.  Check these in order:

- **Secure Boot.** The medium's UEFI loader is systemd-boot, which is not signed;
  with Secure Boot on the firmware ignores it.  Turn Secure Boot off for the
  installation, then turn it back on afterwards if you want it.
- **Boot mode.** The same stick carries a BIOS (syslinux) and a UEFI
  (systemd-boot) entry.  If the firmware is set to legacy/CSM it uses the first;
  if it is set to UEFI it uses the second.  Pick the stick explicitly in the
  boot menu rather than the internal disk.
- **How it was written.** Write the image to the whole device, not to a
  partition: run `dd if=kiln-setup-*.iso of=/dev/sdX bs=4M status=progress
  oflag=sync` as root.  Writing it to `/dev/sdX1` leaves the UEFI system
  partition that the firmware looks for on the stick out of the picture.
- **"No bootable option or device was found".** That is EFI firmware that only
  reads the boot catalogue finding no EFI entry.  Builds from revision 3 on
  register a UEFI El Torito image as well as the ESP partition, so this image
  boots from a disc under EFI; on an older image, turn EFI off in the VM
  settings or boot the stick as a disk.

## If the menu entry opens nothing

A desktop entry has no terminal, so a failure there is silent by default. It is
not silent here: the launcher writes to `~/.local/state/kiln-panel.log`, and a
failure ends in a dialog naming the reason (`zenity` or `kdialog`, whichever the
desktop has).

```bash
kiln-panel --doctor    # python3, Tk, /opt/kiln-setup, the display, the log path
kiln-panel             # from a terminal: the traceback appears here
```

The three causes seen so far:

- `python` or `tk` is not installed — `sudo pacman -S python tk`. Both are part
  of the base set the installer installs, so this means the system predates them.
- `/opt/kiln-setup` is missing because the medium could not copy it at the end of
  the installation. `/var/log/kiln-setup.log` says so, and with no panel there
  is now no menu entry at all instead of one that starts nothing.
- No `DISPLAY`: the entry was started from a root shell on a text console, where
  a Tk window has nowhere to appear.

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
sudo dd if=kiln-setup-<date>-r1-x86_64.iso of=/dev/sdX bs=4M status=progress oflag=sync
qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd \
  -cdrom kiln-setup-<date>-r1-x86_64.iso
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
- Installation erases the selected disk by default. The alongside mode keeps a
  system that is already installed, and can shrink one of its partitions first
  to make room.

## Storage, encryption and dual boot

The Storage step stacks the layers the way Arch does, and shows the result on the
Disk page before anything is written:

```
disk(s) -> mdadm array -> LUKS2 container -> LVM volume group -> ext4/xfs/btrfs -> subvolumes
```

- **Encryption** — LUKS2 with argon2id on the root partition. `/etc/crypttab`
  and the `encrypt` initramfs hook are written for you. By default `/boot` stays
  on the EFI system partition, which the boot loader reads before anything is
  unlocked; **Encrypt /boot** puts the kernel and the initramfs inside the
  container instead, so GRUB has to open it first (`GRUB_ENABLE_CRYPTODISK=y`,
  cryptodisk modules, and the passphrase typed twice at boot).
- **Unlocking** — a passphrase, a **key file** (a random key added to the
  container and carried inside the initramfs, so boot stops asking), or the
  **TPM 2.0** (`systemd-cryptenroll`, PCR 7, with `sd-encrypt` in the initramfs).
  The passphrase is always kept as the way back in.
- **LVM** — `pvcreate`/`vgcreate`/`lvcreate` over the root partition (or over the
  LUKS container). The boot entry names `/dev/vg0/root`.
- **RAID** — mdadm RAID 1, 5 or 10 across the whole root partitions of 2, 3+ or 4+
  disks. The array is assembled by the `mdadm_udev` hook before the container is
  opened; GRUB on a legacy machine is installed to every member.
- **Btrfs** — `@`, `@home`, `@log`, `@pkg` and `@snapshots` subvolumes, mounted
  `compress=zstd:3,noatime`, with `rootflags=subvol=@` in the boot entry.
- **Alongside** — keeps a system that is already installed, Windows included. The
  existing EFI system partition is found and mounted **without formatting**, one
  partition is created in the free space, and `os-prober` is enabled so GRUB
  writes the other system's menu entry.
- **Making room** — when there is no free space, one existing partition can be
  shrunk first: the filesystem is resized (ext2/3/4, Btrfs, NTFS), then the table
  entry is rewritten with `sfdisk` at the same start sector, which keeps its
  number, type and PARTUUID. XFS, FAT, swap and anything already mounted are
  refused rather than guessed at.
- **Kernel parameters and hibernation** — extra parameters are written into the
  boot entry verbatim; hibernation sizes the swap partition to hold memory and
  adds `resume=UUID=` plus the `resume` hook.
- **Mirrors** — the Network step can run `reflector` for a country and write the
  fastest mirrors into `/etc/pacman.d/mirrorlist`, keeping the shipped list when
  nothing answers.

## Hardening

- **Lockdown, kexec and eBPF** — the kernel boots with `lockdown=integrity`,
  kexec is disabled so no second kernel can be started behind the running one,
  and unprivileged BPF is off with the JIT hardened against being used to build
  code.
- **Attestation** — with an encrypted disk the key stays bound to the TPM
  (PCR 7), and `kiln-hardening.service` reports at every boot whether the
  lockdown, kexec, BPF, Secure Boot and TPM state still match; a check that no
  longer holds fails the unit instead of passing silently.
- **Signed kernel and modules** — `sbctl` signs the kernel, the initramfs and
  the boot loader; every module is signed with a key the machine keeps, and a
  pacman hook re-signs after a kernel or DKMS update. Enforcing module
  signatures (`module.sig_enforce=1`) is left off: Arch ships modules unsigned
  by default, and out-of-tree drivers built later would refuse to load until
  they are signed too.

## Secure Boot, the AUR and other architectures

- **Secure Boot** — `sbctl` creates keys, signs the kernel, the initramfs and the
  boot loader, and signs them again on kernel updates. Enrolling the keys needs
  the firmware in setup mode; if the firmware refuses, the log says so and names
  the one command to run from the installed system.
- **AUR** — names listed in Software are cloned and built inside the new system
  by an unprivileged `makepkg` user, after `base-devel`. Dependencies that are
  themselves AUR packages are resolved and built first. Nothing is reviewed: an
  AUR package runs whatever its recipe says.
- **Architectures** — x86_64 and aarch64. The installer refuses a cross-arch
  install instead of producing a system whose packages and EFI loader do not
  match; the medium has to be built for the architecture it installs
  (`arch` in `profiledef.sh`, `packages.<arch>`).

## Other machines

Most of the choices below exist because a machine is rarely the one the
installer was developed on.

- **Removable media** — internal disks are listed first, and an external SSD, a
  USB stick or the eMMC/SD card of a small laptop is offered too, marked on the
  page. The Disk page warns that taking it out unmounts the running root.
- **Reinstall into a partition** — the third disk mode formats one existing
  partition as the new root and mounts another existing partition as `/home`
  without touching it, so the files in it survive. Every other partition is left
  alone and `os-prober` keeps the other systems in the menu.
- **Filesystems** — ext4, Btrfs, XFS, f2fs (the wear-levelled choice for eMMC and
  SD cards) and bcachefs, each with the tools its root needs. Hibernating into a
  swap file is refused on f2fs and bcachefs, because their layout cannot be read
  before they are mounted.
- **Unified kernel image** — one signed `.efi` holding the kernel, the initramfs
  and the command line, built by `mkinitcpio` and found by systemd-boot with no
  loader entry. It needs UEFI and systemd-boot, and it removes the second
  passphrase prompt an encrypted `/boot` forces out of GRUB.
- **Dual boot** — "keep the hardware clock in local time" stops Windows from
  showing the wrong hour and rewriting the clock on every shutdown.
- **Snapshots** — a Btrfs root gets snapper's timeline for the `@snapshots`
  subvolume that is already created, and with GRUB a `grub-btrfs` menu entry per
  snapshot.
- **Network services** — a default-deny `nftables` filter, and an SSH server
  that starts at boot. The filter opens port 22 only when the server was asked
  for.
- **Mirrors** — when the Network page picked a country, the installed system
  keeps `reflector.timer` on that country instead of the list it was born with.
- **Locales and layouts** — extra locales are generated beside the main one, and
  the desktop keyboard layout can differ from the console layout.
- **aarch64** — needs UEFI: legacy BIOS is x86 only, and validation says so
  instead of producing a medium that cannot boot.

### Answer files

Every answer can live in a JSON file, so one installation can be repeated on
another machine without clicking through the window:

```bash
python3 -m installer --write-answers kiln-answers.json   # this machine's answers
# fill in rootPassword and the user password, then:
python3 -m installer --answers kiln-answers.json --dry-run   # prove it first
python3 -m installer --answers kiln-answers.json             # install for real
```

The file is validated exactly like the window's answers, a rejected file names
every problem at once, and a file that other users can read is refused: it holds
the root password. `--answers` runs the same pipeline as the window, so the log
and the failure handling are the same.

## What is not implemented

- Reviewing or signing AUR recipes, or third-party repositories.
- Shrinking an XFS filesystem: no tool can make XFS smaller, so such a partition
  is refused rather than reformatted.  ext2/3/4, Btrfs, NTFS, FAT and swap
  partitions can be shrunk; FAT through parted, swap by being made again.
- Unlocking an encrypted `/boot` with the TPM alone: GRUB cannot ask the TPM for
  the key, so it still asks for the passphrase.  The two choices are allowed
  together, and the TPM then removes the second prompt the initramfs would ask.
- Enrolling Secure Boot keys without the firmware in setup mode: the installer
  hands the keys to the firmware from the medium when it is in setup mode, and
  otherwise prints the one command that finishes the job after a reboot.

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
- A failed installation copies its log into the new system, closes the
  container and unmounts the target before it stops, and the progress page
  offers a **Start over** button that keeps every answer.
- Success is only reported after the target has been synced and unmounted: a
  target that stays busy fails the installation instead.

## Verification status

Checked by `tests/run.sh` on every push (`.github/workflows/checks.yml`), and the
image itself is booted on BIOS and UEFI in QEMU after it is built
(`tests/boot-smoke.py`):

- `python3 -m compileall` over `installer/` and `panel/` — passes.
- `python3 -m installer --selftest` — all 13 phases complete against simulated
  hardware with every command in dry run.
- `python3 -m panel --check` — runs (it reports "unknown" for facts that do not
  exist on Windows).
- `bash -n` on every shell script in `iso/`.

The 1.2.0 image was then installed for real in VirtualBox — LUKS2, LVM, Btrfs
subvolumes and KDE on legacy BIOS, from partition table to a logged-in Plasma
desktop — which is how the BIOS formatting and GRUB cryptodisk defects in 1.2.0
were found and fixed. Not executed anywhere yet: the Tk window on a physical
display, partition shrinking on a real disk, the key-file and TPM unlock paths,
hibernation resume, and the mirror chooser against live mirrors. Test in a VM
before using a real machine:

```bash
qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd \
  -cdrom kiln-setup-<date>-r1-x86_64.iso
```

and check, in order: the window appearing, the disk being listed, the layout
preview, and the log at `/var/log/kiln-setup.log` after the first phase.
