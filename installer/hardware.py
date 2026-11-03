"""Hardware detection for the machine the medium booted on.

Everything here is read-only: ``/proc``, ``/sys``, ``lspci`` and ``lsblk``.  A
failed probe never stops the installer; the field is simply left empty and the
pages say so.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .context import Context, human_bytes

#: Vendor ids used to pick the microcode package, per the installation guide.
INTEL = "GenuineIntel"
AMD = "AuthenticAMD"


@dataclass
class Partition:
    device: str
    number: int
    size: int
    fstype: str = ""
    mountpoint: str = ""
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "number": self.number,
            "size": self.size,
            "sizeText": human_bytes(self.size),
            "fstype": self.fstype,
            "mountpoint": self.mountpoint,
            "label": self.label,
        }


@dataclass
class Disk:
    device: str
    size: int
    model: str = ""
    removable: bool = False
    transport: str = ""
    partitions: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.device.rsplit("/", 1)[-1]

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "name": self.name,
            "size": self.size,
            "sizeText": human_bytes(self.size),
            "model": self.model,
            "removable": self.removable,
            "transport": self.transport,
            "partitions": [p.to_dict() for p in self.partitions],
        }


@dataclass
class Gpu:
    vendor: str = "Unknown"
    device: str = "Graphics device"
    driver: str = ""

    def to_dict(self) -> dict:
        return {"vendor": self.vendor, "device": self.device, "driver": self.driver}


@dataclass
class HardwareProfile:
    cpu_model: str = "Unknown CPU"
    cpu_vendor: str = ""
    cpu_cores: int = 1
    cpu_threads: int = 1
    ram_bytes: int = 0
    firmware: str = "bios"
    secure_boot: bool = False
    disks: list = field(default_factory=list)
    gpus: list = field(default_factory=list)
    network: list = field(default_factory=list)
    audio: list = field(default_factory=list)

    # -- derived values the pages and the engine use ------------------------
    @property
    def microcode(self) -> str:
        """``intel-ucode``, ``amd-ucode`` or ``""`` for this CPU."""
        if self.cpu_vendor == INTEL:
            return "intel-ucode"
        if self.cpu_vendor == AMD:
            return "amd-ucode"
        return ""

    @property
    def cpu_vendor_label(self) -> str:
        if self.cpu_vendor == INTEL:
            return "Intel"
        if self.cpu_vendor == AMD:
            return "AMD"
        return self.cpu_vendor or "Unknown"

    def internal_disks(self) -> list:
        return [d for d in self.disks if not d.removable]

    def to_dict(self) -> dict:
        return {
            "cpuModel": self.cpu_model,
            "cpuVendor": self.cpu_vendor,
            "cores": self.cpu_cores,
            "threads": self.cpu_threads,
            "ram": self.ram_bytes,
            "ramText": human_bytes(self.ram_bytes),
            "firmware": self.firmware,
            "secureBoot": self.secure_boot,
            "microcode": self.microcode,
            "disks": [d.to_dict() for d in self.disks],
            "gpus": [g.to_dict() for g in self.gpus],
            "network": self.network,
            "audio": self.audio,
        }


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _cpu(hw: HardwareProfile) -> None:
    text = _read("/proc/cpuinfo")
    if not text:
        return
    model = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
    vendor = re.search(r"^vendor_id\s*:\s*(.+)$", text, re.M)
    if model:
        hw.cpu_model = re.sub(r"\s+", " ", model.group(1)).strip()
    if vendor:
        hw.cpu_vendor = vendor.group(1).strip()
    # A "core" is one physical core: distinct (physical id, core id) pairs.
    pairs = set()
    for block in text.split("\n\n"):
        physical = re.search(r"^physical id\s*:\s*(\d+)$", block, re.M)
        core = re.search(r"^core id\s*:\s*(\d+)$", block, re.M)
        if physical and core:
            pairs.add((physical.group(1), core.group(1)))
    threads = len(re.findall(r"^processor\s*:", text, re.M)) or 1
    hw.cpu_threads = max(1, threads)
    hw.cpu_cores = len(pairs) or hw.cpu_threads


def _memory(hw: HardwareProfile) -> None:
    match = re.search(r"^MemTotal:\s*(\d+)\s*kB", _read("/proc/meminfo"), re.M)
    if match:
        hw.ram_bytes = int(match.group(1)) * 1024


def _firmware(hw: HardwareProfile) -> None:
    if not os.path.isdir("/sys/firmware/efi"):
        hw.firmware = "bios"
        hw.secure_boot = False
        return
    hw.firmware = "uefi"
    # efivars entries carry 4 bytes of attributes, then the value.
    for name in os.listdir("/sys/firmware/efi/efivars") if os.path.isdir("/sys/firmware/efi/efivars") else []:
        if name.startswith("SecureBoot-"):
            raw = _read(os.path.join("/sys/firmware/efi/efivars", name))
            hw.secure_boot = bool(raw) and raw[-1] == "\x01"
            break


def _disks(ctx: Context, hw: HardwareProfile) -> None:
    """Read ``lsblk`` in key=value pairs, which needs no JSON parser."""
    out = ""
    try:
        out = ctx.captured(
            ["lsblk", "-P", "-b", "-o", "NAME,SIZE,TYPE,MODEL,RM,TRAN,FSTYPE,MOUNTPOINT,LABEL"], quiet=True
        )
    except Exception:
        out = ""
    if not out:
        _disks_from_sysfs(hw)
        return
    by_name: dict[str, Disk] = {}
    for line in out.splitlines():
        fields = dict(re.findall(r'(\w+)="([^"]*)"', line))
        name = fields.get("NAME", "")
        if not name:
            continue
        kind = fields.get("TYPE", "")
        try:
            size = int(fields.get("SIZE", "0") or 0)
        except ValueError:
            size = 0
        if kind == "disk":
            disk = Disk(
                device="/dev/" + name,
                size=size,
                model=fields.get("MODEL", "").strip(),
                removable=fields.get("RM", "0") == "1",
                transport=fields.get("TRAN", ""),
            )
            by_name[name] = disk
            hw.disks.append(disk)
        elif kind == "part":
            parent = None
            for key in by_name:
                if re.match(r"^%sp?\d+$" % re.escape(key), name):
                    parent = by_name[key]
                    break
            if parent is None:
                continue
            number_match = re.search(r"(\d+)$", name)
            parent.partitions.append(
                Partition(
                    device="/dev/" + name,
                    number=int(number_match.group(1)) if number_match else 0,
                    size=size,
                    fstype=fields.get("FSTYPE", ""),
                    mountpoint=fields.get("MOUNTPOINT", ""),
                    label=fields.get("LABEL", ""),
                )
            )
    for disk in hw.disks:
        disk.partitions.sort(key=lambda p: p.number)


def _disks_from_sysfs(hw: HardwareProfile) -> None:
    """Fallback when lsblk is missing or empty (containers, odd VMs)."""
    base = "/sys/block"
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        if name.startswith(("loop", "ram", "zram", "sr", "dm-", "md")):
            continue
        path = os.path.join(base, name)
        try:
            sectors = int(_read(os.path.join(path, "size")).strip() or 0)
        except ValueError:
            sectors = 0
        if sectors <= 0:
            continue
        hw.disks.append(
            Disk(
                device="/dev/" + name,
                size=sectors * 512,
                model=_read(os.path.join(path, "device", "model")).strip(),
                removable=_read(os.path.join(path, "removable")).strip() == "1",
            )
        )


def _gpus(ctx: Context, hw: HardwareProfile) -> None:
    out = ""
    try:
        out = ctx.captured(["lspci", "-nn"], quiet=True)
    except Exception:
        out = ""
    for line in out.splitlines():
        if not re.search(r"(VGA compatible|3D controller|Display controller)", line):
            continue
        vendor = "Unknown"
        lowered = line.lower()
        if "nvidia" in lowered:
            vendor = "NVIDIA"
        elif "advanced micro devices" in lowered or "ati " in lowered or "amd/" in lowered:
            vendor = "AMD"
        elif "intel" in lowered:
            vendor = "Intel"
        device = line.split(":", 2)[-1].strip()[:80]
        if "[" in device and "]" in device:
            device = device.split("[", 1)[-1].rsplit("]", 1)[0]
        hw.gpus.append(Gpu(vendor=vendor, device=device))
    if not hw.gpus:
        # DRM driver names in sysfs are enough to say "some graphics present".
        try:
            for card in sorted(os.listdir("/sys/class/drm")):
                if re.match(r"^card\d+$", card):
                    hw.gpus.append(Gpu(vendor="Unknown", device=card, driver=_read("/sys/class/drm/%s/device/uevent" % card)[:40]))
        except OSError:
            pass


def _network(hw: HardwareProfile) -> None:
    base = "/sys/class/net"
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        if name == "lo":
            continue
        path = os.path.join(base, name)
        # Physical interfaces have a device link; virtual ones (docker0) do not.
        physical = os.path.exists(os.path.join(path, "device"))
        wireless = os.path.isdir(os.path.join(path, "wireless")) or os.path.isdir(os.path.join(path, "phy80211"))
        label = "%s (%s)" % (name, "Wi-Fi" if wireless else "Ethernet")
        hw.network.append(label if physical else label + " virtual")


def _audio(hw: HardwareProfile) -> None:
    base = "/sys/class/sound"
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        if name.startswith("card"):
            hw.audio.append(name)


def detect(ctx: Context) -> HardwareProfile:
    hw = HardwareProfile()
    _cpu(hw)
    _memory(hw)
    _firmware(hw)
    _disks(ctx, hw)
    _gpus(ctx, hw)
    _network(hw)
    _audio(hw)
    if not hw.ram_bytes:
        # A container without /proc/meminfo should still show something sane.
        hw.ram_bytes = 4 * 1024 ** 3
    return hw


def simulate() -> HardwareProfile:
    """A fixed laptop profile for the UI demo and the self-test."""
    hw = HardwareProfile(
        cpu_model="AMD Ryzen 7 7840U w/ Radeon 780M Graphics",
        cpu_vendor=AMD,
        cpu_cores=8,
        cpu_threads=16,
        ram_bytes=16 * 1024 ** 3,
        firmware="uefi",
        secure_boot=False,
        gpus=[Gpu(vendor="AMD", device="Radeon 780M", driver="amdgpu")],
        network=["wlan0 (Wi-Fi)", "enp1s0 (Ethernet)"],
        audio=["card0 (HDMI)", "card1 (Analog)"],
    )
    disk = Disk(
        device="/dev/nvme0n1",
        size=512 * 1024 ** 3,
        model="Samsung SSD 980 PRO 500GB",
        removable=False,
        transport="nvme",
        partitions=[
            Partition("/dev/nvme0n1p1", 1, 100 * 1024 ** 2, "vfat", "", "EFI"),
            Partition("/dev/nvme0n1p2", 2, 16 * 1024 ** 3, "", "", ""),
            Partition("/dev/nvme0n1p3", 3, 495 * 1024 ** 3, "ext4", "/", "Windows"),
        ],
    )
    hw.disks.append(disk)
    return hw
