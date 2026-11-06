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
from .system import KEYFILE_PATH

GRUB_HEADER = """\
# Written by Kiln Setup, an unofficial installer for Arch Linux.
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_TIMEOUT_STYLE=menu
GRUB_DISTRIBUTOR="Kiln"
# No "quiet": a machine that cannot boot has to say why on the console.
GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4"
GRUB_CMDLINE_LINUX=""
GRUB_PRELOAD_MODULES="part_gpt part_msdos"
GRUB_DISABLE_RECOVERY=true
GRUB_DISABLE_OS_PROBER={os_prober}
GRUB_TERMINAL_INPUT=console
GRUB_GFXMODE=auto
GRUB_GFXPAYLOAD_LINUX=keep
"""

#: Make the firmware boot the entry grub-install has just created.  A machine
#: that already had Linux often keeps an older ESP first in BootOrder, and that
#: GRUB comes up with entries for partitions this installation wiped.
EFI_BOOT_ORDER = r"""
entry=$(efibootmgr | sed -n 's/^Boot\([0-9A-Fa-f]\{4\}\)[* ]*Kiln.*/\1/p' | head -1)
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

#: The EFI binaries Secure Boot has to cover, per architecture.
EFI_SIGN_TARGETS = {
    "x86_64": (
        "/boot/EFI/systemd/systemd-bootx64.efi",
        "/boot/EFI/BOOT/BOOTX64.EFI",
        "/boot/EFI/Kiln/grubx64.efi",
    ),
    "aarch64": (
        "/boot/EFI/systemd/systemd-bootaa64.efi",
        "/boot/EFI/BOOT/BOOTAA64.EFI",
        "/boot/EFI/Kiln/grubaa64.efi",
    ),
}


def kernel_images(kernel: str) -> tuple:
    """``(vmlinuz name, initramfs name)`` for a kernel package."""
    return ("/vmlinuz-%s" % kernel, "/initramfs-%s.img" % kernel)


def microcode_initrd(ucode: str) -> str:
    if ucode == "intel-ucode":
        return "/intel-ucode.img"
    if ucode == "amd-ucode":
        return "/amd-ucode.img"
    return ""


def efi_target(cfg: InstallConfig) -> str:
    """The ``grub-install --target`` value for the architecture being installed."""
    return "arm64-efi" if cfg.arch == "aarch64" else "x86_64-efi"


def kernel_options(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> str:
    """The kernel command line the installed system needs to find its root.

    An encrypted root is named by the UUID of the LUKS container, not by the
    filesystem inside it: at boot time that filesystem does not exist yet.  An
    LVM root is named by its volume group, and a Btrfs root by the subvolume.
    """
    options = []
    if layout.encryption == "luks":
        if not layout.luks_uuid:
            ctx.log.log(
                "the LUKS container has no UUID recorded; the boot entry cannot name it",
                "warn",
            )
        if cfg.unlock == "tpm2":
            # sd-encrypt asks systemd-cryptsetup, which asks the TPM first and
            # falls back to the passphrase when the measurement does not match.
            options.append(
                "rd.luks.name=%s=%s" % (layout.luks_uuid, layout.crypt_name)
            )
            options.append("rd.luks.options=tpm2-device=auto")
        else:
            options.append(
                "cryptdevice=UUID=%s:%s" % (layout.luks_uuid, layout.crypt_name)
            )
            if cfg.unlock == "keyfile":
                # The encrypt hook takes its key from the command line, not from
                # /etc/crypttab: without cryptkey it still stops to ask.
                options.append("cryptkey=rootfs:%s" % KEYFILE_PATH)
    if layout.volumes == "lvm":
        options.append("root=/dev/%s/%s" % (layout.vg, layout.lv))
    else:
        root = layout.mapper or layout.md_device or layout.device("root")
        options.append("root=UUID=%s" % uuid_of(ctx, root))
    options.append("rw")
    options.append("rootfstype=%s" % cfg.root_fs)
    if layout.subvolumes:
        options.append("rootflags=subvol=@")
    if cfg.hibernate and layout.swap is not None:
        # The kernel has to know which swap holds the image it is meant to resume.
        uuid = layout.swap_uuid or uuid_of(ctx, layout.swap.device)
        if uuid:
            options.append("resume=UUID=%s" % uuid)
    options.extend(cfg.kernel_param_list)
    return " ".join(options)


def entry_text(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> str:
    vmlinuz, initramfs = kernel_images(cfg.kernel)
    lines = ["title   Arch Linux (%s)" % cfg.kernel, "linux   %s" % vmlinuz]
    ucode = microcode_initrd(
        cfg.microcode if cfg.microcode != "auto" else getattr(ctx.hardware, "microcode", "")
    )
    if ucode:
        lines.append("initrd  %s" % ucode)
    lines.append("initrd  %s" % initramfs)
    lines.append("options %s quiet" % kernel_options(ctx, cfg, layout))
    return "\n".join(lines) + "\n"


def _fix_grub_paths(ctx: Context, cfg: InstallConfig) -> None:
    """Make every kernel path in grub.cfg exist where GRUB will look for it.

    grub-mkconfig writes the kernel paths from how /boot happened to be mounted
    while it ran (``/vmlinuz-linux`` when /boot is its own file system,
    ``/boot/vmlinuz-linux`` when it is not).  If that disagrees with the file
    system GRUB actually reads, every entry fails with "file not found" and the
    menu simply reappears.  Rewrite the paths that do not resolve.
    """
    if ctx.dry_run:
        return
    text = ctx.read_target("boot/grub/grub.cfg")
    if not text:
        return
    # GRUB reads /boot from the ESP unless it had to unlock the container first.
    base = (
        ctx.target_path("boot") if (cfg.efi and not cfg.encrypt_boot) else ctx.target
    )
    changes = []
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        for keyword in ("linux", "linuxefi", "initrd", "initrdefi"):
            if not stripped.startswith(keyword + " "):
                continue
            fields = stripped.split()
            if len(fields) >= 2:
                path = fields[1].strip('"')
                if not os.path.exists(os.path.join(base, path.lstrip("/"))):
                    alternative = (
                        path[len("/boot"):]
                        if path.startswith("/boot/")
                        else "/boot" + path
                    )
                    if os.path.exists(os.path.join(base, alternative.lstrip("/"))):
                        changes.append((path, alternative))
                        line = line.replace(path, alternative, 1)
            break
        lines.append(line)
    if not changes:
        return
    ctx.write_target("boot/grub/grub.cfg", "\n".join(lines) + "\n")
    for old, new in changes:
        ctx.log.log(
            "grub.cfg pointed at %s, which is not in the file system GRUB reads; "
            "changed it to %s" % (old, new),
            "warn",
        )


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


def sign_boot_files(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    """Sign the kernel and the boot loader so Secure Boot accepts them.

    sbctl keeps the keys and remembers what it has signed, so later kernel
    updates are re-signed by its pacman hook.  Enrolling the keys needs the
    firmware in setup mode: that cannot be forced from here, so a refusal is
    reported with the one command that finishes the job after a reboot.
    """
    ctx.log.log("signing the boot files with sbctl", "step")
    ctx.chroot("command -v sbctl >/dev/null 2>&1 || exit 3", check=False)
    ctx.chroot("sbctl status | grep -q 'Setup Mode:.*Enabled' || sbctl create-keys", check=False)
    kernel = "/boot/vmlinuz-%s" % cfg.kernel
    targets = [kernel]
    if layout.encryption == "luks":
        # The initramfs lives on the unencrypted ESP, but it is the file that
        # unlocks the disk: signing it closes the same gap as signing the kernel.
        targets.append("/boot/initramfs-%s.img" % cfg.kernel)
    targets.extend(EFI_SIGN_TARGETS.get(cfg.arch, EFI_SIGN_TARGETS["x86_64"]))
    for target in targets:
        ctx.chroot(
            'if [ -f %s ]; then sbctl sign --save %s; fi'
            % (shlex_quote(target), shlex_quote(target)),
            check=False,
        )
    result = ctx.chroot("sbctl enroll-keys --microsoft", check=False)
    if getattr(result, "returncode", 1) != 0:
        ctx.log.log(
            "the firmware refused the new keys. Reboot into the firmware setup, clear "
            "the Secure Boot keys (setup mode), then run 'sbctl enroll-keys --microsoft' "
            "once from the installed system.",
            "warn",
        )


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def install_systemd_boot(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.chroot("bootctl --esp-path=/boot install")
    ctx.write_target("boot/loader/loader.conf", LOADER_CONF)
    ctx.write_target("boot/loader/entries/arch.conf", entry_text(ctx, cfg, layout))
    _verify_kernel(ctx, cfg)


def grub_defaults(cfg: InstallConfig, layout: DiskPlan | None = None) -> str:
    """The ``/etc/default/grub`` this layout needs.

    On legacy BIOS there is no EFI system partition, so ``/boot`` lives inside
    whatever the storage stack built on the disk: GRUB itself has to assemble
    the array, unlock the container and enter the volume group before it can
    find a kernel.  That needs the modules for each layer, and cryptodisk for an
    encrypted root -- which also means the passphrase is typed twice at boot,
    once by GRUB and once by the initramfs.  On UEFI the kernel and the initramfs
    are on the unencrypted ESP, so none of this applies.
    """
    header = GRUB_HEADER.replace("{os_prober}", "false" if cfg.alongside else "true")
    # GRUB has to open the container itself when the kernel lives inside it: on
    # legacy BIOS /boot cannot be anywhere else, and with an encrypted /boot the
    # menu and the kernel are both on the encrypted file system.
    cryptodisk = cfg.encrypted and (not cfg.efi or cfg.encrypt_boot)
    modules = ["part_gpt", "part_msdos"]
    if cfg.uses_raid:
        modules += ["mdraid09", "mdraid1x"]
    if cfg.uses_lvm:
        modules.append("lvm")
    if cryptodisk:
        modules += ["cryptodisk", "luks"]
    header = header.replace(
        'GRUB_PRELOAD_MODULES="part_gpt part_msdos"',
        'GRUB_PRELOAD_MODULES="%s"' % " ".join(modules),
    )
    if cryptodisk:
        header = header.replace(
            "GRUB_TERMINAL_INPUT=console",
            "GRUB_ENABLE_CRYPTODISK=y\nGRUB_TERMINAL_INPUT=console",
        )
    if layout is not None:
        # The initramfs finds an encrypted root only if the command line names
        # the container; grub-mkconfig writes root= itself, the rest is ours.
        # Only the container is added: grub-mkconfig already writes root= and
        # the Btrfs rootflags for the file system it found.
        extra = []
        if layout.encryption == "luks":
            if cfg.unlock == "tpm2":
                extra.append(
                    "rd.luks.name=%s=%s" % (layout.luks_uuid, layout.crypt_name)
                )
                extra.append("rd.luks.options=tpm2-device=auto")
            else:
                extra.append(
                    "cryptdevice=UUID=%s:%s" % (layout.luks_uuid, layout.crypt_name)
                )
                if cfg.unlock == "keyfile":
                    extra.append("cryptkey=rootfs:%s" % KEYFILE_PATH)
        if cfg.hibernate and layout.swap_uuid:
            extra.append("resume=UUID=%s" % layout.swap_uuid)
        extra.extend(cfg.kernel_param_list)
        if extra:
            header = header.replace(
                'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4"',
                'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=4 %s"' % " ".join(extra),
            )
    return header


def install_grub(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    ctx.write_target("etc/default/grub", grub_defaults(cfg, layout))
    if cfg.efi:
        # With an encrypted /boot the ESP is mounted at /boot/efi instead, so the
        # kernel, the initramfs and grub.cfg all sit inside the container.
        directory = "/boot/efi" if cfg.encrypt_boot else "/boot"
        boot = ctx.target_path(directory)
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
        # An encrypted /boot needs the modules GRUB uses to open the container
        # and the volume group before it can read its own configuration.
        modules = ""
        if cfg.encrypt_boot:
            modules = ' --modules="normal cryptodisk luks lvm part_gpt part_msdos"'
        ctx.chroot(
            "grub-install --target=%s --efi-directory=%s "
            "--bootloader-id=Kiln --recheck%s"
            % (efi_target(cfg), directory, modules)
        )
        ctx.chroot(EFI_BOOT_ORDER, check=False)
        ctx.chroot("umount /sys/firmware/efi/efivars 2>/dev/null || true", check=False)
    else:
        # Legacy BIOS with an array: GRUB has to be on every member, or the
        # machine only boots from the disk the firmware happens to pick.
        for member in layout.members or []:
            ctx.chroot("grub-install --target=i386-pc --recheck %s" % member.disk)
    ctx.chroot("grub-mkconfig -o /boot/grub/grub.cfg")
    _verify_kernel(ctx, cfg)
    _fix_grub_paths(ctx, cfg)
    if ctx.dry_run:
        return
    config = ctx.read_target("boot/grub/grub.cfg")
    if not config:
        raise RuntimeError("grub-mkconfig wrote no /boot/grub/grub.cfg")
    # An LVM or array root is named by its device path on purpose: it does not
    # exist until the initramfs has opened the container.
    if not any(
        marker in config
        for marker in ("root=UUID=", "root=/dev/mapper/", "root=/dev/md")
    ):
        ctx.log.log(
            "grub.cfg does not name the root file system; check /boot/grub/grub.cfg "
            "before rebooting",
            "warn",
        )


def install(ctx: Context, cfg: InstallConfig, layout: DiskPlan) -> None:
    choice = cfg.resolved_bootloader
    ctx.log.log("installing the %s boot loader" % choice, "step")
    if choice == "systemd-boot":
        install_systemd_boot(ctx, cfg, layout)
    else:
        install_grub(ctx, cfg, layout)
    if cfg.secure_boot:
        sign_boot_files(ctx, cfg, layout)
