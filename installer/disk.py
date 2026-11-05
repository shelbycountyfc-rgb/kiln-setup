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
from dataclasses import dataclass, field

from .context import Context, human_bytes, part_path

GIB = 1024 ** 3
MIB = 1024 ** 2
#: The guide asks for 23-32 GiB of root space; below this nothing is offered.
MIN_DISK_BYTES = 20 * GIB

FILESYSTEMS = ("ext4", "btrfs", "xfs")

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
                    mountpoint="/boot",
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
    keep_esp: str = "",
    alongside_number: int = 0,
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
        subvolumes=BTRFS_SUBVOLUMES if (root_fs == "btrfs" and mode == "erase") else (),
    )

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
            swap=swap,
            swap_gb=swap_gb,
            esp_gb=esp_gb,
            separate_home=separate_home,
            ram_bytes=ram_bytes,
            primary=index == 0,
            encryption=encryption,
            smallest=smallest,
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
def _sgdisk_spec(partition: PartitionPlan) -> str:
    """``+1234M`` for a partition of that size."""
    mib = max(1, int(partition.size // MIB))
    return "+%dM" % mib


def _mkfs(ctx: Context, fs: str, device: str, label: str) -> None:
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
    else:
        raise RuntimeError("unsupported filesystem %r" % fs)


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
            if partition.keep or partition.role == "bios" or not partition.fs:
                continue
            _mkfs(ctx, partition.fs, partition.device, partition.label)


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
    os.makedirs(ctx.target, exist_ok=True)
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
        os.makedirs(path, exist_ok=True)
        ctx.mount(device, path, options="subvol=%s,compress=zstd:3,noatime" % name)


def _apply_alongside(ctx: Context, plan: DiskPlan, passphrase: str) -> None:
    """Create one partition in the free space and leave everything else alone."""
    disk = plan.disk
    _unmount_disk(ctx, disk)
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
    _mkfs(ctx, plan.root_fs, device, "ARCH")
    _mount_root(ctx, plan, device)
    if not plan.keep_esp:
        raise RuntimeError(
            "no EFI system partition was found to keep; an installation alongside "
            "another system has to reuse its boot partition"
        )
    os.makedirs(ctx.target_path("boot"), exist_ok=True)
    ctx.mount(plan.keep_esp, ctx.target_path("boot"))


def apply_layout(ctx: Context, plan: DiskPlan, passphrase: str = "") -> None:
    """Create the plan on disk, format it and mount it under the target root."""
    if plan.mode == "alongside":
        _apply_alongside(ctx, plan, passphrase)
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

    _mkfs(ctx, plan.root_fs, device, "ARCH")
    _mount_root(ctx, plan, device)

    if plan.esp is not None:
        os.makedirs(ctx.target_path("boot"), exist_ok=True)
        ctx.mount(plan.esp.device, ctx.target_path("boot"))
    if plan.home is not None:
        os.makedirs(ctx.target_path("home"), exist_ok=True)
        ctx.mount(plan.home.device, ctx.target_path("home"))
    if plan.swap is not None:
        ctx.run(["swapon", plan.swap.device])


def shell_target(ctx: Context) -> str:
    return ctx.target


def wipe_signatures(ctx: Context, plan: DiskPlan) -> None:
    """Clear old signatures so a reused disk is not mistaken for the old one."""
    for member in plan.members:
        for partition in member.partitions:
            if partition.role == "bios" or partition.keep:
                continue
            ctx.run(["wipefs", "-a", partition.device], check=False, quiet=True)
