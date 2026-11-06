"""The package layer: what to install, and how pacman and pacstrap are driven.

Arch has no stage 3 archive; the system is built by ``pacstrap`` from the
official repositories, exactly as the installation guide does it.  This module
owns that package list, the ``[multilib]`` switch and repository search.
"""

from __future__ import annotations

import os
import re

from typing import TYPE_CHECKING

from . import catalog, options
from .catalog import MULTILIB_PACKAGES, is_multilib_package
from .context import Context, CommandError

if TYPE_CHECKING:  # imported for annotations only; avoids a cycle at runtime
    from .config import InstallConfig

#: The guide's essential set, plus what the wizard promises (networking, tools).
BASE_PACKAGES = (
    "base",
    "sudo",
    "nano",
    "vim",
    "man-db",
    "man-pages",
    "texinfo",
    "less",
    "bash-completion",
    "networkmanager",
    "dosfstools",
    "e2fsprogs",
    "mtools",
    "archlinux-keyring",
    "pacman-contrib",
    # The control panel is a Tk application and needs an interpreter and Tk on
    # the installed system, not only on the live medium.
    "python",
    "tk",
)

DEFAULT_PACMAN_CONF = """\
[options]
HoldPkg     = pacman glibc
Architecture = auto
Color
CheckSpace
ParallelDownloads = 5
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

#[multilib]
#Include = /etc/pacman.d/mirrorlist
"""


# --------------------------------------------------------------------------- #
# pacman.conf and multilib
# --------------------------------------------------------------------------- #
def set_multilib(conf: str, enabled: bool) -> str:
    """Enable or disable the ``[multilib]`` section of a pacman.conf text."""
    lines = conf.splitlines()
    result: list[str] = []
    inside = False
    found = False
    for line in lines:
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        if bare == "[multilib]":
            inside = True
            found = True
            result.append("[multilib]" if enabled else "#[multilib]")
            continue
        if inside:
            if stripped.startswith("[") and bare != "[multilib]":
                inside = False
                result.append(line)
                continue
            if bare.startswith("Include"):
                result.append("Include = /etc/pacman.d/mirrorlist" if enabled else "#Include = /etc/pacman.d/mirrorlist")
                continue
            if not stripped:
                result.append(line)
                continue
            if not enabled and not line.startswith("#"):
                result.append("#" + line)
            else:
                result.append(line)
            continue
        result.append(line)

    if enabled and not found:
        result += ["", "[multilib]", "Include = /etc/pacman.d/mirrorlist"]
    return "\n".join(result) + "\n"


def live_pacman_conf() -> str:
    try:
        with open("/etc/pacman.conf", "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return DEFAULT_PACMAN_CONF


def target_pacman_conf(cfg: InstallConfig) -> str:
    return set_multilib(live_pacman_conf(), cfg.multilib)


def enable_live_multilib(ctx: Context) -> None:
    """Turn on [multilib] on the live medium.

    Needed so the picker can list 32-bit packages, and so ``pacstrap`` sees the
    same repositories the target will use.  The live root is an overlay, so this
    never survives a reboot.
    """
    if ctx.dry_run or not os.path.exists("/etc/pacman.conf"):
        ctx.log.log("skipping live multilib setup (no live pacman.conf)", "warn")
        return
    try:
        with open("/etc/pacman.conf", "r", encoding="utf-8") as handle:
            current = handle.read()
    except OSError as error:
        ctx.log.log("cannot read /etc/pacman.conf: %s" % error, "warn")
        return
    updated = set_multilib(current, True)
    if updated == current:
        ctx.log.log("multilib is already enabled on the live medium", "step")
        return
    try:
        with open("/etc/pacman.conf", "w", encoding="utf-8") as handle:
            handle.write(updated)
        ctx.log.log("multilib enabled on the live medium", "step")
    except OSError as error:
        ctx.log.log("cannot write /etc/pacman.conf: %s" % error, "warn")
        return
    ctx.run(["pacman", "-Sy", "--noconfirm"], check=False)


def write_target_conf(ctx: Context, cfg: InstallConfig) -> None:
    ctx.write_target("etc/pacman.conf", target_pacman_conf(cfg), mode=0o644)


# --------------------------------------------------------------------------- #
# Package lists
# --------------------------------------------------------------------------- #
def filesystem_packages(root_fs: str) -> list:
    return {
        "ext4": ["e2fsprogs"],
        "xfs": ["xfsprogs"],
        "btrfs": ["btrfs-progs"],
    }.get(root_fs, [])


def storage_packages(cfg: InstallConfig) -> list:
    """Tools the planned storage stack needs on the installed system."""
    packages = []
    if cfg.encrypted:
        packages.append("cryptsetup")
    if cfg.uses_lvm:
        packages.append("lvm2")
    if cfg.uses_raid:
        packages.append("mdadm")
    return packages


def security_packages(cfg: InstallConfig) -> list:
    packages = []
    if cfg.secure_boot:
        packages.append("sbctl")
    if cfg.hardening:
        # mokutil reports the Secure Boot state the unit checks, tpm2-tools reads
        # the measurements the disk key is bound to.
        packages.extend(["mokutil", "tpm2-tools"])
    if cfg.alongside:
        # os-prober is what finds the other system and writes its menu entry.
        packages.append("os-prober")
    return packages


def bootloader_packages(cfg: InstallConfig) -> list:
    """Boot loader packages needed for the resolved choice."""
    if cfg.resolved_bootloader == "systemd-boot":
        return ["efibootmgr"]
    return ["grub", "efibootmgr"] if cfg.efi else ["grub"]


#: PCI vendor id -> the vendor split of ``linux-firmware`` that serves it.
FIRMWARE_VENDORS = {
    "1002": "linux-firmware-amdgpu",
    "10de": "linux-firmware-nvidia",
    "8086": "linux-firmware-intel",
    "168c": "linux-firmware-atheros",
    "14e4": "linux-firmware-broadcom",
    "14c3": "linux-firmware-mediatek",
    "10ec": "linux-firmware-realtek",
    "1013": "linux-firmware-cirrus",
}


def firmware_packages(cfg: InstallConfig, hardware=None) -> list:
    """The firmware to install, per the choice made on the Software page.

    ``auto`` installs one package per vendor found on the PCI bus plus the
    unsorted set.  When nothing was identified (a virtual machine, or ``lspci``
    missing) the full set is used instead: a machine without firmware does not
    boot its hardware.  ``all`` is the Arch default, where ``linux-firmware``
    is a metapackage that pulls every vendor.
    """
    if (cfg.firmware or "auto").lower() == "all":
        return ["linux-firmware"]
    vendors = list(getattr(hardware, "firmware_vendors", []) or [])
    if not vendors:
        return ["linux-firmware"]
    packages = ["linux-firmware-other"]
    for vendor in vendors:
        package = FIRMWARE_VENDORS.get(str(vendor).lower())
        if package and package not in packages:
            packages.append(package)
    return packages


def microcode_package(cfg: InstallConfig, hardware) -> str:
    if cfg.microcode == "none":
        return ""
    if cfg.microcode == "auto":
        return getattr(hardware, "microcode", "") if hardware is not None else ""
    return cfg.microcode


def required_packages(cfg: InstallConfig, hardware=None) -> list:
    """Everything pacstrap installs in the first pass.

    32-bit packages are deliberately not here: they need the target's
    ``[multilib]`` section, which is written after the base system exists.
    """
    packages: list = list(BASE_PACKAGES)
    packages += firmware_packages(cfg, hardware)
    packages.append(cfg.kernel_package)
    packages += filesystem_packages(cfg.root_fs)
    packages += bootloader_packages(cfg)
    packages += storage_packages(cfg)
    packages += security_packages(cfg)
    packages.append("networkmanager")
    microcode = microcode_package(cfg, hardware)
    if microcode:
        packages.append(microcode)
    packages += options.desktop_packages(cfg.desktop)
    packages += [p for p in cfg.packages if not is_multilib_package(p)]
    # Preserve order, drop duplicates.
    seen = set()
    unique = []
    for package in packages:
        if package and package not in seen:
            seen.add(package)
            unique.append(package)
    return unique


def multilib_packages(cfg: InstallConfig) -> list:
    return [p for p in cfg.packages if is_multilib_package(p)]


def multilib_missing(cfg: InstallConfig) -> list:
    """Selected packages that can only come from a disabled multilib."""
    if cfg.multilib:
        return []
    return [p for p in cfg.packages if is_multilib_package(p)]


def aur_packages(cfg: InstallConfig) -> list:
    """AUR packages the user asked for; built after the repositories are done."""
    return list(cfg.aur_packages) if cfg.aur else []


def install_size_hint(packages: list) -> str:
    return "%d package%s" % (len(packages), "" if len(packages) == 1 else "s")


# --------------------------------------------------------------------------- #
# Running pacman
# --------------------------------------------------------------------------- #
def ensure_keyring(ctx: Context) -> None:
    """Make sure the live keyring can verify signatures before pacstrap runs.

    A medium whose keyring was never populated fails inside pacstrap with
    "required key missing from keyring", which says nothing about the cause.
    Populating first turns that dead end into a few seconds of work.
    """
    if ctx.dry_run:
        return
    if not os.path.exists("/etc/pacman.d/gnupg/trustdb.gpg"):
        ctx.log.log("initialising the pacman keyring", "step")
        ctx.run(["pacman-key", "--init"], check=False)
    ctx.log.log("populating the pacman keyring", "step")
    ctx.run(["pacman-key", "--populate", "archlinux"], check=False)


def pacstrap(ctx: Context, cfg: InstallConfig, packages: list) -> None:
    ensure_keyring(ctx)
    ctx.log.log("pacstrap: %s" % install_size_hint(packages), "step")
    ctx.run(["pacstrap", "-K", ctx.target] + list(packages))


def install(ctx: Context, packages: list) -> None:
    """Install more packages inside the target system."""
    if not packages:
        return
    ctx.chroot("pacman -S --needed --noconfirm %s" % " ".join(packages))


def refresh(ctx: Context) -> None:
    ctx.chroot("pacman -Sy --noconfirm")


def remove_cache(ctx: Context) -> None:
    ctx.chroot("paccache -rk0 || true")
    ctx.chroot("rm -rf /var/cache/pacman/pkg/* /var/lib/pacman/sync/*.db.sig")


def search(ctx: Context, term: str, limit: int = 60) -> list:
    """Search the configured repositories.

    Returns ``[{"repo", "name", "version", "description"}, ...]``.  A failed
    search (offline medium, no repositories yet) is an empty list, not an error.
    """
    term = re.sub(r"[^\w@._+-]", "", term or "")[:60]
    if not term:
        return []
    try:
        out = ctx.captured(["pacman", "-Ss", "--", term], quiet=True)
    except (CommandError, Exception):
        return []
    results = []
    current = None
    for line in out.splitlines():
        if not line.strip():
            continue
        if line[0] not in (" ", "\t"):
            head = line.split()
            if not head or "/" not in head[0]:
                continue
            repo, _, name = head[0].partition("/")
            version = head[1] if len(head) > 1 else ""
            current = {"repo": repo, "name": name, "version": version, "description": ""}
            results.append(current)
        elif current is not None:
            current["description"] = (current["description"] + " " + line.strip()).strip()
        if len(results) >= limit:
            break
    return results


def installed_package_names(ctx: Context) -> list:
    """Package names present in the target, once pacstrap has run."""
    out = ctx.chroot_capture("pacman -Qq 2>/dev/null || true")
    return [line.strip() for line in out.splitlines() if line.strip()]


def selected_details(packages: list) -> list:
    """``[(name, label, note), ...]`` for the review page."""
    rows = []
    for name in packages:
        label, note = catalog.details(name)
        if not label:
            label, note = name, "From the repositories"
        rows.append((name, label, note))
    return rows
