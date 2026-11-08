"""Partition planning, encryption, volume groups and formatting.

The plain layout is the example layout from the installation guide: one EFI
system partition, an optional swap partition, the root partition and an
optional separate ``/home``.  On top of that the planner can stack

    [mdadm array] -> [LUKS2] -> [LVM volume group] -> filesystem -> subvolumes

and can leave an existing system alone instead of erasing the disk.

Everything is planned as data first: the window shows the plan, the pipeline
executes it, and a dry run walks the same code path without touching a device.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .context import Context, human_bytes, part_path

GIB = 1024 ** 3
MIB = 1024 ** 2
#: The guide asks for 23-32 GiB of root space; below this nothing is offered.
MIN_DISK_BYTES = 20 * GIB

FILESYSTEMS = ("ext4", "btrfs", "xfs", "f2fs", "bcachefs")

#: Name -> mountpoint for the Btrfs layout this installer creates.
BTRFS_SUBVOLUMES = (
    ("@", "/"),
    ("@home", "/home"),
    ("@log", "/var/log"),
    ("@pkg", "/var/cache/pacman/pkg"),
    ("@snapshots", "/.snapshots"),
)

#: Root subvolume options: compressed, and no atime writes on every read.
BTRFS_ROOT_OPTIONS = "subvol=@,compress=zstd:3,noatime"

#: sgdisk partition type codes.
TYPE_EFI = "ef00"
TYPE_SWAP = "8200"
TYPE_ROOT = "8304"  # Linux x86-64 root
TYPE_LUKS = "8309"  # Linux LUKS
TYPE_HOME = "8302"  # Linux /home
TYPE_BIOS = "ef02"  # BIOS boot partition (GRUB on GPT)

#: RAID level -> smallest number of member disks that can hold it.
RAID_MINIMUM = {"1": 2, "5": 3, "10": 4}

#: Filesystems an existing partition can be shrunk from, and the tool family.
SHRINKABLE = {
    "ext2": "ext",
    "ext3": "ext",
    "ext4": "ext",
    "btrfs": "btrfs",
    "ntfs": "ntfs",
    "vfat": "fat",
    "fat16": "fat",
    "fat32": "fat",
    #: A swap area holds nothing to keep: it is switched off and made again.
    "swap": "swap",
}


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
    #: Mounted rather than created: an EFI system partition of an existing system.
    keep: bool = False

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
            "keep": self.keep,
        }


@dataclass
class MemberPlan:
    """One physical disk taking part in the installation."""

    disk: str
    size: int
    partitions: list = field(default_factory=list)

    def part(self, role: str):
        for partition in self.partitions:
            if partition.role == role:
                return partition
        return None

    def device(self, role: str) -> str:
        found = self.part(role)
        return found.device if found else ""


@dataclass
class DiskPlan:
    disk: str
    disk_size: int
    efi: bool
    root_fs: str
    mode: str = "erase"
    encryption: str = "none"
    volumes: str = "partitions"
    raid: str = "none"
    members: list = field(default_factory=list)
    partitions: list = field(default_factory=list)
    keep_esp: str = ""
    #: The kernel and the initramfs stay inside the container, on the root.
    encrypt_boot: bool = False
    shrink_partition: str = ""
    shrink_bytes: int = 0
    #: A swap file on the root, when no swap partition is wanted.
    swap_file_bytes: int = 0
    swap_file: str = ""
    #: Where the swap file starts, for the kernel's resume_offset.
    swap_offset: int = 0
    #: The UUID of the root file system, for resume= of a swap file.
    root_uuid: str = ""
    swap_uuid: str = ""
    luks_device: str = ""
    luks_uuid: str = ""
    crypt_name: str = "cryptroot"
    md_device: str = ""
    mapper: str = ""
    vg: str = "vg0"
    lv: str = "root"
    subvolumes: tuple = ()
    separate_home: bool = False

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

    @property
    def root_device(self) -> str:
        """The device the root filesystem lives on, after every layer."""
        if self.mapper:
            return self.mapper
        if self.md_device:
            return self.md_device
        return self.device("root")

    @property
    def array_members(self) -> list:
        """Every partition that takes part in the array."""
        found = []
        for member in self.members:
            partition = member.part("root")
            if partition is not None:
                found.append(partition.device)
        return found

    def stack(self) -> list:
        """The layers, in the order a read request passes through them."""
        layers = []
        if self.raid != "none":
            layers.append("RAID%s over %d disks" % (self.raid, len(self.members)))
        if self.encryption == "luks":
            layers.append("LUKS2 encryption")
        if self.volumes == "lvm":
            layers.append("LVM (%s/%s)" % (self.vg, self.lv))
        layers.append(self.root_fs)
        if self.subvolumes:
            layers.append("%d Btrfs subvolumes" % len(self.subvolumes))
        return layers

    # -- presentation ------------------------------------------------------
    def summary(self) -> list:
        rows = []
        for partition in self.partitions:
            role = "EFI" if partition.role == "efi" else (
                "BIOS" if partition.role == "bios" else partition.role
            )
            note = "[swap]" if partition.role == "swap" else partition.mountpoint
            if partition.keep:
                note = "%s (kept)" % (note or "existing")
            rows.append(
                (
                    partition.device.rsplit("/", 1)[-1],
                    role,
                    partition.fs,
                    human_bytes(partition.size),
                    note,
                )
            )
        return rows

    def to_dict(self) -> dict:
        return {
            "disk": self.disk,
            "diskSize": self.disk_size,
            "efi": self.efi,
            "rootFs": self.root_fs,
            "mode": self.mode,
            "encryption": self.encryption,
            "volumes": self.volumes,
            "raid": self.raid,
            "members": [m.disk for m in self.members],
            "luksDevice": self.luks_device,
            "mdDevice": self.md_device,
            "mapper": self.mapper,
            "subvolumes": [name for name, _mount in self.subvolumes],
            "partitions": [p.to_dict() for p in self.partitions],
        }


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def _plan_member(
    disk: str,
    size: int,
    *,
    efi: bool,
    root_fs: str,
    swap: bool,
    swap_gb: float | None,
    esp_gb: float,
    separate_home: bool,
    ram_bytes: int,
    primary: bool,
    encryption: str,
    smallest: int,
    encrypt_boot: bool = False,
    hibernate: bool = False,
) -> MemberPlan:
    """Partitions for one disk.  Every array member gets the same root size."""
    member = MemberPlan(disk=disk, size=size)
    number = 1
    used = MIB  # 1 MiB alignment, as sgdisk and the guide assume
    root_type = TYPE_LUKS if encryption == "luks" else TYPE_ROOT

    if efi:
        if primary:
            esp_bytes = int(max(0.1, min(4.0, esp_gb)) * GIB)
            member.partitions.append(
                PartitionPlan(
                    number=number,
                    role="efi",
                    size=esp_bytes,
                    fs="vfat",
                    mountpoint="/boot/efi" if encrypt_boot else "/boot",
                    label="EFI",
                    type_code=TYPE_EFI,
                    disk=disk,
                )
            )
            number += 1
            used += esp_bytes
    else:
        # GRUB on a GPT disk needs somewhere to keep its second stage, and on a
        # RAID machine it is installed to every member.
        member.partitions.append(
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

    if swap and primary:
        # Keep 24 GiB for the root filesystem no matter what was asked for.
        room = max(0, smallest - used - 24 * GIB)
        wanted = int(swap_gb * GIB) if swap_gb else recommended_swap(ram_bytes)
        if hibernate:
            # The image of memory has to fit, with room for the kernel beside it.
            wanted = max(wanted, ram_bytes + GIB)
        swap_bytes = max(0, min(wanted, room))
        if swap_bytes >= 1 * GIB:
            member.partitions.append(
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

    # The array is as large as its smallest member: identical partitions keep
    # mdadm from refusing the assembly.
    remaining = max(0, smallest - used - MIB)  # leave the backup GPT alone
    if separate_home and primary:
        root_bytes = int(remaining * 0.45)
        root_bytes -= root_bytes % MIB
        home_bytes = remaining - root_bytes
    else:
        root_bytes = remaining
        home_bytes = 0

    member.partitions.append(
        PartitionPlan(
            number=number,
            role="root",
            size=root_bytes,
            fs=root_fs,
            mountpoint="/",
            label="ARCH",
            type_code=root_type,
            disk=disk,
        )
    )
    number += 1

    if separate_home and home_bytes > 1 * GIB:
        member.partitions.append(
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
    return member


def _device_number(device: str) -> int:
    """/dev/sda3 -> 3, /dev/nvme0n1p2 -> 2; 0 when the path has no number."""
    match = re.search(r"(\d+)$", device or "")
    return int(match.group(1)) if match else 0


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
    mode: str = "erase",
    encryption: str = "none",
    volumes: str = "partitions",
    raid: str = "none",
    raid_members=(),
    swap_file: bool = False,
    keep_esp: str = "",
    alongside_number: int = 0,
    encrypt_boot: bool = False,
    hibernate: bool = False,
    shrink_partition: str = "",
    shrink_gb: float = 0.0,
    reuse_root: str = "",
    reuse_home: str = "",
) -> DiskPlan:
    """Work out the whole storage plan before a single sector is written."""
    plan = DiskPlan(
        disk=disk,
        disk_size=disk_size,
        efi=efi,
        root_fs=root_fs,
        mode=mode,
        encryption=encryption,
        volumes=volumes,
        raid=raid if mode == "erase" else "none",
        keep_esp=keep_esp,
        separate_home=separate_home and mode == "erase",
        encrypt_boot=bool(encrypt_boot) and mode == "erase",
        shrink_partition=shrink_partition if mode == "alongside" else "",
        shrink_bytes=int(max(0.0, shrink_gb) * GIB) if mode == "alongside" else 0,
        subvolumes=(
            BTRFS_SUBVOLUMES
            if (root_fs == "btrfs" and mode in ("erase", "reuse"))
            else ()
        ),
    )

    if swap and swap_file:
        # The file is made once the root is mounted; its size is worked out here
        # so the preview and the plan agree.
        wanted = int(swap_gb * GIB) if swap_gb else recommended_swap(ram_bytes)
        if hibernate:
            wanted = max(wanted, ram_bytes + GIB)
        plan.swap_file_bytes = max(0, wanted - (wanted % MIB))

    if mode == "alongside":
        number = alongside_number or 1
        type_code = TYPE_LUKS if encryption == "luks" else TYPE_ROOT
        plan.members = [MemberPlan(disk=disk, size=disk_size)]
        plan.partitions = [
            PartitionPlan(
                number=number,
                role="root",
                size=0,  # "the rest of the free space", resolved by sgdisk
                fs=root_fs,
                mountpoint="/",
                label="ARCH",
                type_code=type_code,
                disk=disk,
            )
        ]
        if keep_esp:
            plan.partitions.insert(
                0,
                PartitionPlan(
                    number=0,
                    role="efi",
                    size=0,
                    fs="vfat",
                    mountpoint="/boot",
                    label="EFI",
                    disk=disk,
                    keep=True,
                ),
            )
        plan.members[0].partitions = list(plan.partitions)
        return plan

    if mode == "reuse":
        # Nothing is created and nothing is deleted: one existing partition is
        # formatted as the new root, and another can be mounted as /home as it is.
        plan.members = [MemberPlan(disk=disk, size=disk_size)]
        plan.partitions = []
        if keep_esp:
            plan.partitions.append(
                PartitionPlan(
                    number=0,
                    role="efi",
                    size=0,
                    fs="vfat",
                    mountpoint="/boot",
                    label="EFI",
                    disk=disk,
                    keep=True,
                )
            )
        if reuse_root:
            plan.partitions.append(
                PartitionPlan(
                    number=_device_number(reuse_root),
                    role="root",
                    size=0,
                    fs=root_fs,
                    mountpoint="/",
                    label="ARCH",
                    type_code=TYPE_LUKS if encryption == "luks" else TYPE_ROOT,
                    disk=disk,
                )
            )
        if reuse_home:
            plan.partitions.append(
                PartitionPlan(
                    number=_device_number(reuse_home),
                    role="home",
                    size=0,
                    fs="",
                    mountpoint="/home",
                    label="HOME",
                    type_code=TYPE_HOME,
                    disk=disk,
                    keep=True,
                )
            )
        plan.members[0].partitions = list(plan.partitions)
        return plan

    members = [(disk, disk_size)]
    for entry in raid_members or ():
        device, size = entry
        if device and device != disk:
            members.append((device, int(size)))
    if raid == "none":
        members = members[:1]
    smallest = min(size for _device, size in members)

    plan.members = [
        _plan_member(
            device,
            size,
            efi=efi,
            root_fs=root_fs,
            swap=swap and not swap_file,
            swap_gb=swap_gb,
            esp_gb=esp_gb,
            separate_home=separate_home,
            ram_bytes=ram_bytes,
            primary=index == 0,
            encryption=encryption,
            smallest=smallest,
            encrypt_boot=encrypt_boot,
            hibernate=hibernate,
        )
        for index, (device, size) in enumerate(members)
    ]
    plan.partitions = list(plan.members[0].partitions)
    return plan


def find_disk(hardware, device: str):
    """The scanned disk with this device path, or ``None``."""
    for candidate in getattr(hardware, "disks", []) or []:
        if candidate.device == device:
            return candidate
    return None


def find_esp(hardware, disk: str = "") -> str:
    """An EFI system partition of an existing system, on any disk.

    Windows keeps its boot files on a 100-300 MiB FAT partition; a Linux system
    often has it mounted at /boot or /boot/efi.  Reusing it is what makes an
    installation alongside possible: formatting it would take the other system's
    boot loader with it.
    """
    best = ""
    for candidate in getattr(hardware, "disks", []) or []:
        for partition in getattr(candidate, "partitions", []) or []:
            fstype = (partition.fstype or "").lower()
            if fstype not in ("vfat", "fat32", "fat16", "efi"):
                continue
            mountpoint = partition.mountpoint or ""
            score = 0
            if mountpoint.startswith("/boot"):
                score += 2
            if (partition.label or "").upper() in ("EFI", "SYSTEM", "ESP", "BOOT"):
                score += 1
            if score == 0 and not (100 * MIB <= partition.size <= 4 * GIB):
                continue
            if score and not best:
                best = partition.device
            elif score and best:
                # Prefer the partition on the chosen disk, then a mount of ours.
                if disk and candidate.device == disk:
                    best = partition.device
    return best


def next_partition_number(hardware, disk: str) -> int:
    """The number the new partition of an alongside install will get."""
    found = find_disk(hardware, disk)
    numbers = [p.number for p in getattr(found, "partitions", []) or [] if p.number]
    return (max(numbers) if numbers else 0) + 1


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Shrinking an existing partition
# --------------------------------------------------------------------------- #
def shrink_candidates(hardware, disk: str) -> list:
    """Partitions of this disk whose filesystem can be made smaller."""
    found = find_disk(hardware, disk)
    choices = []
    for partition in getattr(found, "partitions", []) or []:
        if SHRINKABLE.get((partition.fstype or "").lower()) and partition.size > 8 * GIB:
            choices.append(partition)
    return choices


def _partition_number(device: str) -> int:
    match = re.search(r"(\d+)$", device)
    return int(match.group(1)) if match else 0


def _resize_partition_table(
    ctx: Context, disk: str, number: int, bytes_wanted: int
) -> None:
    """Move the end of one partition, keeping its number, type and UUID.

    ``sfdisk --dump`` describes every partition in a form it reads back, so
    rewriting one ``size=`` field changes nothing else: not the start sector,
    not the type code, not the UUID the other system's boot loader looks for.
    parted is not used because it stops to ask whether shrinking is intended,
    which fails with no terminal attached.
    """
    table = ctx.captured(["sfdisk", "--dump", disk], quiet=True)
    if not table.strip():
        if ctx.dry_run:
            return
        raise RuntimeError("cannot read the partition table of %s" % disk)
    sectors = bytes_wanted // 512
    lines = []
    changed = False
    for line in table.splitlines():
        stripped = line.strip()
        if ":" in stripped and stripped.split(":", 1)[0].strip().startswith(disk):
            device, _sep, fields = stripped.partition(":")
            if _partition_number(device.strip()) == number:
                updated = []
                for field in (item.strip() for item in fields.split(",")):
                    if field.startswith("size="):
                        updated.append("size=%d" % sectors)
                        changed = True
                    else:
                        updated.append(field)
                line = "%s : %s" % (device.strip(), ", ".join(updated))
        lines.append(line)
    if not changed:
        raise RuntimeError(
            "partition %d is not in the table of %s" % (number, disk)
        )
    ctx.run(
        ["sfdisk", "--no-reread", disk],
        input_text="\n".join(lines) + "\n",
    )
    ctx.run(["partprobe", disk], check=False)
    ctx.run(["udevadm", "settle"], check=False)


def _shrink_filesystem(
    ctx: Context, disk: str, number: int, kind: str, device: str, bytes_wanted: int
) -> None:
    """Make the filesystem smaller before the partition table is changed.

    ``swap`` holds nothing worth keeping: it is switched off here and made again
    once the table has moved.  ``fat`` has no command-line resizer, so parted
    shrinks the file system and moves the table entry in the same step.
    """
    if kind == "ext":
        ctx.run(["e2fsck", "-f", "-y", device], check=False)
        ctx.run(["resize2fs", device, "%dK" % max(1, bytes_wanted // 1024)])
    elif kind == "ntfs":
        ctx.run(["ntfsresize", "--force", "--size", str(bytes_wanted), device])
    elif kind == "swap":
        ctx.run(["swapoff", device], check=False)
    elif kind == "fat":
        start = _partition_start_bytes(ctx, disk, number)
        if start <= 0:
            raise RuntimeError("cannot tell where %s begins on %s" % (device, disk))
        ctx.run(["fsck.fat", "-a", device], check=False)
        end = max(start + MIB, start + bytes_wanted)
        ctx.run(
            [
                "parted", "-s", disk, "unit", "B", "resize", str(number),
                str(start), str(end),
            ]
        )
    elif kind == "btrfs":
        mountpoint = ctx.target_path(".shrink")
        ctx.make_dir(mountpoint)
        ctx.mount(device, mountpoint)
        try:
            ctx.run(["btrfs", "filesystem", "resize", str(bytes_wanted), mountpoint])
        finally:
            ctx.run(["umount", mountpoint], check=False)
    else:
        raise RuntimeError("this installer cannot shrink %s" % kind)


def shrink_partition(ctx: Context, disk: str, device: str, bytes_wanted: int) -> None:
    """Shrink a partition that holds another system's filesystem.

    The filesystem goes first: shrinking the partition under a filesystem that
    still fills it cuts the data off the end.  The table entry is then rewritten
    at the same start sector, which keeps its number, type and PARTUUID, so the
    other system's boot loader still finds it where it was.
    """
    if bytes_wanted < 4 * GIB:
        raise RuntimeError("a partition cannot be shrunk below 4 GiB")
    fstype = ctx.captured(
        ["blkid", "-s", "TYPE", "-o", "value", device], quiet=True
    ).strip()
    kind = SHRINKABLE.get(fstype.lower())
    if kind is None:
        raise RuntimeError(
            "%s holds %s, which cannot be shrunk; pick another partition"
            % (device, fstype or "an unknown filesystem")
        )
    number = _partition_number(device)
    identity = ""
    label = ""
    if kind == "swap":
        # The other system's fstab names this swap by UUID: put it back.
        identity = ctx.captured(
            ["blkid", "-s", "UUID", "-o", "value", device], quiet=True
        ).strip()
        label = ctx.captured(
            ["blkid", "-s", "LABEL", "-o", "value", device], quiet=True
        ).strip()
    if not number:
        raise RuntimeError("cannot tell which partition %s is" % device)
    ctx.log.log(
        "shrinking %s to %d GiB to make room" % (device, bytes_wanted // GIB), "step"
    )
    # One MiB of slack: the filesystem must never be asked to fill the last block
    # of the partition it lives in.
    _shrink_filesystem(ctx, disk, number, kind, device, bytes_wanted - MIB)
    if kind == "fat":
        # parted moved the table entry itself; there is nothing left to rewrite.
        ctx.run(["partprobe", disk], check=False)
        ctx.run(["udevadm", "settle"], check=False)
        ctx.run(["fsck.fat", "-n", device], check=False)
        return
    _resize_partition_table(ctx, disk, number, bytes_wanted)
    if kind == "swap":
        # Nothing was stored there, so it is made again at the new size with the
        # UUID the other system's fstab refers to.
        args = ["mkswap"]
        if identity:
            args += ["--uuid", identity]
        if label:
            args += ["--label", label]
        ctx.run(args + [device])


def _partition_start_bytes(ctx: Context, disk: str, number: int) -> int:
    """Where a partition begins, in bytes, as the table describes it."""
    table = ctx.captured(["sfdisk", "--dump", disk], quiet=True)
    for line in (table or "").splitlines():
        stripped = line.strip()
        if ":" not in stripped or not stripped.startswith(disk):
            continue
        device, _sep, fields = stripped.partition(":")
        if _partition_number(device.strip()) != number:
            continue
        values = {}
        for field in fields.split(","):
            key, _eq, value = field.strip().partition("=")
            values[key] = value.strip()
        try:
            return int(values.get("start", "0")) * 512
        except ValueError:
            return 0
    return 0


def _resume_offset(ctx: Context, path: str, root_fs: str) -> int:
    """Where the swap file starts on its device, in 4096-byte pages."""
    if ctx.dry_run:
        return 0
    if root_fs == "btrfs":
        text = ctx.captured(
            ["btrfs", "inspect-internal", "map-swapfile", "-r", path], quiet=True
        )
        last = text.strip().splitlines()[-1] if text.strip() else ""
        tokens = re.findall(r"\d+", last)
        return int(tokens[-1]) if tokens else 0
    text = ctx.captured(["filefrag", "-v", path], quiet=True)
    block = 4096
    found = re.search(r"blocks of (\d+) bytes", text)
    if found:
        block = int(found.group(1))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("0:"):
            continue
        fields = stripped.split(":")
        numbers = re.findall(r"\d+", fields[2]) if len(fields) > 2 else []
        if numbers:
            return int(numbers[0]) * block // 4096
    return 0


def _create_swap_file(ctx: Context, plan: DiskPlan) -> None:
    """Put swap on a file in the root file system, hibernation included."""
    if not plan.swap_file_bytes:
        return
    path = ctx.target_path("swap/swapfile")
    ctx.log.log(
        "creating a swap file of %d GiB at /swap/swapfile"
        % (plan.swap_file_bytes // GIB),
        "step",
    )
    ctx.make_dir(os.path.dirname(path))
    if plan.root_fs == "btrfs":
        # mkswapfile creates it without COW and with the mode swapon wants.
        ctx.run(
            [
                "btrfs", "filesystem", "mkswapfile",
                "--size", str(plan.swap_file_bytes), path,
            ]
        )
    else:
        ctx.run(["fallocate", "-l", str(plan.swap_file_bytes), path])
        ctx.run(["chmod", "600", path])
        ctx.run(["mkswap", "-L", "SWAP", path])
    plan.swap_file = "/swap/swapfile"
    plan.swap_offset = _resume_offset(ctx, path, plan.root_fs)


def _sgdisk_spec(partition: PartitionPlan) -> str:
    """``+1234M`` for a partition of that size."""
    mib = max(1, int(partition.size // MIB))
    return "+%dM" % mib


def _mkfs(ctx: Context, fs: str, device: str, label: str, role: str = "partition") -> None:
    if fs == "vfat":
        ctx.run(["mkfs.fat", "-F", "32", "-n", label or "EFI", device])
    elif fs == "swap":
        ctx.run(["mkswap", "-L", label or "SWAP", device])
    elif fs == "ext4":
        ctx.run(["mkfs.ext4", "-F", "-L", label or "ARCH", device])
    elif fs == "xfs":
        ctx.run(["mkfs.xfs", "-f", "-L", label or "ARCH", device])
    elif fs == "btrfs":
        ctx.run(["mkfs.btrfs", "-f", "-L", label or "ARCH", device])
    elif fs == "f2fs":
        # Wear-leveled and fast on the flash of an eMMC, an SD card or a stick.
        ctx.run(["mkfs.f2fs", "-f", "-l", label or "ARCH", device])
    elif fs == "bcachefs":
        ctx.run(["bcachefs", "format", "--force", "--label", label or "ARCH", device])
    else:
        raise RuntimeError(
            "cannot format %s (%s): unsupported filesystem %r"
            % (device, role or "partition", fs)
        )


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


def _create_partitions(ctx: Context, plan: DiskPlan) -> None:
    for member in plan.members:
        _unmount_disk(ctx, member.disk)
        ctx.run(["sgdisk", "--zap-all", member.disk])
        ctx.run(["partprobe", member.disk], check=False)
        ctx.run(["udevadm", "settle"], check=False)
        for partition in member.partitions:
            if partition.keep:
                continue
            ctx.run(
                [
                    "sgdisk",
                    "-n",
                    "%d:0:%s" % (partition.number, _sgdisk_spec(partition)),
                    "-t",
                    "%d:%s" % (partition.number, partition.type_code),
                    "-c",
                    "%d:%s" % (partition.number, partition.label or partition.role),
                    member.disk,
                ]
            )
        ctx.run(["partprobe", member.disk], check=False)
    ctx.run(["udevadm", "settle"], check=False)

    # Every partition of the plan gets its filesystem here except the two the
    # layout formats later: the root (the array, container or volume may not
    # exist yet) and the BIOS boot partition (raw, no filesystem at all).  The
    # ESP, swap and a separate /home are formatted only in this loop.
    for member in plan.members:
        for partition in member.partitions:
            if partition.keep or partition.role in ("bios", "root") or not partition.fs:
                continue
            _mkfs(ctx, partition.fs, partition.device, partition.label, partition.role)


def _create_array(ctx: Context, plan: DiskPlan) -> None:
    """Build the mdadm array over the root partitions of every member."""
    members = plan.array_members
    minimum = RAID_MINIMUM.get(plan.raid, 0)
    if minimum and len(members) < minimum:
        raise RuntimeError(
            "RAID%s needs at least %d disks, but %d were selected"
            % (plan.raid, minimum, len(members))
        )
    plan.md_device = "/dev/md0"
    ctx.log.log("creating %s over %s" % (plan.md_device, ", ".join(members)), "step")
    ctx.run(
        ["mdadm", "--create", plan.md_device, "--level=%s" % plan.raid,
         "--raid-devices=%d" % len(members), "--metadata=1.2",
         "--bitmap=internal", "--homehost=any"]
        + members
    )
    ctx.run(["udevadm", "settle"], check=False)


def _open_luks(ctx: Context, plan: DiskPlan, device: str, passphrase: str) -> str:
    """Format the container, open it, and remember the UUID for the command line."""
    if plan.encryption != "luks":
        return device
    if not passphrase:
        raise RuntimeError("an encrypted installation needs a passphrase")
    plan.luks_device = device
    ctx.log.log("formatting %s as a LUKS2 container" % device, "step")
    ctx.run(
        ["cryptsetup", "luksFormat", "--batch-mode", "--type", "luks2",
         "--pbkdf", "argon2id", "--key-file", "-", device],
        input_text=passphrase,
    )
    ctx.run(
        ["cryptsetup", "open", "--key-file", "-", device, plan.crypt_name],
        input_text=passphrase,
    )
    plan.luks_uuid = ctx.captured(
        ["blkid", "-s", "UUID", "-o", "value", device], quiet=True
    ).strip()
    return "/dev/mapper/%s" % plan.crypt_name


def _create_lvm(ctx: Context, plan: DiskPlan, device: str) -> str:
    if plan.volumes != "lvm":
        return device
    ctx.log.log("creating the volume group %s on %s" % (plan.vg, device), "step")
    ctx.run(["pvcreate", "--force", "--yes", device])
    ctx.run(["vgcreate", plan.vg, device])
    ctx.run(["lvcreate", "--yes", "--extents", "100%FREE", "--name", plan.lv, plan.vg])
    return "/dev/%s/%s" % (plan.vg, plan.lv)


def _mount_root(ctx: Context, plan: DiskPlan, device: str) -> None:
    ctx.make_dir(ctx.target)
    if not plan.subvolumes:
        ctx.mount(device, ctx.target)
        return
    # Btrfs: create the subvolumes once, then mount the root subvolume.
    ctx.mount(device, ctx.target, options="compress=zstd:3,noatime")
    for name, _mountpoint in plan.subvolumes:
        ctx.run(["btrfs", "subvolume", "create", os.path.join(ctx.target, name)])
    ctx.run(["umount", ctx.target], check=False)
    ctx.mount(device, ctx.target, options=BTRFS_ROOT_OPTIONS)
    for name, mountpoint in plan.subvolumes:
        if mountpoint == "/":
            continue
        path = ctx.target_path(mountpoint)
        ctx.make_dir(path)
        ctx.mount(device, path, options="subvol=%s,compress=zstd:3,noatime" % name)


def _apply_alongside(ctx: Context, plan: DiskPlan, passphrase: str) -> None:
    """Create one partition in the free space and leave everything else alone."""
    disk = plan.disk
    _unmount_disk(ctx, disk)
    if plan.shrink_partition and plan.shrink_bytes:
        shrink_partition(ctx, disk, plan.shrink_partition, plan.shrink_bytes)
    root = plan.root
    if root is None:
        raise RuntimeError("the alongside layout has no root partition")
    ctx.log.log(
        "creating partition %d in the free space of %s" % (root.number, disk), "step"
    )
    ctx.run(
        ["sgdisk", "-n", "%d:0:0" % root.number, "-t",
         "%d:%s" % (root.number, root.type_code), "-c",
         "%d:%s" % (root.number, root.label or "ARCH"), disk]
    )
    ctx.run(["partprobe", disk], check=False)
    ctx.run(["udevadm", "settle"], check=False)
    device = _open_luks(ctx, plan, root.device, passphrase)
    device = _create_lvm(ctx, plan, device)
    plan.mapper = device
    _mkfs(ctx, plan.root_fs, device, "ARCH", "root")
    _mount_root(ctx, plan, device)
    _create_swap_file(ctx, plan)
    plan.root_uuid = ctx.captured(
        ["blkid", "-s", "UUID", "-o", "value", device], quiet=True
    ).strip()
    if not plan.keep_esp:
        raise RuntimeError(
            "no EFI system partition was found to keep; an installation alongside "
            "another system has to reuse its boot partition"
        )
    ctx.make_dir(ctx.target_path("boot"))
    ctx.mount(plan.keep_esp, ctx.target_path("boot"))


def _apply_reuse(ctx: Context, plan: DiskPlan, passphrase: str) -> None:
    """Format one existing partition and mount everything else as it is."""
    root = plan.root
    if root is None:
        raise RuntimeError("the reinstall layout has no root partition")
    device = root.device
    if not ctx.dry_run and not os.path.exists(device):
        raise RuntimeError(
            "%s does not exist, so there is nothing to reinstall into" % device
        )
    ctx.log.log(
        "formatting %s as the new root, and no other partition" % device, "step"
    )
    _unmount_disk(ctx, plan.disk)
    ctx.run(["swapoff", device], check=False, quiet=True)
    ctx.run(["wipefs", "-a", device], check=False, quiet=True)
    device = _open_luks(ctx, plan, device, passphrase)
    device = _create_lvm(ctx, plan, device)
    plan.mapper = device
    _mkfs(ctx, plan.root_fs, device, "ARCH", "root")
    _mount_root(ctx, plan, device)
    _create_swap_file(ctx, plan)
    plan.root_uuid = ctx.captured(
        ["blkid", "-s", "UUID", "-o", "value", device], quiet=True
    ).strip()
    if plan.keep_esp:
        ctx.make_dir(ctx.target_path("boot"))
        ctx.mount(plan.keep_esp, ctx.target_path("boot"))
    if plan.home is not None:
        # Kept, not formatted: the files on it are the point of a reinstall.
        ctx.make_dir(ctx.target_path("home"))
        ctx.mount(plan.home.device, ctx.target_path("home"))


def apply_layout(ctx: Context, plan: DiskPlan, passphrase: str = "") -> None:
    """Create the plan on disk, format it and mount it under the target root."""
    if plan.mode == "alongside":
        _apply_alongside(ctx, plan, passphrase)
        return
    if plan.mode == "reuse":
        _apply_reuse(ctx, plan, passphrase)
        return

    ctx.log.log("creating partitions on %s" % ", ".join(m.disk for m in plan.members), "step")
    _create_partitions(ctx, plan)
    if plan.raid != "none":
        _create_array(ctx, plan)

    device = plan.md_device or plan.device("root")
    device = _open_luks(ctx, plan, device, passphrase)
    device = _create_lvm(ctx, plan, device)
    plan.mapper = device

    if plan.md_device:
        # The array has to be assembled by the initramfs, and mdadm.conf is how
        # it knows which devices belong together.
        scan = ctx.captured(["mdadm", "--detail", "--scan"], quiet=True)
        if scan.strip():
            ctx.write_target("etc/mdadm.conf", scan.rstrip() + "\n")

    _mkfs(ctx, plan.root_fs, device, "ARCH", "root")
    _mount_root(ctx, plan, device)

    _create_swap_file(ctx, plan)
    plan.root_uuid = ctx.captured(
        ["blkid", "-s", "UUID", "-o", "value", device], quiet=True
    ).strip()
    if plan.esp is not None:
        boot = "boot/efi" if plan.encrypt_boot else "boot"
        ctx.make_dir(ctx.target_path(boot))
        ctx.mount(plan.esp.device, ctx.target_path(boot))
    if plan.home is not None:
        ctx.make_dir(ctx.target_path("home"))
        ctx.mount(plan.home.device, ctx.target_path("home"))
    if plan.swap is not None:
        ctx.run(["swapon", plan.swap.device])
        plan.swap_uuid = ctx.captured(
            ["blkid", "-s", "UUID", "-o", "value", plan.swap.device], quiet=True
        ).strip()


def shell_target(ctx: Context) -> str:
    return ctx.target


def wipe_signatures(ctx: Context, plan: DiskPlan) -> None:
    """Clear old signatures so a reused disk is not mistaken for the old one."""
    for member in plan.members:
        for partition in member.partitions:
            if partition.role == "bios" or partition.keep:
                continue
            ctx.run(["wipefs", "-a", partition.device], check=False, quiet=True)
