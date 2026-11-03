"""Boot loader installation: systemd-boot on UEFI, GRUB everywhere."""

from __future__ import annotations

from .config import InstallConfig
from .context import Context, uuid_of
from .disk import DiskPlan

GRUB_HEADER = """\
# Written by the Arch Linux installer.
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="Arch"
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"
GRUB_CMDLINE_LINUX=""
GRUB_PRELOAD_MODULES="part_gpt part_msdos"
GRUB_TIMEOUT_STYLE=menu
GRUB_TERMINAL_INPUT=console
GRUB_GFXMODE=auto
GRUB_GFXPAYLOAD_LINUX=keep
GRUB_DISABLE_RECOVERY=true
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


def _esp_is_mounted(ctx: Context) -> bool:
    return True  # the disk phase mounts the ESP at /boot, or there is none


def install_systemd_boot(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.chroot("bootctl --esp-path=/boot install")
    ctx.write_target("boot/loader/loader.conf", LOADER_CONF)
    ctx.write_target("boot/loader/entries/arch.conf", entry_text(ctx, cfg, layout))


def entry_text(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> str:
    root_uuid = uuid_of(ctx, layout.device("root"))
    vmlinuz, initramfs = kernel_images(cfg.kernel)
    lines = ["title   Arch Linux (%s)" % cfg.kernel, "linux   %s" % vmlinuz]
    ucode = microcode_initrd(cfg.microcode if cfg.microcode != "auto" else getattr(ctx.hardware, "microcode", ""))
    if ucode:
        lines.append("initrd  %s" % ucode)
    lines.append("initrd  %s" % initramfs)
    lines.append(
        "options root=UUID=%s rw rootfstype=%s quiet" % (root_uuid, cfg.root_fs)
    )
    return "\n".join(lines) + "\n"


def install_grub(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.write_target("etc/default/grub", GRUB_HEADER)
    if cfg.efi:
        # grub-install writes an EFI variable, which needs efivarfs writable
        # inside the chroot; arch-chroot does not mount it for us.
        ctx.chroot("mount -t efivarfs efivarfs /sys/firmware/efi/efivars 2>/dev/null || true", check=False)
        ctx.chroot(
            "grub-install --target=x86_64-efi --efi-directory=/boot "
            "--bootloader-id=Arch --recheck"
        )
        ctx.chroot("umount /sys/firmware/efi/efivars 2>/dev/null || true", check=False)
    else:
        ctx.chroot("grub-install --target=i386-pc --recheck %s" % layout.disk)
    ctx.chroot("grub-mkconfig -o /boot/grub/grub.cfg")


def install(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    choice = cfg.resolved_bootloader
    ctx.log.log("installing the %s boot loader" % choice, "step")
    if choice == "systemd-boot":
        install_systemd_boot(ctx, cfg, layout)
    else:
        install_grub(ctx, cfg, layout)
