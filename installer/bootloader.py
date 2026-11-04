"""Boot loader installation: systemd-boot on UEFI, GRUB everywhere.

A menu that reappears after Enter means GRUB failed before the kernel started:
the entry points at a file that is not there, at the wrong partition, or the
firmware booted a GRUB left on another disk.  So this module installs one entry
for the installed system, makes the firmware boot that ESP, and checks the
kernel and initramfs it references really exist before the install finishes.
"""

from __future__ import annotations

import os

from .config import InstallConfig
from .context import Context, uuid_of
from .disk import DiskPlan

GRUB_HEADER = """\
# Written by the Arch Linux installer.
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="Arch"
# No "quiet": a machine that cannot boot has to say why on the console.
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4"
GRUB_CMDLINE_LINUX=""
GRUB_PRELOAD_MODULES="part_gpt part_msdos"
GRUB_DISABLE_RECOVERY=true
# An entry left behind by a system this installation erased must never become
# the default: it fails and drops straight back to the GRUB menu.
GRUB_DISABLE_OS_PROBER=true
GRUB_TERMINAL_INPUT=console
GRUB_GFXMODE=auto
GRUB_GFXPAYLOAD_LINUX=keep
"""

#: Make the firmware boot the entry grub-install has just created.  A machine
#: that already had Linux often keeps an older ESP first in BootOrder, and that
#: GRUB comes up with entries for partitions this installation wiped.
EFI_BOOT_ORDER = r"""
entry=$(efibootmgr | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\)[* ]*Arch.*/\1/p' | head -1)
if [ -n "$entry" ]; then
  others=$(efibootmgr | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\).*/\1/p' | grep -v "^$entry$" | paste -sd, -)
  efibootmgr --bootorder "$entry${others:+,$others}"
fi
"""

LOADER_CONF = """\

default  arch.conf
timeout  4
console-mode max
editor   no
"""


def kernel_images(kernel: str) -> tuple:
    """``(vmlinuz name, initramfs name)`` for a kernel package."""
    return ("/vmlinuz-%s" % kernel, "/initramfs-%s.img" % kernel)


def microcode_initrd(ucode: str) -> str:
    if ucode == "intel-ucode":
        return "/intel-ucode.img"
    if ucode == "amd-ucode":
        return "/amd-ucode.img"
    return ""


def entry_text(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> str:
    root_uuid = uuid_of(ctx, layout.device("root"))
    vmlinuz, initramfs = kernel_images(cfg.kernel)
    lines = ["title   Arch Linux (%s)" % cfg.kernel, "linux   %s" % vmlinuz]
    ucode = microcode_initrd(
        cfg.microcode if cfg.microcode != "auto" else getattr(ctx.hardware, "microcode", "")
    )
    if ucode:
        lines.append("initrd  %s" % ucode)
    lines.append("initrd  %s" % initramfs)
    lines.append(
        "options root=UUID=%s rw rootfstype=%s quiet" % (root_uuid, cfg.root_fs)
    )
    return "\n".join(lines) + "\n"


def _verify_kernel(ctx: Context, cfg: InstallConfig) -> None:
    """The kernel and initramfs the entry names must exist under /boot."""
    boot = ctx.target_path("boot")
    vmlinuz, initramfs = kernel_images(cfg.kernel)
    missing = [
        name for name in (vmlinuz, initramfs) if not os.path.exists(boot + name)
    ]
    if missing and not ctx.dry_run:
        raise RuntimeError(
            "GRUB and systemd-boot would name %s, but %s %s missing from %s. The boot "
            "menu would reappear after every entry."
            % (
                ", ".join(missing),
                "they are" if len(missing) > 1 else "it is",
                "both" if len(missing) > 1 else "",
                boot,
            )
        )


def install_systemd_boot(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.chroot("bootctl --esp-path=/boot install")
    ctx.write_target("boot/loader/loader.conf", LOADER_CONF)
    ctx.write_target("boot/loader/entries/arch.conf", entry_text(ctx, cfg, layout))
    _verify_kernel(ctx, cfg)


def install_grub(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.write_target("etc/default/grub", GRUB_HEADER)
    if cfg.efi:
        boot = ctx.target_path("boot")
        if not ctx.dry_run and not os.path.ismount(boot):
            raise RuntimeError(
                "the EFI system partition is not mounted at %s, so grub-install would "
                "write its files into the root file system and the machine would not "
                "boot" % boot
            )
        # grub-install writes an EFI variable, which needs efivarfs writable
        # inside the chroot; arch-chroot does not mount it for us.
        ctx.chroot(
            "mountpoint -q /sys/firmware/efi/efivars || "
            "mount -t efivarfs efivarfs /sys/firmware/efi/efivars",
            check=False,
        )
        ctx.chroot(
            "grub-install --target=x86_64-efi --efi-directory=/boot "
            "--bootloader-id=Arch --recheck"
        )
        ctx.chroot(EFI_BOOT_ORDER, check=False)
        ctx.chroot("umount /sys/firmware/efi/efivars 2>/dev/null || true", check=False)
    else:
        ctx.chroot("grub-install --target=i386-pc --recheck %s" % layout.disk)
    ctx.chroot("grub-mkconfig -o /boot/grub/grub.cfg")
    _verify_kernel(ctx, cfg)
    if ctx.dry_run:
        return
    config = ctx.read_target("boot/grub/grub.cfg")
    if not config:
        raise RuntimeError("grub-mkconfig wrote no /boot/grub/grub.cfg")
    if "root=UUID=" not in config:
        ctx.log.log(
            "grub.cfg does not boot by UUID; check /boot/grub/grub.cfg before rebooting",
            "warn",
        )


def install(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    choice = cfg.resolved_bootloader
    ctx.log.log("installing the %s boot loader" % choice, "step")
    if choice == "systemd-boot":
        install_systemd_boot(ctx, cfg, layout)
    else:
        install_grub(ctx, cfg, layout)
