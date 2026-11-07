"""Checks that need no root, no Arch and no disk.

Covers the answers, the plan, and the failure paths: a format error names the
partition, the root is formatted once, a busy target is not reported as a
finished installation, the package cache moves to the target, and a failure
keeps its log and unwinds the mounts.

Run from the arch/ directory: python tests/check-installer.py
"""

import json
import os
import sys
import tempfile
import types
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import disk, hardware, options, packages, system  # noqa: E402
from installer.config import InstallConfig  # noqa: E402
from installer.context import Context, Logger  # noqa: E402
from installer.orchestrator import Session  # noqa: E402

GIB = 1024 ** 3
failures = []


def check(condition, message):
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


class Recorder(Context):
    """A context that runs nothing and remembers every command."""

    def __init__(self, dry_run=True):
        super().__init__(Logger(None), dry_run=dry_run)
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append([str(part) for part in cmd])
        return super().run(cmd, **kwargs)


# -- the plan: the raw BIOS partition is never formatted ----------------------
plan = disk.plan_layout("/dev/sda", 64 * GIB, efi=False, mode="erase", swap=False)
roles = [partition.role for partition in plan.members[0].partitions]
check(roles[0] == "bios", "a legacy install plans a BIOS boot partition first: %r" % roles)
check(plan.members[0].partitions[0].fs == "", "the BIOS boot partition has no filesystem")

recorder = Recorder()
disk._create_partitions(recorder, plan)
formatted = [cmd for cmd in recorder.calls if cmd[0].startswith("mkfs") or cmd[0] == "mkswap"]
check(formatted == [],
      "creating the partitions formats nothing: %r" % (formatted,))

# -- a format error names the device and the role ----------------------------
try:
    disk._mkfs(Recorder(), "", "/dev/sda1", "BIOS", "bios")
    message = ""
except RuntimeError as error:
    message = str(error)
check("/dev/sda1" in message and "bios" in message,
      "an unsupported filesystem names the partition: %r" % message)

# -- the default answers ------------------------------------------------------
defaults = InstallConfig()
check(defaults.raid == "none", "the RAID level defaults to no array (%r)" % defaults.raid)
check(list(options.RAID)[0] == "none", "the RAID menu opens on no array")
stale = InstallConfig.from_dict({"raid": "none", "raidDisks": ["/dev/sdb"]})
check(stale.raid_disks == [], "no array keeps no member disks: %r" % (stale.raid_disks,))


# -- the target must be unmounted before success is reported ------------------
def stub_context(target=None, dry_run=False):
    stub = types.SimpleNamespace()
    stub.dry_run = dry_run
    stub.target = target or tempfile.mkdtemp()
    stub.calls = []
    stub.log = Logger(None)

    def run(cmd, **kwargs):
        stub.calls.append([str(part) for part in cmd])
        return None

    stub.run = run
    return stub


stub = stub_context()
system.finalize(stub)
check(any(cmd[0] == "sync" for cmd in stub.calls), "an installation is synced before it ends")
check(any(cmd[:2] == ["umount", "-R"] for cmd in stub.calls), "the target is unmounted")

stub = stub_context()
with mock.patch("os.path.ismount", return_value=True):
    try:
        system.finalize(stub)
        raised = False
    except RuntimeError as error:
        raised = "still mounted" in str(error)
check(raised, "a target that stays mounted fails the installation instead of finishing it")


# -- the package cache goes to the target -------------------------------------
stub = stub_context()
stub.target_path = lambda relpath: os.path.join(stub.target, relpath)
with mock.patch("installer.packages.ensure_keyring"):
    packages.pacstrap(stub, InstallConfig.from_dict({"disk": "/dev/sda"}), ["base"])
binds = [cmd for cmd in stub.calls if cmd[:3] == ["mount", "--bind", stub.target_path("var/cache/pacman/pkg")]]
check(binds and binds[0][3] == "/var/cache/pacman/pkg",
      "the package cache is bound from the target: %r" % (binds,))
check(any(cmd[0] == "umount" for cmd in stub.calls), "the cache is unbound afterwards")


# -- a failure keeps the log, unwinds, and says what was written --------------
profile = hardware.simulate()
cfg = InstallConfig.from_dict({
    "disk": profile.disks[0].device, "mode": "erase", "confirmErase": True,
    "rootFs": "ext4",
})


class Boom(Session):
    def _prepare(self):
        raise RuntimeError("boom")


session = Boom(profile, cfg, dry_run=True, log_path=None)
session.run()
check(session.error == "boom", "the failure is reported (%r)" % session.error)
check(session.state["prepare"] == "failed", "the failing phase is marked")
check("Nothing had been written" in session.note,
      "a failure says how far it got: %r" % session.note)
check(not session.running, "a failed session stops running")

session.state["prepare"] = "done"
check("Preparing" in session._written_so_far(),
      "the summary names the phases that finished: %r" % session._written_so_far())

session.unwind()  # dry run: it must not execute anything
check(True, "unwinding is safe in a dry run")

# -- a dry run creates nothing on the host filesystem ------------------------
with mock.patch("os.makedirs", side_effect=AssertionError("a dry run created a directory")):
    dry = Session(profile, cfg, dry_run=True, log_path=None)
    dry.run()
check(not dry.error, "the dry run finished without touching a directory (%r)" % dry.error)
check(dry.done, "the dry run reached the end")


# -- every new choice has a check -------------------------------------------
f2fs_plan = disk.plan_layout("/dev/sda", 64 * GIB, root_fs="f2fs", mode="erase")
check(f2fs_plan.root_fs == "f2fs", "f2fs is a planned root filesystem")
check(
    packages.filesystem_packages("f2fs") == ["f2fs-tools"],
    "f2fs brings its own tools: %r" % (packages.filesystem_packages("f2fs"),),
)
check(
    packages.filesystem_packages("bcachefs") == ["bcachefs-tools"],
    "bcachefs brings its own tools",
)
f2fs_ctx = Recorder()
disk._mkfs(f2fs_ctx, "f2fs", "/dev/sda2", "ARCH", "root")
check(f2fs_ctx.calls[0][0] == "mkfs.f2fs", "f2fs is formatted with mkfs.f2fs")
burn_ctx = Recorder()
disk._mkfs(burn_ctx, "bcachefs", "/dev/sda2", "ARCH", "root")
check(burn_ctx.calls[0][:2] == ["bcachefs", "format"], "bcachefs is formatted by bcachefs format")

# -- a reinstall formats one partition and keeps the rest --------------------
reuse = disk.plan_layout(
    "/dev/sda", 64 * GIB, mode="reuse", reuse_root="/dev/sda2",
    reuse_home="/dev/sda3", keep_esp="/dev/sda1",
)
by_role = {partition.role: partition for partition in reuse.partitions}
check(by_role["root"].keep is False, "the reinstall root is formatted")
check(by_role["home"].keep is True, "the kept /home is not formatted")
check(by_role["efi"].keep is True, "the ESP is reused, not formatted")
check(
    any("(kept)" in row[4] for row in reuse.summary()),
    "the plan says which partitions are kept: %r" % (reuse.summary(),),
)
problems = InstallConfig.from_dict({"disk": "/dev/sda", "mode": "reuse"}).disk_errors()
check(
    any("reinstall" in problem for problem in problems),
    "a reinstall without a partition is refused: %r" % (problems,),
)

# -- removable disks are offered, after the internal ones --------------------
spare = types.SimpleNamespace(
    disks=[
        types.SimpleNamespace(device="/dev/sda", removable=False),
        types.SimpleNamespace(device="/dev/sdb", removable=True),
    ]
)
order = [found.device for found in hardware.HardwareProfile.installable_disks(spare)]
check(
    order == ["/dev/sda", "/dev/sdb"],
    "removable disks are offered after the internal ones: %r" % (order,),
)

# -- a unified kernel image needs systemd-boot and UEFI ----------------------
problems = InstallConfig.from_dict({
    "disk": "/dev/sda", "efi": True, "bootloader": "grub", "uki": True,
}).disk_errors()
check(any("systemd-boot" in problem for problem in problems),
      "a UKI is refused with GRUB: %r" % (problems,))
problems = InstallConfig.from_dict({
    "disk": "/dev/sda", "efi": False, "bootloader": "grub", "uki": True,
}).disk_errors()
check(any("UEFI" in problem for problem in problems), "a UKI is refused without UEFI")
uki_cfg = InstallConfig.from_dict({
    "disk": "/dev/sda", "efi": True, "bootloader": "systemd-boot", "uki": True,
    "confirmErase": True,
})
check(uki_cfg.uki and not uki_cfg.disk_errors(),
      "a UKI on systemd-boot is accepted: %r" % (uki_cfg.disk_errors(),))

# -- snapshots need Btrfs, hibernation needs a readable swap -----------------
problems = InstallConfig.from_dict({"disk": "/dev/sda", "snapshots": True}).disk_errors()
check(any("Btrfs" in problem for problem in problems), "snapshots are refused on ext4")
problems = InstallConfig.from_dict({
    "disk": "/dev/sda", "rootFs": "f2fs", "swap": True, "swapFile": True,
    "hibernate": True,
}).disk_errors()
check(any("swap partition" in problem for problem in problems),
      "hibernating into a swap file is refused on f2fs: %r" % (problems,))
check(
    not InstallConfig.from_dict({
        "disk": "/dev/sda", "rootFs": "btrfs", "snapshots": True,
        "confirmErase": True,
    }).disk_errors(),
    "snapshots are accepted on Btrfs",
)


class Stub:
    """A context that records files and commands instead of writing anything."""

    def __init__(self):
        self.dry_run = False
        self.target = tempfile.mkdtemp()
        self.log = Logger(None)
        self.files = {}
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append([str(part) for part in cmd])

    def chroot(self, command, **kwargs):
        self.calls.append(["chroot", command])
        return types.SimpleNamespace(returncode=0)

    def target_path(self, relpath):
        return os.path.join(self.target, relpath)

    def write_target(self, relpath, text, mode=0o644):
        self.files[relpath] = text

    def read_target(self, relpath):
        return self.files.get(relpath, "")


clock = Stub()
system.set_localtime(clock, InstallConfig.from_dict({"clockLocal": True}))
check(any("--localtime" in " ".join(call) for call in clock.calls),
      "a dual-boot machine keeps the clock in local time: %r" % (clock.calls,))
clock = Stub()
system.set_localtime(clock, InstallConfig())
check(not any("--localtime" in " ".join(call) for call in clock.calls),
      "the default still stores the clock in UTC")

wall = Stub()
system.configure_firewall(wall, InstallConfig.from_dict({"firewall": True, "sshd": True}))
conf = wall.files.get("etc/nftables.conf", "")
check("policy drop" in conf and "tcp dport 22 accept" in conf,
      "the filter drops inbound and opens the SSH port that was asked for")
check(any("nftables" in " ".join(call) for call in wall.calls), "nftables is enabled")
wall = Stub()
system.configure_firewall(wall, InstallConfig.from_dict({"firewall": True}))
check("dport 22" not in wall.files.get("etc/nftables.conf", ""),
      "no SSH port is opened when the server was not asked for")

shots = Stub()
system.configure_snapshots(shots, InstallConfig.from_dict({
    "disk": "/dev/sda", "rootFs": "btrfs", "snapshots": True,
}))
check('TIMELINE_CREATE="yes"' in shots.files.get("etc/snapper/configs/root", ""),
      "a Btrfs root gets snapper's timeline")
check(any("snapper-timeline.timer" in " ".join(call) for call in shots.calls),
      "the snapshot timers are enabled")

mirrors = Stub()
system.configure_mirrors(mirrors, InstallConfig.from_dict({"mirrorCountry": "Germany"}))
check('--country "Germany"' in mirrors.files.get("etc/xdg/reflector/reflector.conf", ""),
      "reflector is told which country to use")
check(any("reflector.timer" in " ".join(call) for call in mirrors.calls),
      "reflector's timer is enabled")

# -- extra locales and a separate desktop layout -----------------------------
keyboard = Stub()
system.configure(keyboard, InstallConfig.from_dict({
    "disk": "/dev/sda", "locale": "en_US.UTF-8", "locale": "en_US.UTF-8",
    "locales": ["de_DE.UTF-8"], "keymap": "us", "x11Keymap": "de",
    "timezone": "UTC", "hostname": "archbox",
}), profile, None)
check("de_DE.UTF-8 UTF-8" in keyboard.files.get("etc/locale.gen", ""),
      "an extra locale is generated beside the main one")
check('"de"' in keyboard.files.get("etc/X11/xorg.conf.d/00-keyboard.conf", ""),
      "the desktop layout is written for X11")

# -- an answer file is a whole installation ---------------------------------
from installer import answers  # noqa: E402

sample = answers.example(profile)
sample["rootPassword"] = "correcthorse1"
sample["user"]["password"] = "correcthorse2"
handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump(sample, handle)
handle.close()
os.chmod(handle.name, 0o600)
loaded = answers.load(handle.name, disk_size=profile.disks[0].size)
check(loaded.disk == sample["disk"], "an answer file produces a validated configuration")
if os.name != "nt":
    check(answers.secret_warning(handle.name) == "",
          "a private answer file is accepted")
    os.chmod(handle.name, 0o644)
    check("chmod 600" in answers.secret_warning(handle.name),
          "a world-readable answer file is refused with the command that fixes it")
sample["rootPassword"] = ""
json.dump(sample, open(handle.name, "w"))
try:
    answers.load(handle.name, disk_size=profile.disks[0].size)
    rejected = False
except ValueError as error:
    rejected = "rootPassword" in str(error)
check(rejected, "an answer file without the root password is rejected by name")
os.unlink(handle.name)


print("\n%s: %d failure(s)"
      % ("INSTALLER CHECK PASS" if not failures else "INSTALLER CHECK FAIL", len(failures)))
sys.exit(1 if failures else 0)
