"""Partition planning and formatting.

The layout is the example layout from the installation guide: one EFI system
partition (UEFI only), an optional swap partition, the root partition and an
optional separate ``/home``.  GPT is used in both firmware modes, with a BIOS
boot partition for GRUB on legacy machines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .context import Context, human_bytes, part_path

GIB = 1024 ** 3
MIB = 1024 ** 2
#: The guide asks for 23-32 GiB of root space; below this nothing is offered.
MIN_DISK_BYTES = 20 * GIB

FILESYSTEMS = ("ext4", "btrfs", "xfs")

#: sgdisk partition type codes.
TYPE_EFI = "ef00"
TYPE_SWAP = "8200"
TYPE_ROOT = "8304"  # Linux x86-64 root
TYPE_HOME = "8302"  # Linux /home
TYPE_BIOS = "ef02"  # BIOS boot partition (GRUB on GPT)


def recommended_swap(ram_bytes: int) -> int:
    """Swap worth creating for this much memory.

    Enough to hibernate a small machine, capped on large ones where a swap file
    is the better answer.  Returns bytes.
    """
    if ram_bytes <= 8 * GIB:
        return max(2 * GIB, ram_bytes)
    if ram_bytes <= 32 * GIB:
        return 8 * GIB
    return 8 * GIB


@dataclass
class PartitionPlan:
    number: int
    role: str  # "efi" | "swap" | "root" | "home" | "bios"
    size: int
    fs: str
    disk: str = ""
    mountpoint: str = ""
    label: str = ""
    type_code: str = TYPE_ROOT

    @property
    def device(self) -> str:
        return part_path(self.disk, self.number)

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "role": self.role,
            "device": self.device,
            "size": self.size,
            "sizeText": human_bytes(self.size),
            "fs": self.fs,
            "mountpoint": self.mountpoint,
            "label": self.label,
        }


@dataclass
class DiskPlan:
    disk: str
    disk_size: int
    efi: bool
    root_fs: str
    partitions: list = field(default_factory=list)

    # -- lookups -----------------------------------------------------------
    def part(self, role: str):
        for partition in self.partitions:
            if partition.role == role:
                return partition
        return None

    @property
    def root(self):
        return self.part("root")

    @property
    def esp(self):
        return self.part("efi")

    @property
    def swap(self):
        return self.part("swap")

    @property
    def home(self):
        return self.part("home")

    def device(self, role: str) -> str:
        found = self.part(role)
        return found.device if found else ""

    # -- presentation ------------------------------------------------------
    def summary(self) -> list:
        rows = []
        for partition in self.partitions:
            rows.append(
                (
                    partition.device.rsplit("/", 1)[-1],
                    partition.role.upper() if partition.role in ("efi", "bios") else partition.role,
                    partition.fs,
                    human_bytes(partition.size),
                    "[swap]" if partition.role == "swap" else partition.mountpoint,
                )
            )
        return rows

    def to_dict(self) -> dict:
        return {
            "disk": self.disk,
            "diskSize": self.disk_size,
            "efi": self.efi,
            "rootFs": self.root_fs,
            "partitions": [p.to_dict() for p in self.partitions],
        }


def plan_layout(
    disk: str,
    disk_size: int,
    *,
    efi: bool = True,
    root_fs: str = "ext4",
    swap: bool = True,
    swap_gb: float | None = None,
    esp_gb: float = 1.0,
    separate_home: bool = False,
    ram_bytes: int = 8 * GIB,
) -> DiskPlan:
    """Work out the partition table before a single sector is written."""
    plan = DiskPlan(disk=disk, disk_size=disk_size, efi=efi, root_fs=root_fs)
    number = 1
    used = MIB  # 1 MiB alignment, as sgdisk and the guide assume

    if efi:
        esp_bytes = int(max(0.1, min(4.0, esp_gb)) * GIB)
        plan.partitions.append(
            PartitionPlan(
                number=number,
                role="efi",
                size=esp_bytes,
                fs="vfat",
                mountpoint="/boot",
                label="EFI",
                type_code=TYPE_EFI,
                disk=disk,
            )
        )
        number += 1
        used += esp_bytes
    else:
        # GRUB on a GPT disk needs somewhere to keep its second stage.
        plan.partitions.append(
            PartitionPlan(
                number=number,
                role="bios",
                size=2 * MIB,
                fs="",
                label="BIOS",
                type_code=TYPE_BIOS,
                disk=disk,
            )
        )
        number += 1
        used += 2 * MIB

    swap_bytes = 0
    if swap:
        # Keep 24 GiB for the root filesystem no matter what was asked for.
        room = max(0, disk_size - used - 24 * GIB)
        wanted = int(swap_gb * GIB) if swap_gb else recommended_swap(ram_bytes)
        swap_bytes = max(0, min(wanted, room))
        if swap_bytes >= 1 * GIB:
            plan.partitions.append(
                PartitionPlan(
                    number=number,
                    role="swap",
                    size=swap_bytes - (swap_bytes % MIB),
                    fs="swap",
                    label="SWAP",
                    type_code=TYPE_SWAP,
                    disk=disk,
                )
            )
            number += 1
            used += swap_bytes

    remaining = max(0, disk_size - used - MIB)  # leave the backup GPT alone
    if separate_home:
        root_bytes = int(remaining * 0.45)
        root_bytes -= root_bytes % MIB
        home_bytes = remaining - root_bytes
    else:
        root_bytes = remaining
        home_bytes = 0

    plan.partitions.append(
        PartitionPlan(
            number=number,
            role="root",
            size=root_bytes,
            fs=root_fs,
            mountpoint="/",
            label="ARCH",
            type_code=TYPE_ROOT,
            disk=disk,
        )
    )
    number += 1

    if separate_home and home_bytes > 1 * GIB:
        plan.partitions.append(
            PartitionPlan(
                number=number,
                role="home",
                size=home_bytes,
                fs=root_fs,
                mountpoint="/home",
                label="HOME",
                type_code=TYPE_HOME,
                disk=disk,
            )
        )
    return plan


def find_disk(hardware, device: str):
    """The scanned disk with this device path, or ``None``."""
    for candidate in getattr(hardware, "disks", []) or []:
        if candidate.device == device:
            return candidate
    return None


def _sgdisk_spec(partition: PartitionPlan) -> str:
    """``+1234M`` for a partition of that size."""
    mib = max(1, int(partition.size // MIB))
    return "+%dM" % mib


def _mkfs(ctx: Context, partition: PartitionPlan) -> None:
    device = partition.device
    if partition.role == "efi":
        ctx.run(["mkfs.fat", "-F", "32", "-n", partition.label or "EFI", device])
    elif partition.role == "swap":
        ctx.run(["mkswap", "-L", partition.label or "SWAP", device])
    elif partition.fs == "ext4":
        ctx.run(["mkfs.ext4", "-F", "-L", partition.label or "ARCH", device])
    elif partition.fs == "xfs":
        ctx.run(["mkfs.xfs", "-f", "-L", partition.label or "ARCH", device])
    elif partition.fs == "btrfs":
        ctx.run(["mkfs.btrfs", "-f", "-L", partition.label or "ARCH", device])
    else:
        raise RuntimeError("unsupported filesystem %r" % partition.fs)


def _unmount_disk(ctx: Context, disk: str) -> None:
    """Unmount anything on this disk, including a swap partition in use."""
    ctx.run(["swapoff", "-a"], check=False, quiet=True)
    out = ""
    try:
        out = ctx.captured(["lsblk", "-ln", "-o", "NAME,MOUNTPOINT", disk], quiet=True)
    except Exception:
        out = ""
    for line in reversed(out.splitlines()):
        parts = line.split()
        if len(parts) < 2:
            continue
        mountpoint = parts[-1]
        if mountpoint.startswith("/") and os.path.ismount(mountpoint):
            ctx.run(["umount", "-R", mountpoint], check=False)


def apply_layout(ctx: Context, plan: DiskPlan) -> None:
    """Erase the disk, create the planned partitions, format and mount them."""
    disk = plan.disk
    _unmount_disk(ctx, disk)
    ctx.run(["sgdisk", "--zap-all", disk])
    ctx.run(["partprobe", disk], check=False)
    ctx.run(["udevadm", "settle"], check=False)

    for partition in plan.partitions:
        ctx.run(
            [
                "sgdisk",
                "-n",
                "%d:0:%s" % (partition.number, _sgdisk_spec(partition)),
                "-t",
                "%d:%s" % (partition.number, partition.type_code),
                "-c",
                "%d:%s" % (partition.number, partition.label or partition.role),
                disk,
            ]
        )
    ctx.run(["partprobe", disk], check=False)
    ctx.run(["udevadm", "settle"], check=False)

    for partition in plan.partitions:
        if partition.role == "bios":
            continue
        _mkfs(ctx, partition)

    os.makedirs(shell_target(ctx), exist_ok=True)
    ctx.mount(plan.device("root"), "/mnt")
    if plan.esp is not None:
        os.makedirs("/mnt/boot", exist_ok=True)
        ctx.mount(plan.esp.device, "/mnt/boot")
    if plan.home is not None:
        os.makedirs("/mnt/home", exist_ok=True)
        ctx.mount(plan.home.device, "/mnt/home")
    if plan.swap is not None:
        ctx.run(["swapon", plan.swap.device])


def shell_target(ctx: Context) -> str:
    return ctx.target


def wipe_signatures(ctx: Context, plan: DiskPlan) -> None:
    """Clear old signatures so a reused disk is not mistaken for the old one."""
    for partition in plan.partitions:
        if partition.role == "bios":
            continue
        ctx.run(["wipefs", "-a", partition.device], check=False, quiet=True)
