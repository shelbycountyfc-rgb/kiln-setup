"""System configuration inside the new installation (the chroot steps)."""

from __future__ import annotations

import os
import re
import shlex
import shutil

from . import options
from .config import InstallConfig
from .context import Context
from .hardware import HardwareProfile
from .disk import DiskPlan

LOG_PATH = "/var/log/kiln-setup.log"

#: Where a key file for the container lives in the new system.
KEYFILE_PATH = "/etc/cryptsetup-keys.d/cryptroot.key"
KEYFILE_BYTES = 4096

#: Where the live medium keeps both packages (installer and control panel).
MEDIUM_ROOT = "/opt/kiln-setup"

PANEL_LAUNCHER = """#!/bin/sh
# Kiln Control Panel launcher.
#
# A desktop entry starts this with no terminal, so a failure is invisible unless
# it is written down and shown.  The panel's output goes to a log the user owns,
# the working directory is the installation root (so a file in $HOME cannot
# shadow the package), and a failure ends in a dialog instead of nothing.

ROOT=/opt/kiln-setup
LOG="${XDG_STATE_HOME:-$HOME/.local/state}/kiln-panel.log"

mkdir -p "$(dirname "$LOG")" 2>/dev/null

reason=""
if [ ! -d "$ROOT/panel" ]; then
    reason="the control panel is not installed in $ROOT"
elif [ ! -x /usr/bin/python3 ]; then
    reason="python3 is not installed: sudo pacman -S python tk"
elif ! cd "$ROOT"; then
    reason="cannot enter $ROOT"
fi

if [ -z "$reason" ]; then
    PYTHONPATH=$ROOT
    export PYTHONPATH
    /usr/bin/python3 -m panel "$@" >>"$LOG" 2>&1
    status=$?
    if [ "$status" -eq 0 ]; then
        exit 0
    fi
    reason="the panel exited with status $status"
fi

{
    echo "$(date '+%Y-%m-%d %H:%M:%S') kiln-panel: $reason"
    tail -n 20 "$LOG" 2>/dev/null
} >>"$LOG" 2>/dev/null

message="$reason

The log is $LOG
Run 'kiln-panel --doctor' in a terminal for details."

if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="Kiln Control Panel" --text="$message" 2>/dev/null
elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title "Kiln Control Panel" --error "$message" 2>/dev/null
fi

echo "$message" >&2
exit 1
"""

PANEL_DESKTOP = """[Desktop Entry]
Version=1.0
Type=Application
Name=Kiln Control Panel
GenericName=System Settings
Comment=Manage software, services, users and network
Exec=/usr/local/bin/kiln-panel
TryExec=/usr/local/bin/kiln-panel
Icon=preferences-system
Terminal=false
StartupNotify=false
Categories=Settings;System;
Keywords=kiln;pacman;settings;software;
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
    # Windows writes the clock as local time and reads it back as local time.
    # A dual-boot machine that stores UTC shows the wrong hour in Windows, which
    # then rewrites the RTC on every shutdown, so the choice is ours to make.
    ctx.chroot("hwclock --systohc%s" % (" --localtime" if cfg.clock_local else ""))


def set_locale(ctx: Context, cfg: InstallConfig) -> None:
    current = ctx.read_target("etc/locale.gen")
    for locale in [cfg.locale] + [entry for entry in cfg.locales if entry != cfg.locale]:
        current = _uncomment_locale(current, locale)
    ctx.write_target("etc/locale.gen", current)
    ctx.chroot("locale-gen")
    ctx.write_target("etc/locale.conf", "LANG=%s\n" % cfg.locale)


def set_keyboard(ctx: Context, cfg: InstallConfig) -> None:
    ctx.write_target("etc/vconsole.conf", "KEYMAP=%s\n" % cfg.keymap)
    layout = cfg.x11_keymap or cfg.keymap
    # The console layout and the desktop layout are separate settings: the
    # desktop reads X11's keyboard file, and localed writes it from localectl.
    ctx.chroot("localectl set-x11-keymap %s" % shlex.quote(layout), check=False)
    ctx.write_target(
        "etc/X11/xorg.conf.d/00-keyboard.conf",
        "# Written by Kiln Setup, an unofficial installer for Arch Linux.\n"
        'Section "InputClass"\n'
        '    Identifier "system-keyboard"\n'
        '    MatchIsKeyboard "on"\n'
        '    Option "XkbLayout" "%s"\n'
        "EndSection\n" % layout,
        mode=0o644,
    )


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
    if cfg.hardening:
        ctx.chroot("systemctl enable kiln-hardening.service", check=False)


def enable_fstrim(ctx: Context) -> None:
    ctx.chroot("systemctl enable fstrim.timer", check=False)


#: snapper's timeline for the root subvolume of a fresh Btrfs system.
SNAPPER_CONFIG = """\
# Written by Kiln Setup, an unofficial installer for Arch Linux.
SUBVOLUME="/"
FSTYPE="btrfs"
QGROUP=""
SPACE_LIMIT="0.5"
FREE_LIMIT="0.2"
ALLOW_USERS=""
ALLOW_GROUPS=""
SYNC_ACL="no"
BACKGROUND_COMPARISON="yes"
NUMBER_CLEANUP="yes"
NUMBER_MIN_AGE="1800"
NUMBER_LIMIT="50"
NUMBER_LIMIT_IMPORTANT="10"
TIMELINE_CREATE="yes"
TIMELINE_CLEANUP="yes"
TIMELINE_MIN_AGE="1800"
TIMELINE_LIMIT_HOURLY="10"
TIMELINE_LIMIT_DAILY="10"
TIMELINE_LIMIT_WEEKLY="0"
TIMELINE_LIMIT_MONTHLY="5"
TIMELINE_LIMIT_YEARLY="0"
EMPTY_PRE_POST_CLEANUP="yes"
EMPTY_PRE_POST_MIN_AGE="1800"
"""

#: A default-deny filter: loopback, replies and ICMP are the whole allow list.
NFTABLES_CONF = """\
#!/usr/bin/nft -f
# Written by Kiln Setup, an unofficial installer for Arch Linux.
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;
        iif lo accept
        ct state established,related accept
        ct state invalid drop
@@SSH@@        ip protocol icmp accept
        ip6 nexthdr ipv6-icmp accept
    }
    chain forward {
        type filter hook forward priority filter; policy drop;
    }
    chain output {
        type filter hook output priority filter; policy accept;
    }
}
"""

#: reflector's own configuration, so its timer repeats what the medium did.
REFLECTOR_CONF = """\
# Written by Kiln Setup, an unofficial installer for Arch Linux.
--country {country}
--protocol https
--latest 20
--sort rate
--save /etc/pacman.d/mirrorlist
"""


def configure_snapshots(ctx: Context, cfg: InstallConfig) -> None:
    """Give a Btrfs root the timeline that makes subvolumes worth having."""
    if not cfg.snapshots or cfg.root_fs != "btrfs":
        return
    ctx.log.log("configuring Btrfs snapshots in /.snapshots", "step")
    ctx.write_target("etc/snapper/configs/root", SNAPPER_CONFIG, mode=0o640)
    ctx.write_target("etc/conf.d/snapper", 'SNAPPER_CONFIGS="root"\n', mode=0o644)
    ctx.chroot("systemctl enable snapper-timeline.timer", check=False)
    ctx.chroot("systemctl enable snapper-cleanup.timer", check=False)
    if cfg.resolved_bootloader == "grub":
        # grub-btrfsd keeps a menu entry per snapshot in step with grub.cfg.
        ctx.chroot("systemctl enable grub-btrfsd.service", check=False)


def configure_firewall(ctx: Context, cfg: InstallConfig) -> None:
    """Write a default-deny filter, and open the one port that was asked for."""
    if not cfg.firewall:
        if cfg.sshd:
            ctx.chroot("systemctl enable sshd", check=False)
        return
    ctx.log.log("writing a default-deny packet filter", "step")
    ssh = "        tcp dport 22 accept\n" if cfg.sshd else ""
    ctx.write_target(
        "etc/nftables.conf", NFTABLES_CONF.replace("@@SSH@@", ssh), mode=0o644
    )
    ctx.chroot("systemctl enable nftables", check=False)
    if cfg.sshd:
        ctx.chroot("systemctl enable sshd", check=False)


def configure_mirrors(ctx: Context, cfg: InstallConfig) -> None:
    """Keep the mirrorlist fresh, in the country the medium was told to use."""
    country = (cfg.mirror_country or "").strip()
    if not country or country.lower() == "worldwide":
        return
    ctx.log.log("keeping the mirrorlist in step with reflector (%s)" % country, "step")
    ctx.write_target(
        "etc/xdg/reflector/reflector.conf",
        REFLECTOR_CONF.replace("{country}", '"%s"' % country),
        mode=0o644,
    )
    ctx.chroot("systemctl enable reflector.timer", check=False)


def configure_crypto(ctx: Context, cfg: InstallConfig, layout: DiskPlan | None = None) -> None:
    """Make the initramfs able to find an encrypted, assembled or LVM root.

    The order of the hooks is the order the kernel runs them: the array has to
    be assembled before the container is unlocked, and the container before the
    volume group is scanned.  Getting this wrong produces a boot that stops in
    the emergency shell with no explanation.
    """
    tpm = cfg.unlock == "tpm2"
    if tpm:
        # A TPM unlock is systemd's: sd-encrypt asks systemd-cryptsetup, which is
        # not in the image unless the systemd hook is.
        hooks = [
            "base", "systemd", "autodetect", "microcode", "modconf", "kms",
            "keyboard", "sd-vconsole", "block",
        ]
    else:
        hooks = [
            "base", "udev", "autodetect", "microcode", "modconf", "kms",
            "keyboard", "keymap", "consolefont", "block",
        ]
    if cfg.uses_raid:
        hooks.append("mdadm_udev")
    if cfg.encrypted:
        hooks.append("sd-encrypt" if tpm else "encrypt")
    if cfg.uses_lvm:
        hooks.append("lvm2")
    if cfg.hibernate:
        # resume runs after every layer the swap can sit on has been assembled.
        hooks.append("resume")
    hooks += ["filesystems", "fsck"]

    text = ctx.read_target("etc/mkinitcpio.conf")
    if text:
        updated = re.sub(
            r"^HOOKS=\(.*\)\s*$", "HOOKS=(%s)" % " ".join(hooks), text, flags=re.M
        )
        if cfg.root_fs == "btrfs":
            # The autodetect hook only keeps modules of devices in this machine;
            # a btrfs root always needs its module in the image.
            updated = re.sub(
                r"^MODULES=\(.*\)\s*$", "MODULES=(btrfs)", updated, flags=re.M
            )
        if cfg.unlock == "keyfile":
            # The key file has to be inside the image: that is what replaces the
            # passphrase prompt at boot.
            if re.search(r"^FILES=\(.*\)\s*$", updated, flags=re.M):
                updated = re.sub(
                    r"^FILES=\(.*\)\s*$",
                    "FILES=(%s)" % KEYFILE_PATH,
                    updated,
                    flags=re.M,
                )
            else:
                updated = updated.replace(
                    "HOOKS=(", "FILES=(%s)\nHOOKS=(" % KEYFILE_PATH, 1
                )
        if updated != text:
            ctx.write_target("etc/mkinitcpio.conf", updated)

    if cfg.encrypted and layout is not None and getattr(layout, "luks_uuid", ""):
        if cfg.unlock == "tpm2":
            note = (
                "# Written by Kiln Setup. The TPM releases the key; the passphrase "
                "still works.\n"
            )
            line = "%s UUID=%s none luks,tpm2-device=auto\n" % (
                layout.crypt_name,
                layout.luks_uuid,
            )
        elif cfg.unlock == "keyfile":
            note = "# Written by Kiln Setup. A key file in the initramfs opens it.\n"
            line = "%s UUID=%s %s luks\n" % (
                layout.crypt_name,
                layout.luks_uuid,
                KEYFILE_PATH,
            )
        else:
            note = "# Written by Kiln Setup. The passphrase is asked for at every boot.\n"
            line = "%s UUID=%s none luks\n" % (layout.crypt_name, layout.luks_uuid)
        ctx.write_target("etc/crypttab", note + line, mode=0o600)


def install_keyfile(ctx: Context, cfg: InstallConfig, layout: DiskPlan | None) -> None:
    """Add a random key file to the container and keep it in the initramfs."""
    if cfg.unlock != "keyfile" or not cfg.encrypted or layout is None:
        return
    device = getattr(layout, "luks_device", "")
    if not device:
        raise RuntimeError("the container to add a key file to is not known")
    path = ctx.target_path(KEYFILE_PATH.lstrip("/"))
    ctx.log.log("adding a key file for %s" % device, "step")
    if not ctx.dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(os.urandom(KEYFILE_BYTES))
        os.chmod(path, 0o400)
    ctx.run(
        ["cryptsetup", "luksAddKey", "--key-file", "-", device, path],
        input_text=cfg.luks_passphrase,
    )


def enroll_tpm(ctx: Context, cfg: InstallConfig, layout: DiskPlan | None) -> None:
    """Enrol the container with the TPM so the firmware can release the key."""
    if cfg.unlock != "tpm2" or not cfg.encrypted or layout is None:
        return
    device = getattr(layout, "luks_device", "")
    if not device:
        raise RuntimeError("the container to enrol with the TPM is not known")
    if not ctx.dry_run and not os.path.exists("/dev/tpmrm0"):
        ctx.log.log(
            "this machine has no TPM 2.0 device, so the passphrase is still asked for "
            "at boot; enrol later with 'systemd-cryptenroll --tpm2-device=auto %s'"
            % device,
            "warn",
        )
        return
    ctx.log.log("enrolling %s with the TPM" % device, "step")
    result = ctx.chroot(
        "systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 %s" % device,
        check=False,
    )
    if getattr(result, "returncode", 1) != 0:
        ctx.log.log(
            "the TPM would not take the key (firmware not in setup mode, or no TPM "
            "2.0). The passphrase still unlocks the disk; enrol later with "
            "'systemd-cryptenroll --tpm2-device=auto %s'." % device,
            "warn",
        )


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
    missing = [
        name
        for name in ("installer", "panel")
        if not ctx.dry_run
        and not os.path.isfile(ctx.target_path("opt/kiln-setup/%s/__main__.py" % name))
    ]
    if missing:
        # A menu entry that starts nothing is worse than no entry at all.  The
        # panel is a Tk window that imports the installer package, so both have
        # to have arrived before the launcher and the entry are written.
        ctx.log.log(
            "the control panel was not installed (%s missing from %s), so no menu "
            "entry was written" % (", ".join(missing), destination),
            "warn",
        )
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
    """Unwind the mounts so the installed system can be rebooted into.

    A target that stays mounted means the last writes did not reach the disk:
    reporting "installation complete" over it is how a system boots into a
    half-written /etc.
    """
    if ctx.dry_run:
        return
    ctx.run(["swapoff", "-a"], check=False, quiet=True)
    ctx.run(["sync"], check=False, quiet=True)
    for _attempt in range(3):
        ctx.run(["umount", "-R", ctx.target], check=False, quiet=True)
        if not os.path.ismount(ctx.target):
            return
        ctx.run(["sleep", "1"], check=False, quiet=True)
    raise RuntimeError(
        "the new system is still mounted at %s: something is using it, so the "
        "installation was not written out completely" % ctx.target
    )


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


def stack_description(cfg: InstallConfig) -> str:
    """The storage layers, bottom-up, as one readable line."""
    layers = []
    if cfg.uses_raid:
        layers.append("RAID %s" % cfg.raid)
    if cfg.encrypted:
        layers.append("LUKS2")
    if cfg.uses_lvm:
        layers.append("LVM")
    layers.append(cfg.root_fs)
    if cfg.root_fs == "btrfs" and not cfg.alongside:
        layers.append("subvolumes")
    return " / ".join(layers)


def summary(cfg: InstallConfig, hw: HardwareProfile) -> list:
    suffix = ""
    if cfg.alongside:
        suffix = " (alongside the existing system)"
    elif cfg.reuse:
        suffix = " (reinstalled into %s)" % (cfg.reuse_root or "a partition")
    disk = cfg.disk + suffix
    extras = [
        name
        for name, on in (
            ("firewall", cfg.firewall),
            ("ssh server", cfg.sshd),
            ("snapshots", cfg.snapshots),
            ("unified kernel image", cfg.uki),
            ("local-time clock", cfg.clock_local),
            ("extra locales", bool(cfg.locales)),
        )
        if on
    ]
    return [
        ("Computer", hw.cpu_model),
        ("Memory", "%d GiB" % max(1, round(hw.ram_bytes / 1024 ** 3))),
        ("Firmware", hw.firmware.upper() + (", Secure Boot on" if hw.secure_boot else "")),
        ("Architecture", cfg.arch),
        ("Disk", disk),
        ("Storage", stack_description(cfg)),
        ("Kernel", cfg.kernel_package),
        (
            "Epic Games",
            "Easy Anti-Cheat kernel profile (EAC Linux v2)"
            if getattr(cfg, "eac", False)
            else "not set up",
        ),
        ("Desktop", options.DESKTOPS.get(cfg.desktop, (cfg.desktop, ""))[0]),
        (
            "Boot loader",
            cfg.resolved_bootloader + (", signed for Secure Boot" if cfg.secure_boot else ""),
        ),
        ("Multilib", "enabled (32-bit software)" if cfg.multilib else "disabled"),
        ("Extras", ", ".join(extras) or "none"),
        (
            "Unlock",
            options.UNLOCK.get(cfg.unlock, (cfg.unlock, ""))[0]
            if cfg.encrypted
            else "not encrypted",
        ),
        (
            "Hibernation",
            "memory is written to swap" if cfg.hibernate else "not set up",
        ),
        (
            "AUR",
            "%d package(s)" % len(cfg.aur_packages) if cfg.aur_packages else "not used",
        ),
        ("Packages", "%d selected by hand" % len(cfg.packages)),
    ]
