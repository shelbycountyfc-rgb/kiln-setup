# Architecture

## Process model

```
tty1 autologin (root)
   └─ /usr/local/bin/arch-installer-session
        └─ xinit ──► Xorg :0                no window manager, no browser
             └─ /root/.xinitrc
                  └─ /usr/local/bin/arch-installer-gui
                       └─ python3 -m installer
                            ├── hardware.detect()      once at start-up
                            ├── disk.plan_layout()     per disk/options change
                            └── orchestrator.Session   one background thread
                                   └── Context (log + run + chroot + mount)
                                          └── subprocess → pacman, sgdisk, mkfs…
```

One installation per boot. The window is a single-process Tk application: the
`Installer` object owns the widget tree, and a `Session` is created when the
progress page opens. The session runs on a daemon thread and publishes a
snapshot (percent, phase states, current sub-step, log tail); the Tk main loop
polls it every 400 ms with `after()`, so the worker thread never touches a
widget.

The collected answers live in one plain dict whose keys match what
`InstallConfig.from_dict()` reads, so validation has a single entry point and
the engine never learns about widgets.

`Context` is the only place that spawns processes. It centralises:

- command logging with credential redaction,
- `dry_run` (every command succeeds without executing; used by `--simulate`,
  `--dry-run` and `--selftest`),
- `arch-chroot` execution (falling back to plain `chroot`),
- mount bookkeeping so `finalize()` can unwind everything it mounted.

## Wizard step → installation guide → module

| Step | Installation guide section | Module |
| --- | --- | --- |
| Welcome | Pre-installation | `hardware.detect` |
| Hardware | Verify the boot mode | `hardware.detect` |
| Disk | Partition the disks, format, mount | `disk.plan_layout`, `disk.apply_layout` |
| System | Time, localization, network configuration | `system.configure`, `network.write_profiles` |
| Software | Install essential packages | `catalog`, `packages.required_packages` |
| Review | — | `InstallConfig.validate` |
| Install | Install essential packages → Reboot | `orchestrator`, `bootloader` |
| Finish | Reboot | `Installer.power` |

## Installation pipeline

| Phase | Weight | Actions |
| --- | --- | --- |
| prepare | 3 | check the medium's tools, enable live `[multilib]`, size the disk, plan layout and package list |
| disk | 10 | `swapoff`, unmount anything on the disk, `sgdisk --zap-all`, create GPT partitions, `mkfs`/`mkswap`, mount at `/mnt` |
| pacstrap | 46 | `pacstrap -K /mnt` base + kernel + firmware + microcode + filesystem tools + boot loader + desktop |
| extras | 6 | write the target's `pacman.conf` (with `[multilib]` when enabled), `pacman -Sy`, install `lib32-*`/Steam/Wine inside the chroot |
| fstab | 2 | `genfstab -U /mnt`, refuse to continue on an empty result |
| locale | 5 | `ln -sf` zoneinfo, `hwclock --systohc`, `locale.gen` + `locale-gen`, `locale.conf`, `vconsole.conf`, hostname/hosts |
| users | 4 | `chpasswd` for root over stdin, `useradd` + `chpasswd` for the daily account, `sudo` drop-in for `wheel` |
| initramfs | 4 | `mkinitcpio -P` |
| bootloader | 12 | systemd-boot (`bootctl` + loader entries) or GRUB (`grub-install`, `grub-mkconfig`) |
| services | 5 | Wi-Fi profile, `systemctl enable NetworkManager`, display manager, optional `bluetooth`/`cups`/`docker`/`libvirtd`/`tlp`, `fstrim.timer` |
| finish | 3 | install the control panel, copy the log into the target, `swapoff`, `umount -R /mnt` |

Weights add up to 100, so the bar is a weighted estimate rather than a timer.

## Partition layout

`disk.plan_layout()` works in bytes and emits 1 MiB-aligned partitions:

```
UEFI:  [1] EFI system partition (ef00)  /boot   1 GiB (adjustable 0.5–2)
       [2] swap (8200)                  [swap]  RAM-sized, capped
       [3] root (8304)                  /       remainder, or 45 % with /home
       [4] home (8302)                  /home   only when separate home is on

BIOS:  [1] BIOS boot (ef02) 2 MiB, [2] swap, [3] root, [4] home
```

`apply_layout()` unmounts what is there, zaps every signature on the disk,
creates the table with `sgdisk -n/-t/-c`, re-reads it with `partprobe` and
`udevadm settle`, formats each partition, mounts root at `/mnt`, the ESP at
`/mnt/boot`, `/home`, and activates swap. Formatting happens only after the
table is complete, so an interrupted run leaves recognizable pieces rather than
a half-written filesystem on a disk the kernel still believes is partitioned.

The ESP is mounted at `/boot` (the guide's example), which is what both
systemd-boot and GRUB expect for kernel and initramfs placement.

## Multilib

```
Software page switch ──► cfg.multilib
        │
        ├── false: validation refuses steam/wine/lib32-*, [multilib] stays commented
        │
        └── true:
             prepare    : packages.enable_live_multilib()  (live overlay only)
             extras     : packages.write_target_conf()      /mnt/etc/pacman.conf
                          pacman -Sy
                          pacman -S lib32-* steam wine
             bootloader : unaffected
             panel      : core.multilib_enabled() ← [multilib] in /etc/pacman.conf
```

`set_multilib()` is the single function that turns the section on or off; it is
used by the installer (target config) and by the panel (live file through
`pkexec tee`), so both agree on what "enabled" looks like.

## Package picker

```
catalog.GROUPS ──► Software page category list ──► checkbutton list ──► cfg.packages
                           │                                              │
pacman -Ss <term> ─────────┘ (search, background thread)                   │
                                                                           ▼
                            required_packages() ──► pacstrap (no lib32)
                            multilib_packages() ──► chroot pacman (lib32)
```

Every catalogue name is lowercase, in the official repositories, and passes
`PACKAGE_RE` before it is placed in a command. The search results are parsed
from `pacman -Ss` output (`repo/name version` + description) and limited to 60
entries.

## Boot loader

- **systemd-boot**: `bootctl --esp-path=/boot install`, `loader/loader.conf`,
  `loader/entries/arch.conf` with `root=UUID=<fs uuid>`, `rootfstype=`, and the
  microcode initrd before the initramfs (the order the guide requires).
- **GRUB (UEFI)**: `efivarfs` is mounted inside the chroot for the duration of
  `grub-install --target=x86_64-efi --efi-directory=/boot --bootloader-id=Arch`,
  then `grub-mkconfig`.
- **GRUB (BIOS)**: `grub-install --target=i386-pc --recheck /dev/sdX` with the
  2 MiB BIOS boot partition created by the layout planner.

## Control panel

```
python3 -m panel
  └── Panel (Tk)
        ├── core.services() / users() / interfaces()   read-only facts
        ├── Panel.job(fn, done) → worker thread → after(0, done)
        └── PanelContext.elevated(["pacman", "-S", ...])
                 └── pkexec  (polkit prompt)  |  sudo -n
```

The panel reuses `installer.gui.theme`, `installer.gui.widgets`,
`installer.catalog`, `installer.packages` and `installer.options`, which is why
it looks and behaves like the installer without a second copy of either. It is
installed into the target as part of the finish phase
(`system.install_panel`), together with `/usr/local/bin/arch-control-panel` and
`/usr/share/applications/arch-control-panel.desktop`.

Pages never run a command inline: `Panel.job` executes on a worker thread, an
exception is delivered to the callback instead of raised, and the pages render
the message in their own banner. A missing tool prints a reason; it does not
close the window.

## Security boundaries

- No network service in either application: the UI and the engine are the same
  process.
- `InstallConfig.validate()` is the single gate between the form and the shell:
  device paths, hostname, locale, timezone, keymap, usernames, groups and
  package names are pattern-checked, and the disk must be one the hardware scan
  found.
- Commands are argument lists. The only shell strings are fixed pipelines, and
  the values inside them come from validated fields.
- Passwords live in entry widgets and are piped to `chpasswd` on stdin; `Context`
  redacts `password=`, `chpasswd` lines and `--password` before logging.
- The panel runs unprivileged and elevates single commands through polkit; it
  never writes to `/etc` except `pacman.conf` through `tee`, after authorisation.
- The installation session runs as root because it partitions disks; tty2–tty6
  give the same access, so the medium must not be left unattended.
