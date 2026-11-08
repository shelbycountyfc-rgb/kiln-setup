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
#: One kernel parameter: the characters the kernel command line accepts.
KERNEL_PARAM_RE = re.compile(r"^[A-Za-z0-9_.:+@%,/=~-]{1,128}$")


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
    #: Swap as a file on the root instead of a partition of its own.
    swap_file: bool = False
    esp_gb: float = 1.0
    separate_home: bool = False
    # -- storage stack ------------------------------------------------------
    encryption: str = "none"
    luks_passphrase: str = ""
    volumes: str = options.DEFAULT_VOLUME
    raid: str = "none"
    raid_disks: list = field(default_factory=list)
    keep_esp: str = ""
    #: An existing partition to shrink first, so there is room to install in.
    shrink_partition: str = ""
    shrink_gb: float = 0.0
    #: Keep the kernel and the initramfs inside the container, on "/boot".
    encrypt_boot: bool = False
    unlock: str = "passphrase"
    # -- firmware and software ---------------------------------------------
    secure_boot: bool = False
    #: Lock the kernel down and keep a unit that reports whether it held.
    hardening: bool = False
    #: Sign every kernel module with a key this machine keeps.
    signed_modules: bool = False
    #: One signed .efi holding kernel, initramfs and command line.
    uki: bool = False
    #: Keep the hardware clock in local time, the way Windows does.
    clock_local: bool = False
    #: A default-deny packet filter, and an SSH server.
    firewall: bool = False
    sshd: bool = False
    #: Btrfs snapshots: a timeline, and a menu entry per snapshot with GRUB.
    snapshots: bool = False
    #: Extra locales generated beside the main one.
    locales: list = field(default_factory=list)
    #: Desktop layout, when it should differ from the console keymap.
    x11_keymap: str = ""
    #: A reinstall: the existing partition to format as the new root, and an
    #: existing partition to mount as /home without touching it.
    reuse_root: str = ""
    reuse_home: str = ""
    #: The country reflector keeps the mirrorlist in step with.
    mirror_country: str = ""
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
    #: Extra kernel parameters, exactly as they should appear on the command line.
    kernel_params: str = ""
    #: Set up the kernel the way Epic's anti-cheat for Linux requires.
    eac: bool = False
    hibernate: bool = False
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
        # A swap file is a kind of swap, not a replacement for it: with swap
        # turned off there is nothing for the file to be.  The choice stays in
        # the form, so ticking swap again brings the file back.
        config.swap_file = bool(data.get("swapFile", False)) and config.swap
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
        config.volumes = (
            str(data.get("volumes", options.DEFAULT_VOLUME)).strip().lower()
            or options.DEFAULT_VOLUME
        )
        config.raid = str(data.get("raid", "none")).strip().lower() or "none"
        raid_disks = data.get("raidDisks") or []
        if isinstance(raid_disks, str):
            raid_disks = [d for d in re.split(r"[\s,]+", raid_disks) if d]
        config.raid_disks = [str(d).strip() for d in raid_disks if str(d).strip()]
        if not config.uses_raid:
            # "No array" is the default: a member disk list left over from an
            # earlier RAID choice must not travel with the configuration.
            config.raid_disks = []
        config.keep_esp = str(data.get("keepEsp", "")).strip()
        config.shrink_partition = str(data.get("shrinkPartition", "")).strip()
        try:
            config.shrink_gb = float(data.get("shrinkGb") or 0)
        except (TypeError, ValueError):
            config.shrink_gb = 0.0
        config.encrypt_boot = bool(data.get("encryptBoot", False))
        config.unlock = (
            str(data.get("unlock", "passphrase")).strip().lower() or "passphrase"
        )
        config.eac = bool(data.get("eac", False))
        config.secure_boot = bool(data.get("secureBoot", False))
        config.hardening = bool(data.get("hardening", False))
        config.signed_modules = bool(data.get("signedModules", False))
        if config.hardening:
            # A locked-down kernel without Secure Boot is a claim the firmware
            # does not back up, so the two go together.
            config.secure_boot = True
        if config.eac:
            # EAC Linux v2 refuses a machine without Secure Boot, so asking
            # for its kernel profile brings the signing with it.
            config.secure_boot = True
        config.uki = bool(data.get("uki", False))
        config.clock_local = bool(data.get("clockLocal", False))
        config.firewall = bool(data.get("firewall", False))
        config.sshd = bool(data.get("sshd", False))
        config.snapshots = bool(data.get("snapshots", False))
        locales = data.get("locales") or []
        if isinstance(locales, str):
            locales = [entry for entry in re.split(r"[\s,]+", locales) if entry]
        config.locales = [str(entry).strip() for entry in locales if str(entry).strip()]
        config.x11_keymap = str(data.get("x11Keymap", "")).strip()
        config.reuse_root = str(data.get("reuseRoot", "")).strip()
        config.reuse_home = str(data.get("reuseHome", "")).strip()
        config.mirror_country = str(data.get("mirrorCountry", "")).strip()
        config.aur = bool(data.get("aur", False))
        aur_packages = data.get("aurPackages") or []
        if isinstance(aur_packages, str):
            aur_packages = [p for p in re.split(r"[\s,]+", aur_packages) if p]
        config.aur_packages = [str(p).strip() for p in aur_packages if str(p).strip()]
        config.arch = str(data.get("arch", "x86_64")).strip().lower() or "x86_64"
        if config.mode == "alongside":
            # There is no free space to give a swap partition of our own; a swap
            # file lives on the root instead.
            config.swap = bool(config.swap_file)
            config.separate_home = False
        if config.mode == "reuse":
            # The partitions already exist: only a swap file fits, and an array
            # cannot be built over partitions that were in use a moment ago.
            config.swap = bool(config.swap_file)
            config.separate_home = False
            config.raid = "none"
            config.raid_disks = []
        config.hostname = str(data.get("hostname", "archlinux")).strip() or "archlinux"
        config.timezone = str(data.get("timezone", "UTC")).strip() or "UTC"
        config.locale = str(data.get("locale", "en_US.UTF-8")).strip() or "en_US.UTF-8"
        config.keymap = str(data.get("keymap", "us")).strip() or "us"
        config.desktop = str(data.get("desktop", "kde")).strip().lower() or "kde"
        config.kernel = str(data.get("kernel", "linux")).strip().lower() or "linux"
        config.microcode = str(data.get("microcode", "auto")).strip().lower() or "auto"
        config.firmware = str(data.get("firmware", "auto")).strip().lower() or "auto"
        config.multilib = bool(data.get("multilib", True))
        config.kernel_params = str(data.get("kernelParams", "")).strip()
        config.hibernate = bool(data.get("hibernate", False))
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
    def reuse(self) -> bool:
        """A reinstall into a partition that already exists."""
        return self.mode == "reuse"

    @property
    def encrypted_boot(self) -> bool:
        """The kernel and the initramfs live inside the LUKS container."""
        return self.encrypted and self.encrypt_boot

    @property
    def kernel_param_list(self) -> list:
        return [token for token in self.kernel_params.split() if token]

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
        if self.reuse:
            if not DISK_RE.match(self.reuse_root):
                errors.append(
                    "A reinstall has to name the existing partition to format as root."
                )
            elif not self.reuse_root.startswith(self.disk):
                errors.append(
                    "The partition to reinstall into has to be on the selected disk."
                )
            if self.reuse_home:
                if not DISK_RE.match(self.reuse_home):
                    errors.append("Invalid partition %r for /home." % self.reuse_home)
                elif self.reuse_home == self.reuse_root:
                    errors.append("The root and /home cannot be the same partition.")
                elif self.reuse_home == self.keep_esp:
                    errors.append("/home cannot be the EFI system partition.")
            if self.uses_raid:
                errors.append(
                    "A reinstall cannot build an array from a disk already in use."
                )
            if self.efi and not self.keep_esp:
                errors.append(
                    "No EFI system partition was found to keep. A reinstall reuses "
                    "the boot partition that is already there."
                )
        if self.root_fs == "btrfs" and self.separate_home:
            errors.append("Btrfs uses a /home subvolume; a separate /home partition is redundant.")

        # -- making room, unlocking, booting ---------------------------------
        if self.shrink_partition:
            if not self.alongside:
                errors.append(
                    "Shrinking a partition is only used when installing alongside "
                    "another system."
                )
            elif not DISK_RE.match(self.shrink_partition):
                errors.append("Invalid partition to shrink %r." % self.shrink_partition)
            elif not 1 <= self.shrink_gb <= 4096:
                errors.append(
                    "A shrunk partition has to end up between 1 and 4096 GiB."
                )
        elif self.shrink_gb:
            errors.append(
                "Choose the partition to shrink as well as the size it should end up."
            )
        if self.encrypt_boot:
            if not self.encrypted:
                errors.append("Encrypting /boot needs an encrypted root as well.")
            if self.resolved_bootloader != "grub":
                errors.append(
                    "Only GRUB can boot a kernel that lives inside the container."
                )
            if self.mode != "erase":
                errors.append(
                    "Sharing a disk with another system keeps /boot on the EFI "
                    "partition it reuses, so /boot cannot be encrypted."
                )
        if self.unlock not in options.UNLOCK:
            errors.append("Unknown unlock method %r." % self.unlock)
        elif self.unlock != "passphrase" and not self.encrypted:
            errors.append("A key file or TPM unlock needs an encrypted root.")

        # -- firmware and architecture --------------------------------------
        if self.hardening and not self.efi:
            errors.append("Kernel lockdown with Secure Boot needs UEFI firmware.")
        if self.secure_boot and self.signed_modules and not self.efi:
            errors.append("Signing the boot files needs UEFI firmware.")
        if self.secure_boot and not self.efi:
            errors.append("Secure Boot signing needs UEFI firmware.")
        if self.secure_boot and self.mode != "erase":
            errors.append(
                "Signing the boot files would take over the boot loader of the "
                "system sharing the disk; turn Secure Boot signing off for this mode."
            )
        if self.uki:
            if not self.efi:
                errors.append("A unified kernel image needs UEFI firmware.")
            elif self.resolved_bootloader != "systemd-boot":
                errors.append(
                    "A unified kernel image is built for systemd-boot; choose that "
                    "boot loader, or turn the unified image off."
                )
        if self.snapshots and self.root_fs != "btrfs":
            errors.append(
                "Snapshots need a Btrfs root: the subvolumes are what they keep."
            )
        if self.hibernate and self.swap_file and self.root_fs in ("f2fs", "bcachefs"):
            errors.append(
                "Hibernation from a swap file needs a file system whose layout can "
                "be read before it is mounted; %s cannot, so use a swap partition."
                % self.root_fs
            )
        if self.arch != "x86_64" and not self.efi:
            errors.append(
                "Legacy BIOS is x86 only: an %s installation boots through UEFI."
                % self.arch
            )
        if self.x11_keymap and not KEYMAP_RE.match(self.x11_keymap):
            errors.append("Invalid desktop keyboard layout.")
        for extra in self.locales:
            if not LOCALE_RE.match(extra):
                errors.append("Invalid locale %r in the extra list." % extra)
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
        if self.hibernate:
            if not self.swap:
                errors.append("Hibernation needs swap to write the image of memory into.")
            if self.alongside and not self.swap_file:
                errors.append(
                    "There is no swap partition of our own in the free space of the "
                    "disk to hibernate into; choose a swap file instead."
                )
        if self.swap_file and not self.swap:
            errors.append("A swap file needs swap to be turned on as well.")
        if self.eac and not self.efi:
            errors.append(
                "Epic Games anti-cheat runs only with UEFI firmware, Secure Boot "
                "and a measured boot, so it cannot be set up on a BIOS install."
            )
        if len(self.kernel_params) > 400:
            errors.append("The kernel command line is limited to 400 characters.")
        for token in self.kernel_param_list:
            if not KERNEL_PARAM_RE.match(token):
                errors.append(
                    "Kernel parameter %r contains characters that do not belong there."
                    % token
                )
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
            "swapFile": self.swap_file,
            "espGb": self.esp_gb,
            "separateHome": self.separate_home,
            "encryption": self.encryption,
            "volumes": self.volumes,
            "raid": self.raid,
            "raidDisks": list(self.raid_disks),
            "keepEsp": self.keep_esp,
            "shrinkPartition": self.shrink_partition,
            "shrinkGb": self.shrink_gb,
            "encryptBoot": self.encrypt_boot,
            "unlock": self.unlock,
            "kernelParams": self.kernel_params,
            "hibernate": self.hibernate,
            "secureBoot": self.secure_boot,
            "hardening": self.hardening,
            "eac": self.eac,
            "signedModules": self.signed_modules,
            "uki": self.uki,
            "clockLocal": self.clock_local,
            "firewall": self.firewall,
            "sshd": self.sshd,
            "snapshots": self.snapshots,
            "locales": list(self.locales),
            "x11Keymap": self.x11_keymap,
            "reuseRoot": self.reuse_root,
            "reuseHome": self.reuse_home,
            "mirrorCountry": self.mirror_country,
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
