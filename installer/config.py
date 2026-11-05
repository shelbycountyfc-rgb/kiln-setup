"""The installation request and its validation.

Every value the window collects is checked here before a single command runs:
the installer is root on a live medium, so a value must never reach a device
path or a chroot command unchecked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import options
from .catalog import is_multilib_package
from .disk import FILESYSTEMS, MIN_DISK_BYTES, RAID_MINIMUM

DEFAULT_GROUPS = ("wheel", "audio", "video", "optical", "storage", "scanner", "power")

HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEYMAP_RE = re.compile(r"^[a-z0-9_-]{2,24}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?\.UTF-8$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
DISK_RE = re.compile(r"^/dev/[a-zA-Z0-9/_.-]{1,60}$")
#: Arch package names are lower case, and may carry @ . _ + -
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9@._+-]{0,79}$")
#: AUR package names follow the same rules as repository package names.
AUR_RE = PACKAGE_RE
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


@dataclass
class UserAccount:
    name: str
    password: str
    groups: list = field(default_factory=lambda: list(DEFAULT_GROUPS))

    def to_public(self) -> dict:
        return {"name": self.name, "groups": list(self.groups)}


@dataclass
class InstallConfig:
    disk: str = ""
    #: ``erase`` wipes the disk; ``alongside`` keeps the existing system.
    mode: str = "erase"
    confirm_erase: bool = False
    efi: bool = True
    bootloader: str = "auto"
    root_fs: str = "ext4"
    swap: bool = True
    swap_gb: float | None = None
    esp_gb: float = 1.0
    separate_home: bool = False
    # -- storage stack ------------------------------------------------------
    encryption: str = "none"
    luks_passphrase: str = ""
    volumes: str = "partitions"
    raid: str = "none"
    raid_disks: list = field(default_factory=list)
    keep_esp: str = ""
    # -- firmware and software ---------------------------------------------
    secure_boot: bool = False
    aur: bool = False
    aur_packages: list = field(default_factory=list)
    arch: str = "x86_64"
    # -- the system ---------------------------------------------------------
    hostname: str = "archlinux"
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    keymap: str = "us"
    desktop: str = "kde"
    kernel: str = "linux"
    microcode: str = "auto"
    firmware: str = "auto"
    multilib: bool = True
    packages: list = field(default_factory=list)
    root_password: str = ""
    user: UserAccount | None = None
    wifi_ssid: str = ""
    wifi_password: str = ""

    # -- construction ------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "InstallConfig":
        if not isinstance(data, dict):
            raise ValueError("installation request must be a mapping")
        config = cls()
        config.disk = str(data.get("disk", "")).strip()
        config.mode = str(data.get("mode", "erase")).strip().lower() or "erase"
        config.confirm_erase = bool(data.get("confirmErase", False))
        config.efi = bool(data.get("efi", True))
        config.bootloader = str(data.get("bootloader", "auto")).strip().lower() or "auto"
        config.root_fs = str(data.get("rootFs", "ext4")).strip().lower() or "ext4"
        config.swap = bool(data.get("swap", True))
        swap_gb = data.get("swapGb")
        try:
            config.swap_gb = None if swap_gb in (None, "", 0, "0", 0.0) else float(swap_gb)
        except (TypeError, ValueError):
            config.swap_gb = None
        config.esp_gb = float(data.get("espGb", 1.0) or 1.0)
        config.separate_home = bool(data.get("separateHome", False))
        if config.root_fs == "btrfs":
            # The subvolume layout already gives /home its own subvolume.
            config.separate_home = False
        config.encryption = str(data.get("encryption", "none")).strip().lower() or "none"
        config.luks_passphrase = str(data.get("luksPassphrase", ""))
        config.volumes = str(data.get("volumes", "partitions")).strip().lower() or "partitions"
        config.raid = str(data.get("raid", "none")).strip().lower() or "none"
        raid_disks = data.get("raidDisks") or []
        if isinstance(raid_disks, str):
            raid_disks = [d for d in re.split(r"[\s,]+", raid_disks) if d]
        config.raid_disks = [str(d).strip() for d in raid_disks if str(d).strip()]
        config.keep_esp = str(data.get("keepEsp", "")).strip()
        config.secure_boot = bool(data.get("secureBoot", False))
        config.aur = bool(data.get("aur", False))
        aur_packages = data.get("aurPackages") or []
        if isinstance(aur_packages, str):
            aur_packages = [p for p in re.split(r"[\s,]+", aur_packages) if p]
        config.aur_packages = [str(p).strip() for p in aur_packages if str(p).strip()]
        config.arch = str(data.get("arch", "x86_64")).strip().lower() or "x86_64"
        if config.mode == "alongside":
            # There is no free space to give a swap partition of our own.
            config.swap = False
            config.separate_home = False
        config.hostname = str(data.get("hostname", "archlinux")).strip() or "archlinux"
        config.timezone = str(data.get("timezone", "UTC")).strip() or "UTC"
        config.locale = str(data.get("locale", "en_US.UTF-8")).strip() or "en_US.UTF-8"
        config.keymap = str(data.get("keymap", "us")).strip() or "us"
        config.desktop = str(data.get("desktop", "kde")).strip().lower() or "kde"
        config.kernel = str(data.get("kernel", "linux")).strip().lower() or "linux"
        config.microcode = str(data.get("microcode", "auto")).strip().lower() or "auto"
        config.firmware = str(data.get("firmware", "auto")).strip().lower() or "auto"
        config.multilib = bool(data.get("multilib", True))
        packages = data.get("packages") or []
        if isinstance(packages, str):
            packages = [p for p in re.split(r"[\s,]+\s*".rstrip(), packages) if p]
        config.packages = [str(p).strip() for p in packages if str(p).strip()]
        config.root_password = str(data.get("rootPassword", ""))
        config.wifi_ssid = str(data.get("wifiSsid", "")).strip()
        config.wifi_password = str(data.get("wifiPassword", ""))
        user = data.get("user")
        if isinstance(user, dict) and user.get("name"):
            groups = user.get("groups")
            if isinstance(groups, str):
                groups = [g for g in re.split(r"[\s,]+", groups) if g]
            config.user = UserAccount(
                name=str(user.get("name", "")).strip(),
                password=str(user.get("password", "")),
                groups=[str(g).strip() for g in (groups or [])] or list(DEFAULT_GROUPS),
            )
        return config

    # -- derived -----------------------------------------------------------
    @property
    def resolved_bootloader(self) -> str:
        if self.bootloader in ("grub", "systemd-boot"):
            return self.bootloader
        return "systemd-boot" if self.efi else "grub"

    @property
    def desktop_requested(self) -> bool:
        return self.desktop != "minimal"

    @property
    def encrypted(self) -> bool:
        return self.encryption == "luks"

    @property
    def uses_lvm(self) -> bool:
        return self.volumes == "lvm"

    @property
    def uses_raid(self) -> bool:
        return self.raid != "none"

    @property
    def alongside(self) -> bool:
        return self.mode == "alongside"

    @property
    def kernel_package(self) -> str:
        """The kernel package name for the architecture being installed."""
        if self.arch == "x86_64":
            return self.kernel
        return "linux-aarch64"

    # -- validation --------------------------------------------------------
    # Each page checks only the answers it collects.  A page that reports the
    # empty root password while offering no field for it is a dead end: that is
    # exactly how the installer became impossible to get past the Disk step.
    def validate(self, disk_size: int = 0) -> list:
        """Every problem, for the last check before anything is written."""
        return self.disk_errors(disk_size) + self.software_errors() + self.system_errors()

    def disk_errors(self, disk_size: int = 0) -> list:
        """Only the answers collected on the disk and storage pages."""
        errors = []
        if self.mode not in options.INSTALL_MODES:
            errors.append("Unknown installation mode %r." % self.mode)
        if not DISK_RE.match(self.disk):
            errors.append("Select the disk Arch Linux should be installed on (/dev/...).")
        elif disk_size and disk_size < MIN_DISK_BYTES:
            errors.append(
                "The selected disk is smaller than 20 GiB; Arch Linux needs at least that much."
            )
        if self.mode == "erase" and not self.confirm_erase:
            errors.append("The disk erase confirmation must be accepted.")
        if self.alongside and not self.efi:
            errors.append(
                "Installing alongside another system needs UEFI: a legacy boot loader "
                "would overwrite the other system's."
            )
        if self.root_fs not in FILESYSTEMS:
            errors.append("Unsupported filesystem %r." % self.root_fs)
        if self.bootloader not in options.BOOTLOADERS:
            errors.append("Unknown boot loader %r." % self.bootloader)
        if not self.efi and self.resolved_bootloader == "systemd-boot":
            errors.append("systemd-boot needs UEFI. Choose GRUB on a legacy BIOS machine.")
        if not 0.1 <= self.esp_gb <= 4:
            errors.append("The EFI system partition must be between 0.1 and 4 GiB.")
        if self.swap_gb is not None and not 0 <= self.swap_gb <= 4096:
            errors.append("Swap size is out of range.")

        # -- encryption, volume manager, array ------------------------------
        if self.encryption not in options.ENCRYPTION:
            errors.append("Unknown encryption choice %r." % self.encryption)
        if self.encrypted and len(self.luks_passphrase) < 8:
            errors.append("The disk encryption passphrase must be at least 8 characters.")
        if self.volumes not in options.VOLUMES:
            errors.append("Unknown volume manager %r." % self.volumes)
        if self.raid not in options.RAID:
            errors.append("Unknown RAID level %r." % self.raid)
        elif self.uses_raid:
            if self.alongside:
                errors.append("A RAID array cannot be created in the free space of a disk.")
            minimum = RAID_MINIMUM.get(self.raid, 2)
            members = [d for d in self.raid_disks if d]
            if len(members) < minimum - 1:
                errors.append(
                    "RAID%s needs %d disks in total; only %d were selected."
                    % (self.raid, minimum, len(members) + 1)
                )
            if len(set(members)) != len(members):
                errors.append("A disk was selected twice for the array.")
            for member in members:
                if member == self.disk:
                    errors.append("The installation disk cannot also be an array member.")
                elif not DISK_RE.match(member):
                    errors.append("Invalid array member %r." % member)
        if self.alongside and not self.keep_esp:
            errors.append(
                "No EFI system partition was found to keep. An installation alongside "
                "another system reuses that system's boot partition."
            )
        if self.root_fs == "btrfs" and self.separate_home:
            errors.append("Btrfs uses a /home subvolume; a separate /home partition is redundant.")

        # -- firmware and architecture --------------------------------------
        if self.secure_boot and not self.efi:
            errors.append("Secure Boot signing needs UEFI firmware.")
        if self.secure_boot and self.alongside:
            errors.append(
                "Signing the boot files would take over the other system's boot loader; "
                "turn Secure Boot signing off when installing alongside."
            )
        if self.arch not in options.ARCHES:
            errors.append("Unknown architecture %r." % self.arch)
        return errors

    def software_errors(self) -> list:
        """Only the answers collected on the Software page."""
        errors = []
        if self.desktop not in options.DESKTOPS:
            errors.append("Unknown desktop %r." % self.desktop)
        if self.kernel not in options.KERNELS:
            errors.append("Unknown kernel %r." % self.kernel)
        if self.microcode not in options.MICROCODE:
            errors.append("Unknown microcode choice %r." % self.microcode)
        if self.firmware not in options.FIRMWARE:
            errors.append("Unknown firmware choice %r." % self.firmware)
        seen = set()
        for package in self.packages:
            if not PACKAGE_RE.match(package):
                errors.append("Invalid package name %r." % package)
            elif package in seen:
                errors.append("%s was selected twice." % package)
            seen.add(package)
        for package in self.packages:
            if is_multilib_package(package) and not self.multilib:
                errors.append(
                    "%s needs 32-bit libraries: turn on multilib in Software." % package
                )
        if self.aur_packages and not self.aur:
            errors.append("AUR packages were listed but AUR support is turned off.")
        seen_aur = set()
        for package in self.aur_packages:
            if not AUR_RE.match(package):
                errors.append("Invalid AUR package name %r." % package)
            elif package in seen_aur:
                errors.append("%s was listed twice for the AUR." % package)
            seen_aur.add(package)
        if self.arch != "x86_64" and self.kernel != "linux":
            errors.append(
                "%s boots one kernel; choose linux for an %s installation."
                % (self.arch, self.arch)
            )
        return errors

    def system_errors(self) -> list:
        """Only the answers collected on the System page, passwords included."""
        errors = []
        if not HOSTNAME_RE.match(self.hostname):
            errors.append("Hostname may only contain letters, digits and hyphens.")
        if not TIMEZONE_RE.match(self.timezone) or ".." in self.timezone:
            errors.append("Invalid timezone.")
        if not LOCALE_RE.match(self.locale):
            errors.append("Invalid locale; it must be a UTF-8 locale such as en_US.UTF-8.")
        if not KEYMAP_RE.match(self.keymap):
            errors.append("Invalid keyboard layout.")
        if len(self.root_password) < 8:
            errors.append("The root password must be at least 8 characters.")
        if self.user is not None:
            if not USER_RE.match(self.user.name):
                errors.append("Usernames must start with a lower-case letter or underscore.")
            if self.user.name == "root":
                errors.append("The account cannot be named root.")
            if len(self.user.password) < 8:
                errors.append("The user password must be at least 8 characters.")
            for group in self.user.groups:
                if not GROUP_RE.match(group):
                    errors.append("Invalid group name %r." % group)
        if self.wifi_ssid:
            if len(self.wifi_ssid) > 32:
                errors.append("A Wi-Fi network name cannot be longer than 32 characters.")
            if any(ord(char) < 32 or ord(char) == 127 for char in self.wifi_ssid):
                errors.append("The Wi-Fi network name contains control characters.")
            if "\n" in self.wifi_password:
                errors.append("The Wi-Fi password cannot contain a line break.")
            if self.wifi_password and not 8 <= len(self.wifi_password) <= 63:
                errors.append("A Wi-Fi password must be between 8 and 63 characters.")
        elif self.wifi_password:
            errors.append("Enter the Wi-Fi network name that belongs to the password.")
        return errors

    def to_public(self) -> dict:
        data = {
            "disk": self.disk,
            "mode": self.mode,
            "efi": self.efi,
            "bootloader": self.resolved_bootloader,
            "rootFs": self.root_fs,
            "swap": self.swap,
            "swapGb": self.swap_gb,
            "espGb": self.esp_gb,
            "separateHome": self.separate_home,
            "encryption": self.encryption,
            "volumes": self.volumes,
            "raid": self.raid,
            "raidDisks": list(self.raid_disks),
            "keepEsp": self.keep_esp,
            "secureBoot": self.secure_boot,
            "aur": self.aur,
            "aurPackages": list(self.aur_packages),
            "arch": self.arch,
            "hostname": self.hostname,
            "timezone": self.timezone,
            "locale": self.locale,
            "keymap": self.keymap,
            "desktop": self.desktop,
            "kernel": self.kernel,
            "microcode": self.microcode,
            "firmware": self.firmware,
            "multilib": self.multilib,
            "packages": list(self.packages),
            "wifiSsid": self.wifi_ssid,
        }
        if self.user is not None:
            data["user"] = self.user.to_public()
        return data
