"""System configuration inside the new installation (the chroot steps)."""

from __future__ import annotations

import os
import shlex
import shutil

from . import options
from .config import InstallConfig
from .context import Context
from .hardware import HardwareProfile
from .disk import DiskPlan

LOG_PATH = "/var/log/kiln-setup.log"

#: Where the live medium keeps both packages (installer and control panel).
MEDIUM_ROOT = "/opt/kiln-setup"

PANEL_LAUNCHER = """#!/bin/sh
# Arch Control Panel launcher (installed by the Arch GUI installer).
PYTHONPATH=/opt/kiln-setup
export PYTHONPATH
exec /usr/bin/python3 -m panel "$@"
"""

PANEL_DESKTOP = """[Desktop Entry]
Type=Application
Name=Arch Control Panel
GenericName=System Settings
Comment=Manage software, services, users and network
Exec=/usr/local/bin/kiln-panel
Icon=preferences-system
Terminal=false
Categories=Settings;System;
Keywords=arch;pacman;settings;software;
"""


def _uncomment_locale(text: str, locale: str) -> str:
    """Return locale.gen with ``locale`` enabled, adding it when absent."""
    lines = text.splitlines() if text else []
    out = []
    found = False
    for line in lines:
        token = line.lstrip("#").strip().split(" ")[0] if line.strip() else ""
        if token == locale:
            out.append("%s UTF-8" % locale)
            found = True
        else:
            out.append(line)
    if not found:
        out.append("%s UTF-8" % locale)
    return "\n".join(out) + "\n"


def set_localtime(ctx: Context, cfg: InstallConfig) -> None:
    ctx.run(
        [
            "ln",
            "-sf",
            "/usr/share/zoneinfo/%s" % cfg.timezone,
            ctx.target_path("etc/localtime"),
        ]
    )
    ctx.chroot("hwclock --systohc")


def set_locale(ctx: Context, cfg: InstallConfig) -> None:
    current = ctx.read_target("etc/locale.gen")
    ctx.write_target("etc/locale.gen", _uncomment_locale(current, cfg.locale))
    ctx.chroot("locale-gen")
    ctx.write_target("etc/locale.conf", "LANG=%s\n" % cfg.locale)


def set_keyboard(ctx: Context, cfg: InstallConfig) -> None:
    ctx.write_target("etc/vconsole.conf", "KEYMAP=%s\n" % cfg.keymap)


def set_hostname(ctx: Context, cfg: InstallConfig) -> None:
    ctx.write_target("etc/hostname", cfg.hostname + "\n")
    ctx.write_target(
        "etc/hosts",
        "127.0.0.1\tlocalhost\n"
        "::1\t\tlocalhost\n"
        "127.0.1.1\t%s.localdomain\t%s\n" % (cfg.hostname, cfg.hostname),
    )


def set_root_password(ctx: Context, password: str) -> None:
    # chpasswd reads the credential from stdin, so the password never appears in
    # a command line (and never in a process list).
    ctx.chroot("chpasswd", input_text="root:%s\n" % password)


def create_user(ctx: Context, cfg: InstallConfig) -> None:
    if cfg.user is None:
        return
    groups = ",".join(cfg.user.groups) if cfg.user.groups else "wheel"
    ctx.chroot(
        "useradd --create-home --shell /bin/bash --groups %s %s"
        % (shlex.quote(groups), shlex.quote(cfg.user.name))
    )
    ctx.chroot("chpasswd", input_text="%s:%s\n" % (cfg.user.name, cfg.user.password))


def configure_sudoers(ctx: Context) -> None:
    ctx.write_target(
        "etc/sudoers.d/10-wheel",
        "# Written by Kiln Setup, an unofficial installer for Arch Linux.\n"
        "%wheel ALL=(ALL:ALL) ALL\n",
        mode=0o440,
    )


def enable_services(ctx: Context, cfg: InstallConfig) -> None:
    ctx.chroot("systemctl enable NetworkManager")
    ctx.chroot("systemctl enable systemd-timesyncd", check=False)
    if cfg.desktop == "gnome":
        ctx.chroot("systemctl enable gdm", check=False)
    elif cfg.desktop in ("kde", "lxqt"):
        ctx.chroot("systemctl enable sddm", check=False)
    elif cfg.desktop in ("xfce", "cinnamon", "mate"):
        ctx.chroot("systemctl enable lightdm", check=False)
    if "bluez" in cfg.packages:
        ctx.chroot("systemctl enable bluetooth", check=False)
    if "cups" in cfg.packages:
        ctx.chroot("systemctl enable cups", check=False)
    if "docker" in cfg.packages:
        ctx.chroot("systemctl enable docker", check=False)
    if "libvirt" in cfg.packages:
        ctx.chroot("systemctl enable libvirtd", check=False)
    if "tlp" in cfg.packages:
        ctx.chroot("systemctl enable tlp", check=False)
    if "power-profiles-daemon" in cfg.packages:
        ctx.chroot("systemctl enable power-profiles-daemon", check=False)
    if "fwupd" in cfg.packages:
        ctx.chroot("systemctl enable fwupd-refresh.timer", check=False)


def enable_fstrim(ctx: Context) -> None:
    ctx.chroot("systemctl enable fstrim.timer", check=False)


def build_initramfs(ctx: Context) -> None:
    ctx.chroot("mkinitcpio -P")


def install_panel(ctx: Context, cfg: InstallConfig) -> None:
    """Copy the control panel into the new system and give it a launcher.

    The panel shares the installer's theme, catalogue and pacman helpers, so both
    Python packages are installed side by side under /opt/kiln-setup.
    """
    if not os.path.isdir(MEDIUM_ROOT) and not ctx.dry_run:
        ctx.log.log("the control panel is not on this medium; skipping it", "warn")
        return
    destination = ctx.target_path("opt/kiln-setup")
    ctx.log.log("installing the control panel into %s" % destination, "step")
    if not ctx.dry_run:
        try:
            os.makedirs(destination, exist_ok=True)
            shutil.copytree(
                MEDIUM_ROOT,
                destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        except OSError as error:
            # A missing panel must not fail an otherwise finished installation.
            ctx.log.log("could not copy the control panel: %s" % error, "warn")
            return
    ctx.write_target("usr/local/bin/kiln-panel", PANEL_LAUNCHER, mode=0o755)
    ctx.write_target("usr/share/applications/kiln-panel.desktop", PANEL_DESKTOP, mode=0o644)


def copy_log(ctx: Context, path: str = LOG_PATH) -> None:
    if ctx.dry_run or not os.path.exists(path):
        return
    ctx.run(
        ["install", "-D", "-m", "644", path, ctx.target_path("var/log/kiln-setup.log")],
        check=False,
        quiet=True,
    )


def finalize(ctx: Context) -> None:
    """Unwind the mounts so the installed system can be rebooted into."""
    ctx.run(["swapoff", "-a"], check=False, quiet=True)
    ctx.run(["umount", "-R", ctx.target], check=False)


def configure(ctx: Context, cfg: InstallConfig, hw: HardwareProfile, layout: DiskPlan) -> None:
    """Locale, keyboard and hostname: the quick chroot steps."""
    set_localtime(ctx, cfg)
    set_locale(ctx, cfg)
    set_keyboard(ctx, cfg)
    set_hostname(ctx, cfg)


def accounts(ctx: Context, cfg: InstallConfig) -> None:
    set_root_password(ctx, cfg.root_password)
    create_user(ctx, cfg)
    configure_sudoers(ctx)


def summary(cfg: InstallConfig, hw: HardwareProfile) -> list:
    return [
        ("Computer", hw.cpu_model),
        ("Memory", "%d GiB" % max(1, round(hw.ram_bytes / 1024 ** 3))),
        ("Firmware", hw.firmware.upper() + (", Secure Boot on" if hw.secure_boot else "")),
        ("Disk", cfg.disk),
        ("Filesystem", cfg.root_fs),
        ("Kernel", cfg.kernel),
        ("Desktop", options.DESKTOPS.get(cfg.desktop, (cfg.desktop, ""))[0]),
        ("Boot loader", cfg.resolved_bootloader),
        ("Multilib", "enabled (32-bit software)" if cfg.multilib else "disabled"),
        ("Packages", "%d selected by hand" % len(cfg.packages)),
    ]
