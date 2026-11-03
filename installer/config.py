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
from .disk import FILESYSTEMS, MIN_DISK_BYTES

DEFAULT_GROUPS = ("wheel", "audio", "video", "optical", "storage", "scanner", "power")

HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEYMAP_RE = re.compile(r"^[a-z0-9_-]{2,24}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?\.UTF-8$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
DISK_RE = re.compile(r"^/dev/[a-zA-Z0-9/_.-]{1,60}$")
#: Arch package names are lower case, and may carry @ . _ + -
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9@._+-]{0,79}$")
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
    #: Only whole-disk installation is offered; the field exists so a later
    #: "install alongside" mode does not have to change the schema.
    mode: str = "erase"
    confirm_erase: bool = False
    efi: bool = True
    bootloader: str = "auto"
    root_fs: str = "ext4"
    swap: bool = True
    swap_gb: float | None = None
    esp_gb: float = 1.0
    separate_home: bool = False
    hostname: str = "archlinux"
    timezone: str = "UTC"
    locale: str = "en_US.UTF-8"
    keymap: str = "us"
    desktop: str = "kde"
    kernel: str = "linux"
    microcode: str = "auto"
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
        config.hostname = str(data.get("hostname", "archlinux")).strip() or "archlinux"
        config.timezone = str(data.get("timezone", "UTC")).strip() or "UTC"
        config.locale = str(data.get("locale", "en_US.UTF-8")).strip() or "en_US.UTF-8"
        config.keymap = str(data.get("keymap", "us")).strip() or "us"
        config.desktop = str(data.get("desktop", "kde")).strip().lower() or "kde"
        config.kernel = str(data.get("kernel", "linux")).strip().lower() or "linux"
        config.microcode = str(data.get("microcode", "auto")).strip().lower() or "auto"
        config.multilib = bool(data.get("multilib", True))
        packages = data.get("packages") or []
        if isinstance(packages, str):
            packages = [p for p in re.split(r"[\s,]+", packages) if p]
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

    # -- validation --------------------------------------------------------
    def validate(self, disk_size: int = 0) -> list:
        errors = []
        if self.mode != "erase":
            errors.append("Only a whole-disk installation is supported.")
        if not DISK_RE.match(self.disk):
            errors.append("Select the disk Arch Linux should be installed on (/dev/...).")
        elif disk_size and disk_size < MIN_DISK_BYTES:
            errors.append(
                "The selected disk is smaller than 20 GiB; Arch Linux needs at least that much."
            )
        if not self.confirm_erase:
            errors.append("The disk erase confirmation must be accepted.")
        if self.root_fs not in FILESYSTEMS:
            errors.append("Unsupported filesystem %r." % self.root_fs)
        if self.bootloader not in options.BOOTLOADERS:
            errors.append("Unknown boot loader %r." % self.bootloader)
        if not self.efi and self.resolved_bootloader == "systemd-boot":
            errors.append("systemd-boot needs UEFI. Choose GRUB on a legacy BIOS machine.")
        if self.desktop not in options.DESKTOPS:
            errors.append("Unknown desktop %r." % self.desktop)
        if self.kernel not in options.KERNELS:
            errors.append("Unknown kernel %r." % self.kernel)
        if self.microcode not in options.MICROCODE:
            errors.append("Unknown microcode choice %r." % self.microcode)
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
        if not 0.1 <= self.esp_gb <= 4:
            errors.append("The EFI system partition must be between 0.1 and 4 GiB.")
        if self.swap_gb is not None and not 0 <= self.swap_gb <= 4096:
            errors.append("Swap size is out of range.")
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
            "hostname": self.hostname,
            "timezone": self.timezone,
            "locale": self.locale,
            "keymap": self.keymap,
            "desktop": self.desktop,
            "kernel": self.kernel,
            "microcode": self.microcode,
            "multilib": self.multilib,
            "packages": list(self.packages),
            "wifiSsid": self.wifi_ssid,
        }
        if self.user is not None:
            data["user"] = self.user.to_public()
        return data
