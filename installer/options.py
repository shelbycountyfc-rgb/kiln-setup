"""Fixed choices the wizard offers: desktops, kernels, keymaps, locales, zones."""

from __future__ import annotations

import os

#: name -> (label, short description)
DESKTOPS = {
    "minimal": ("No desktop", "A console system. Add a desktop later if you want one."),
    "gnome": ("GNOME", "Modern, touch-friendly desktop with the Activities overview."),
    "kde": ("KDE Plasma", "Highly configurable desktop, closest to a Windows layout."),
    "xfce": ("Xfce", "Light and familiar, good on older machines."),
    "cinnamon": ("Cinnamon", "Traditional layout with a taskbar and a menu."),
    "mate": ("MATE", "The classic GNOME 2 desktop, still maintained."),
    "lxqt": ("LXQt", "Very light Qt desktop for modest hardware."),
    "hyprland": ("Hyprland", "Tiling Wayland compositor for people who know they want it."),
}

#: name -> (label, package, note)
KERNELS = {
    "linux": ("linux", "linux", "The latest stable kernel (recommended)."),
    "linux-lts": ("linux-lts", "linux-lts", "Long-term support: older, very well tested."),
    "linux-zen": ("linux-zen", "linux-zen", "Tuned for desktop responsiveness."),
    "linux-hardened": ("linux-hardened", "linux-hardened", "Extra hardening, some features removed."),
}

#: name -> (label, description)
BOOTLOADERS = {
    "auto": ("Choose for me", "systemd-boot on UEFI, GRUB on legacy BIOS."),
    "systemd-boot": ("systemd-boot", "Simple UEFI boot manager. UEFI machines only."),
    "grub": ("GRUB", "Works on UEFI and legacy BIOS, more options and themes."),
}

FIRMWARE = {
    "auto": ("Match this computer", "Only the firmware for the chips this computer has."),
    "all": ("All firmware", "The full linux-firmware set, including hardware added later."),
}

MICROCODE = {
    "auto": ("Detect for this CPU", "amd-ucode or intel-ucode, chosen from the CPU vendor."),
    "none": ("Do not install", "Only for virtual machines."),
    "intel-ucode": ("intel-ucode", "Intel CPU microcode updates."),
    "amd-ucode": ("amd-ucode", "AMD CPU microcode updates."),
}

#: Console keymaps shipped by the ``kbd`` package, a practical subset.
KEYMAPS = (
    "us", "uk", "de", "de-latin1", "de-latin1-nodeadkeys", "fr", "fr-latin1",
    "es", "it", "pt-latin1", "nl", "be-latin1", "dk", "no", "se", "fi", "is",
    "pl", "cz", "sk", "hu", "ro", "bg", "gr", "ru", "ua", "tr", "il", "br-abnt2",
    "latam", "ca", "ch-de", "ch-fr", "jp106", "kr", "us-acentos",
)

#: UTF-8 locales (the form ``locale.gen`` expects).
LOCALES = (
    "en_US.UTF-8", "en_GB.UTF-8", "en_CA.UTF-8", "en_AU.UTF-8", "de_DE.UTF-8",
    "fr_FR.UTF-8", "es_ES.UTF-8", "it_IT.UTF-8", "pt_PT.UTF-8", "pt_BR.UTF-8",
    "nl_NL.UTF-8", "sv_SE.UTF-8", "nb_NO.UTF-8", "da_DK.UTF-8", "fi_FI.UTF-8",
    "pl_PL.UTF-8", "cs_CZ.UTF-8", "sk_SK.UTF-8", "hu_HU.UTF-8", "ro_RO.UTF-8",
    "ru_RU.UTF-8", "uk_UA.UTF-8", "tr_TR.UTF-8", "el_GR.UTF-8", "he_IL.UTF-8",
    "ar_SA.UTF-8", "ja_JP.UTF-8", "ko_KR.UTF-8", "zh_CN.UTF-8", "zh_TW.UTF-8",
    "hi_IN.UTF-8", "id_ID.UTF-8", "vi_VN.UTF-8", "th_TH.UTF-8",
)

#: A geography-ordered list of common zones; any zone in /usr/share/zoneinfo is
#: accepted, the list is only the dropdown.
TIMEZONES = (
    "UTC",
    "Europe/London", "Europe/Dublin", "Europe/Lisbon", "Europe/Madrid",
    "Europe/Paris", "Europe/Brussels", "Europe/Amsterdam", "Europe/Berlin",
    "Europe/Zurich", "Europe/Vienna", "Europe/Rome", "Europe/Prague",
    "Europe/Warsaw", "Europe/Budapest", "Europe/Bucharest", "Europe/Athens",
    "Europe/Kyiv", "Europe/Istanbul", "Europe/Moscow", "Europe/Stockholm",
    "Europe/Oslo", "Europe/Copenhagen", "Europe/Helsinki", "Europe/Riga",
    "Europe/Vilnius", "Europe/Tallinn", "Europe/Belgrade", "Europe/Zagreb",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Phoenix", "America/Anchorage",
    "America/Toronto", "America/Vancouver", "America/Mexico_City",
    "America/Bogota", "America/Lima", "America/Santiago", "America/Sao_Paulo",
    "America/Argentina/Buenos_Aires", "Pacific/Honolulu",
    "Asia/Jerusalem", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata",
    "Asia/Dhaka", "Asia/Bangkok", "Asia/Jakarta", "Asia/Shanghai",
    "Asia/Hong_Kong", "Asia/Taipei", "Asia/Seoul", "Asia/Tokyo",
    "Asia/Manila", "Asia/Singapore", "Australia/Perth", "Australia/Adelaide",
    "Australia/Brisbane", "Australia/Sydney", "Australia/Melbourne",
    "Pacific/Auckland", "Africa/Cairo", "Africa/Lagos", "Africa/Johannesburg",
    "Africa/Nairobi", "Africa/Casablanca",
)


def timezones() -> list:
    """Every zone the live medium knows, falling back to the curated list."""
    base = "/usr/share/zoneinfo"
    found = []
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("posix", "right")]
            for name in files:
                path = os.path.join(root, name)
                rel = os.path.relpath(path, base)
                if rel in ("iso3166.tab", "zone.tab", "zone1970.tab", "leap-seconds.list"):
                    continue
                if rel.startswith(("posix/", "right/")):
                    continue
                found.append(rel)
    return sorted(found) if found else list(TIMEZONES)


def desktop_packages(name: str) -> list:
    """Packages that make up a desktop, with the session manager it needs."""
    name = (name or "minimal").lower()
    table = {
        "minimal": [],
        "gnome": ["gnome", "gnome-tweaks", "gdm"],
        "kde": ["plasma-meta", "kde-applications-meta", "sddm"],
        "xfce": ["xfce4", "xfce4-goodies", "lightdm", "lightdm-gtk-greeter"],
        "cinnamon": ["cinnamon", "lightdm", "lightdm-gtk-greeter"],
        "mate": ["mate", "mate-extra", "lightdm", "lightdm-gtk-greeter"],
        "lxqt": ["lxqt", "sddm"],
        "hyprland": ["hyprland", "kitty", "waybar", "wofi", "xdg-desktop-portal-hyprland"],
    }
    packages = list(table.get(name, []))
    if packages:
        # Sound, fonts and a browser are what "a desktop" means in practice.
        packages += ["pipewire", "pipewire-pulse", "wireplumber", "noto-fonts", "noto-fonts-emoji"]
    return packages


#: How the disk is used: wiped, or shared with a system that is already there.
INSTALL_MODES = {
    "erase": ("Erase the disk", "The whole disk is used; every partition on it is deleted."),
    "alongside": (
        "Install alongside",
        "Keep the existing system and install into the free space, reusing its EFI partition.",
    ),
}

#: Full-disk encryption of the root filesystem.
ENCRYPTION = {
    "none": ("No encryption", "The disk can be read by anyone who has the machine."),
    "luks": ("LUKS2 encryption", "The root filesystem is unlocked with a passphrase at boot."),
}

#: What the filesystem sits on inside the root partition.
VOLUMES = {
    "partitions": ("Plain partitions", "The filesystem sits directly on the partition."),
    "lvm": ("LVM", "A volume group makes resizing, snapshots and extra volumes easier."),
}

#: mdadm levels the wizard offers, and what each one costs.
RAID = {
    "none": ("No array", "One disk holds the system."),
    "1": ("RAID 1 (mirror)", "Every write goes to all disks; survives a disk failure."),
    "5": ("RAID 5 (parity)", "The space of one disk holds parity; survives one failure."),
    "10": ("RAID 10 (mirror and stripe)", "Fast and redundant; needs at least four disks."),
}

#: Signing the boot files so Secure Boot accepts them.
SECURE_BOOT = {
    "off": ("Do not sign", "Secure Boot has to stay off, as for any Arch install medium."),
    "sbctl": ("Sign with sbctl", "Own keys and signatures for the kernel and the boot loader."),
}

#: How the container is unlocked while the machine boots.
UNLOCK = {
    "passphrase": (
        "Passphrase",
        "Typed at every boot. Nothing else is needed.",
    ),
    "keyfile": (
        "Key file",
        "A random key is added to the container and kept inside the initramfs, so the "
        "passphrase is only needed for recovery.",
    ),
    "tpm2": (
        "TPM 2.0",
        "The firmware measures the boot chain and releases the key through "
        "systemd-cryptenroll; the passphrase still works as a fallback.",
    ),
}

#: Countries the mirror chooser offers, in reflector's own spelling.
MIRROR_COUNTRIES = (
    "Worldwide", "Australia", "Austria", "Belgium", "Brazil", "Bulgaria",
    "Canada", "Chile", "China", "Croatia", "Czechia", "Denmark", "Estonia",
    "Finland", "France", "Germany", "Greece", "Hong Kong", "Hungary", "Iceland",
    "India", "Indonesia", "Ireland", "Israel", "Italy", "Japan", "Kazakhstan",
    "Kenya", "Latvia", "Lithuania", "Luxembourg", "Mexico", "Netherlands",
    "New Zealand", "Norway", "Poland", "Portugal", "Romania", "Russia",
    "Singapore", "Slovakia", "Slovenia", "South Africa", "South Korea", "Spain",
    "Sweden", "Switzerland", "Taiwan", "Thailand", "Turkey", "Ukraine",
    "United Kingdom", "United States", "Vietnam",
)

#: Architectures the installer can put on the disk.
ARCHES = {
    "x86_64": ("x86_64 (64-bit PC)", "The usual desktop and laptop architecture."),
    "aarch64": ("aarch64 (64-bit ARM)", "ARM machines; needs a medium built for aarch64."),
}

#: Whether packages may be built from the AUR.
AUR = {
    "off": ("Official repositories only", "Every package is signed and comes from Arch."),
    "on": ("Allow AUR packages", "Recipes from the AUR are built on this machine while installing."),
}


def desktop_service(name: str) -> str:
    """Display manager unit for a desktop, or ``""`` when there is none."""
    return {
        "gnome": "gdm",
        "kde": "sddm",
        "lxqt": "sddm",
        "xfce": "lightdm",
        "cinnamon": "lightdm",
        "mate": "lightdm",
    }.get((name or "").lower(), "")
