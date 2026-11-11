"""Choose the answers that fit the machine the system is being installed on.

The wizard used to start from the same values on every computer: KDE Plasma,
UTC, ``us``, en_US, and whatever graphics stack was assumed.  Everything here
reads the detected hardware - and, for language, the settings the live medium
itself was booted with - and returns the answers this computer needs, each with
the sentence the Hardware page shows next to it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import options
from .context import human_bytes
from .disk import recommended_swap
from .hardware import AMD, INTEL

GIB = 1024 ** 3

#: A machine with less memory than this is unhappy with a full desktop.
LIGHT_DESKTOP = 4 * GIB
#: Below this, even a light desktop is a stretch and a lean Qt one fits better.
MINIMAL_DESKTOP = 2 * GIB


@dataclass
class Recommendation:
    """The answers proposed for one machine, and why."""

    values: dict = field(default_factory=dict)
    packages: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "values": dict(self.values),
            "packages": list(self.packages),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# What the live medium is already set to
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def live_settings() -> dict:
    """Locale, keymap and timezone the live medium booted with.

    A medium built for a different region (or a user who ran ``localectl``
    before starting the installer) already carries the right answers, so they
    are read back instead of being reset to ``us``/``UTC``.
    """
    settings = {}

    match = re.search(r"^\s*KEYMAP\s*=\s*(\S+)\s*$", _read("/etc/vconsole.conf"), re.M)
    if match and match.group(1) in options.KEYMAPS:
        settings["keymap"] = match.group(1)

    match = re.search(r"^\s*LANG\s*=\s*(\S+)\s*$", _read("/etc/locale.conf"), re.M)
    if match and match.group(1) in options.LOCALES:
        settings["locale"] = match.group(1)

    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        zone = target.split("zoneinfo/", 1)[1]
        if zone and ".." not in zone:
            settings["timezone"] = zone
    return settings


# --------------------------------------------------------------------------- #
# Software this machine needs
# --------------------------------------------------------------------------- #
def _desktop(hw) -> str:
    """The heaviest desktop that still leaves this machine comfortable."""
    if hw.ram_bytes <= MINIMAL_DESKTOP:
        return "lxqt"
    if hw.ram_bytes < LIGHT_DESKTOP:
        return "xfce"
    return "kde"


def _desktop_note(hw) -> str:
    memory = human_bytes(hw.ram_bytes)
    desktop = _desktop(hw)
    if desktop == "lxqt":
        return "LXQt, because %s of memory is too little for a full desktop." % memory
    if desktop == "xfce":
        # Both Xfce choices fit this much memory and they differ only in how
        # they look, so the note names both instead of sending the machine to
        # one of them and hiding the other.
        looks = " or ".join(options.DESKTOPS[key][0] for key in ("xfce", "xfce4"))
        return "%s: %s of memory is tight for a full desktop." % (looks, memory)
    return "KDE Plasma, which %s of memory handles comfortably." % memory


def _graphics(hw, kernel: str, packages: list, notes: list) -> None:
    vendors = {gpu.vendor for gpu in hw.gpus}
    if "NVIDIA" in vendors:
        if kernel == "linux":
            packages.append("nvidia-open")
            notes.append(
                ("Graphics", "NVIDIA adapter: the open kernel modules (nvidia-open) are installed.")
            )
        else:
            # The proprietary and open drivers are built per kernel series; a
            # package for another series would not boot.
            notes.append(
                (
                    "Graphics",
                    "NVIDIA adapter: install the driver matching %s after the first boot; "
                    "none is bundled here because it is not built for that kernel." % kernel,
                )
            )
    elif AMD in vendors or "AMD" in vendors:
        packages.extend(["mesa", "vulkan-radeon", "libva-mesa-driver"])
        notes.append(
            ("Graphics", "AMD graphics: Mesa, the Radeon Vulkan driver and hardware video decoding.")
        )
    elif "Intel" in vendors:
        packages.extend(["mesa", "vulkan-intel", "intel-media-driver"])
        notes.append(
            ("Graphics", "Intel graphics: Mesa, the Intel Vulkan driver and media acceleration.")
        )
    else:
        packages.append("mesa")
        notes.append(("Graphics", "No graphics adapter was identified, so only Mesa is installed."))


def _software(hw, kernel: str) -> tuple:
    """Extra packages for this machine, and the reasons for them."""
    packages: list = []
    notes: list = []

    _graphics(hw, kernel, packages, notes)

    if hw.battery:
        packages.append("power-profiles-daemon")
        notes.append(
            (
                "Power",
                "Laptop battery found: the power profiles (balanced, performance, saver) "
                "are available in the desktop.",
            )
        )

    if hw.bluetooth:
        packages.extend(["bluez", "bluez-utils"])
        notes.append(("Bluetooth", "Bluetooth adapter found: BlueZ is installed and bluetoothd enabled."))

    if hw.battery and hw.cpu_vendor == INTEL and hw.audio:
        packages.append("sof-firmware")
        notes.append(
            (
                "Audio",
                "Intel laptop: the Sound Open Firmware is installed so the internal "
                "speakers and microphone work.",
            )
        )

    if hw.touchpad:
        packages.append("xf86-input-libinput")
        notes.append(("Touchpad", "Touchpad found: the libinput driver is installed for it."))

    seen = set()
    unique = []
    for package in packages:
        if package and package not in seen:
            seen.add(package)
            unique.append(package)
    return unique, notes


# --------------------------------------------------------------------------- #
# The recommendation
# --------------------------------------------------------------------------- #
def recommend(hw, live: dict | None = None) -> Recommendation:
    """Every answer this machine's hardware implies, with its reason."""
    if live is None:
        live = live_settings()

    values = {
        "efi": hw.firmware == "uefi",
        "bootloader": "auto",
        "desktop": _desktop(hw),
        "kernel": "linux",
        "microcode": "auto",
        "firmware": "auto",
        "keymap": live.get("keymap", "us"),
        "locale": live.get("locale", "en_US.UTF-8"),
        "timezone": live.get("timezone", "UTC"),
        "swapGb": round(recommended_swap(hw.ram_bytes) / GIB, 1),
    }

    packages, notes = _software(hw, values["kernel"])

    header = [("Desktop", _desktop_note(hw))]
    header.append(
        (
            "Boot loader",
            "systemd-boot, because this machine booted in UEFI mode."
            if values["efi"]
            else "GRUB, because this machine booted in legacy BIOS mode.",
        )
    )
    if hw.microcode:
        header.append(("Microcode", "%s, matching the %s CPU." % (hw.microcode, hw.cpu_vendor_label)))
    header.append(
        (
            "Firmware",
            "Only the firmware packages for the adapters this computer has; "
            "choose All firmware in Software for the full set."
            if getattr(hw, "firmware_vendors", None)
            else "The full firmware set, because no adapter was identified to narrow it down.",
        )
    )
    header.append(
        (
            "Swap",
            "%s GB, matched to the %s of memory."
            % (values["swapGb"], human_bytes(hw.ram_bytes)),
        )
    )

    live_keys = ("keymap", "locale", "timezone")
    taken = [key for key in live_keys if key in live]
    if taken:
        header.append(
            (
                "Language",
                "%s, taken from the settings this live medium booted with."
                % ", ".join("%s=%s" % (key, values[key]) for key in taken),
            )
        )

    return Recommendation(values=values, packages=packages, notes=header + notes)
