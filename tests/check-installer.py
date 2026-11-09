"""Checks that need no root, no Arch and no disk.

Covers the answers, the plan, and the failure paths: a format error names the
partition, the root is formatted once, a busy target is not reported as a
finished installation, the package cache moves to the target, and a failure
keeps its log and unwinds the mounts.

Run from the arch/ directory: python tests/check-installer.py
"""

import json
import os
import subprocess
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

# -- the medium ships the tools the new filesystems need ---------------------
medium = {
    line.strip()
    for line in open(
        os.path.join(ROOT, "iso", "profile", "packages.x86_64"), encoding="utf-8"
    ).read().splitlines()
    if line.strip() and not line.strip().startswith("#")
}
check(
    {"f2fs-tools", "bcachefs-tools"} <= medium,
    "the medium ships the new formatters (missing: %r)"
    % sorted({"f2fs-tools", "bcachefs-tools"} - medium),
)

# -- the panel says why it did not open -------------------------------------
check("kiln-panel.log" in system.PANEL_LAUNCHER,
      "the launcher keeps its output in a log the user can read")
check('cd "$ROOT"' in system.PANEL_LAUNCHER,
      "the launcher runs from /opt/kiln-setup, not from the user's directory")
check("zenity" in system.PANEL_LAUNCHER and "kdialog" in system.PANEL_LAUNCHER,
      "a failure ends in a dialog instead of nothing")
check("TryExec=/usr/local/bin/kiln-panel" in system.PANEL_DESKTOP,
      "the menu entry names the binary it starts")
check("Version=1.0" in system.PANEL_DESKTOP, "the menu entry has a version")

panel_root = tempfile.mkdtemp()
for name in ("installer", "panel"):
    os.makedirs(os.path.join(panel_root, name))
    open(os.path.join(panel_root, name, "__main__.py"), "w").close()
launcher = Stub()
with mock.patch.object(system, "MEDIUM_ROOT", panel_root):
    system.install_panel(launcher, InstallConfig())
check("usr/local/bin/kiln-panel" in launcher.files,
      "the launcher is written once the panel is in place")
check("usr/share/applications/kiln-panel.desktop" in launcher.files,
      "the menu entry is written once the panel is in place")

os.unlink(os.path.join(panel_root, "panel", "__main__.py"))
launcher = Stub()
with mock.patch.object(system, "MEDIUM_ROOT", panel_root):
    system.install_panel(launcher, InstallConfig())
check("usr/share/applications/kiln-panel.desktop" not in launcher.files,
      "no menu entry is written for a panel that did not arrive")

doctor = subprocess.run(
    [sys.executable, "-m", "panel", "--doctor"],
    capture_output=True, text=True, timeout=120,
)
check(doctor.returncode in (0, 1) and "Display" in (doctor.stdout + doctor.stderr),
      "the panel can explain itself (rc=%d)" % doctor.returncode)

# -- a closed stdout does not stop the panel ---------------------------------
quiet = Logger(None)
with mock.patch("builtins.print", side_effect=OSError(9, "Bad file descriptor")):
    try:
        quiet.log("the first line of a panel started from a menu entry")
        survived = True
    except OSError:
        survived = False
check(survived, "a log line cannot stop the panel when stdout is closed")

# -- the volume manager opens on plain partitions ---------------------------
check(options.DEFAULT_VOLUME == "partitions",
      "plain partitions is the default volume manager")
check(list(options.VOLUMES)[0] == "partitions",
      "the volume manager menu opens on plain partitions")
check(InstallConfig().volumes == "partitions",
      "a fresh request keeps the filesystem on the partition")
check(InstallConfig.from_dict({}).uses_lvm is False,
      "nothing adds a volume group by default")
plain_plan = disk.plan_layout("/dev/sda", 64 * GIB)
check(plain_plan.volumes == "partitions" and plain_plan.stack() == ["ext4"],
      "the plan has no LVM layer unless it was asked for: %r" % (plain_plan.stack(),))
lvm_plan = disk.plan_layout("/dev/sda", 64 * GIB, volumes="lvm")
check("LVM (vg0/root)" in lvm_plan.stack(),
      "LVM is still available when chosen: %r" % (lvm_plan.stack(),))
stale_volume = InstallConfig.from_dict({"volumes": ""})
check(stale_volume.volumes == "partitions",
      "an empty answer falls back to plain partitions: %r" % stale_volume.volumes)

# -- the medium must carry a UEFI boot entry the firmware can catalogue ------
profile = open("iso/profile/profiledef.sh", encoding="utf-8").read()
check("'uefi-x64.systemd-boot.esp'" in profile,
      "the profile builds the UEFI system partition for USB media")
check("'uefi-x64.systemd-boot.eltorito'" in profile,
      "the profile registers a UEFI El Torito image, so EFI firmware can boot the disc")
check("'bios.syslinux'" in profile,
      "the profile declares the BIOS boot mode")


# -- the ESP has to hold the loader a firmware entry names -------------------
from installer import bootloader  # noqa: E402


def loader_error(context, config):
    try:
        bootloader._verify_efi_loader(context, config)
        return ""
    except RuntimeError as error:
        return str(error)


esp = tempfile.mkdtemp()
os.makedirs(os.path.join(esp, "boot"))
os.makedirs(os.path.join(esp, "boot", "EFI", "BOOT"))
os.makedirs(os.path.join(esp, "boot", "EFI", "Kiln"))
open(os.path.join(esp, "boot", "EFI", "Kiln", "grubx64.efi"), "w").close()
open(os.path.join(esp, "boot", "EFI", "BOOT", "BOOTX64.EFI"), "w").close()
grub_esp = types.SimpleNamespace(dry_run=False, target=esp, log=Logger(None))
grub_esp.target_path = lambda relpath: os.path.join(esp, relpath)
grub_efi = InstallConfig.from_dict({"disk": "/dev/sda", "efi": True, "bootloader": "grub"})
check(loader_error(grub_esp, grub_efi) == "",
      "a GRUB ESP that holds its loader passes the check")

os.unlink(os.path.join(esp, "boot", "EFI", "Kiln", "grubx64.efi"))
message = loader_error(grub_esp, grub_efi)
check("failed to load Boot0001" in message and "grubx64.efi" in message,
      "a missing loader fails the install instead of leaving a machine that will "
      "not boot: %r" % message)

open(os.path.join(esp, "boot", "EFI", "Kiln", "grubx64.efi"), "w").close()
os.unlink(os.path.join(esp, "boot", "EFI", "BOOT", "BOOTX64.EFI"))
check(loader_error(grub_esp, grub_efi) == "",
      "the removable copy is not required for the check to pass")
check(any("BOOTX64.EFI" in line for line in grub_esp.log.tail(50)),
      "a missing removable loader is reported: %r" % (grub_esp.log.tail(3),))

recorder = Stub()
sb_cfg = InstallConfig.from_dict({
    "disk": "/dev/sda", "efi": True, "bootloader": "systemd-boot",
})
with mock.patch.object(bootloader, "_verify_kernel"):
    with mock.patch.object(bootloader, "_verify_efi_loader"):
        with mock.patch.object(bootloader, "entry_text", return_value="title Arch"):
            bootloader.install_systemd_boot(
                recorder, sb_cfg, disk.plan_layout("/dev/sda", 64 * GIB, efi=True)
            )
joined = [" ".join(call) for call in recorder.calls]
mount_at = next((i for i, line in enumerate(joined) if "efivarfs" in line), None)
bootctl_at = next((i for i, line in enumerate(joined) if "bootctl" in line), None)
check(mount_at is not None and bootctl_at is not None and mount_at < bootctl_at,
      "efivarfs is mounted before bootctl writes the firmware entry: %r" % (joined,))
check(any("arch.conf" in path for path in recorder.files),
      "systemd-boot still writes its loader entry")



# -- the Epic Games anti-cheat profile is not part of the installer ------------
import re  # noqa: E402

from installer import bootloader  # noqa: E402

check(
    not hasattr(InstallConfig, "eac") and "eac" not in InstallConfig.__annotations__,
    "the Epic Games answer is gone from the configuration",
)
check(
    "eac" not in InstallConfig().to_public(),
    "an installation request carries no anti-cheat setting: %r"
    % (sorted(InstallConfig().to_public()),),
)
check(
    InstallConfig.from_dict({"disk": "/dev/sda", "eac": True}).secure_boot is False,
    "an old answer that still says eac is ignored, not turned into Secure Boot "
    "signing",
)
check(
    not os.path.exists(os.path.join(ROOT, "installer", "anticheat.py")),
    "installer/anticheat.py is gone, so no boot entry can carry its parameters",
)
installer_source = "".join(
    open(os.path.join(ROOT, "installer", name), encoding="utf-8").read()
    for name in sorted(os.listdir(os.path.join(ROOT, "installer")))
    if name.endswith(".py")
)
check(
    re.search(r"\b(?:anticheat|eac|EAC)\b", installer_source) is None,
    "nothing under installer/ mentions the anti-cheat any more: %r"
    % (re.findall(r".{0,20}\b(?:anticheat|eac|EAC)\b.{0,20}", installer_source)[:3],),
)
readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
check(
    "Epic Games and anti-cheat" not in readme and "EAC Linux" not in readme,
    "the README no longer documents a profile the installer does not set up",
)

from installer import packages as package_layer  # noqa: E402

check(
    "ttf-dejavu" in package_layer.required_packages(InstallConfig()),
    "every install carries a font: the panel's Tk window cannot draw a label "
    "without one, and the window then exits 1 with a bare TclError",
)

from installer import system as system_layer  # noqa: E402

check(
    "${detail}" in system_layer.PANEL_LAUNCHER,
    "the launcher puts the panel's last log line in the dialog, so a failure "
    "names the error instead of only the exit status",
)

check(
    "--doctor|--check|--version" in system_layer.PANEL_LAUNCHER
    and "exec /usr/bin/python3 -m panel" in system_layer.PANEL_LAUNCHER,
    "a terminal request skips the log, so 'kiln-panel --doctor' prints where it "
    "was typed instead of hiding the diagnosis in the log",
)

no_swap = disk.plan_layout("/dev/sda", 64 * GIB, swap=False, swap_file=True)
check(
    no_swap.swap is None and not no_swap.swap_file_bytes and not no_swap.swap_file,
    "swap turned off creates no swap partition and no swap file, even when the "
    "kind chosen earlier was a file",
)
cfg_no_swap = InstallConfig.from_dict({
    "disk": "/dev/sda", "confirmErase": True, "swap": False, "swapFile": True,
    "swapGb": 8,
})
check(
    not cfg_no_swap.swap and not cfg_no_swap.swap_file,
    "a swap file does not outlive turning swap off: %r" % (cfg_no_swap.to_public(),),
)
check(
    not any("swap" in problem.lower() for problem in cfg_no_swap.disk_errors(64 * GIB)),
    "turning swap off leaves the disk answers valid: %r"
    % (cfg_no_swap.disk_errors(64 * GIB),),
)

tail_src = """LABEL existing
TEXT HELP
Boot an existing operating system.
ENDTEXT
MENU LABEL Boot existing OS
COM32 chain.c32
APPEND hd0 0

LABEL memtest
MENU LABEL Run Memtest86+ (RAM test)
LINUX /boot/memtest86+/memtest

LABEL reboot
MENU LABEL Reboot
COM32 reboot.c32
"""
with tempfile.TemporaryDirectory() as tmp:
    source = os.path.join(tmp, "archiso_tail.cfg")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write(tail_src)
    try:
        written = subprocess.run(
            ["awk", "-f", os.path.join(ROOT, "iso", "localboot.awk"), source],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        written = None
check(written is not None, "awk can rewrite the BIOS menu (awk is installed)")
if written is not None:
    check("LOCALBOOT -1" in written,
          "the BIOS hand-over is LOCALBOOT: %r" % (written,))
    check("chain.c32" not in written,
          "nothing is read off the medium to start the installed system")
    check("LABEL memtest" in written and "memtest86+" in written
          and 'COM32 reboot.c32' in written,
          "every other menu entry survives the rewrite: %r" % (written,))
    check("Boot existing OS" not in written,
          "the entry no longer claims to boot an existing OS while loading a module")

build_script = open(os.path.join(ROOT, "iso", "build-iso.sh"), encoding="utf-8").read()
check(
    "retarget_localboot" in build_script and "localboot.awk" in build_script,
    "the build applies the local-boot rewrite to the profile it builds",
)

check(
    'GRUB_DISTRIBUTOR="Arch"' in bootloader.GRUB_HEADER,
    "the GRUB menu names Arch Linux: grub titles every entry from the distributor",
)
check(
    'GRUB_DISTRIBUTOR="Kiln"' not in bootloader.GRUB_HEADER,
    "the installer's name is not what starts the installed system",
)
retitled, retitled_changes = bootloader.retitle_menu(
    "menuentry 'Kiln Linux, with Linux linux' --class arch --class gnu-linux {\n"
    "\tlinux /vmlinuz-linux root=UUID=abc rw\n"
    "}\n"
    "menuentry 'Arch Linux, with Linux linux (fallback)' {\n"
    "}\n"
)
check(
    retitled_changes == 1
    and "menuentry 'Arch Linux, with Linux linux' --class arch" in retitled,
    "an entry titled after the installer is rewritten, and the rest of the line "
    "survives: %r" % (retitled,),
)
check(
    "menuentry 'Arch Linux, with Linux linux (fallback)'" in retitled,
    "an entry that already says Arch Linux is left alone",
)
grub_text = open(os.path.join(ROOT, "installer", "bootloader.py"),
                 encoding="utf-8").read()
check(
    "_fix_menu_titles(ctx)" in grub_text,
    "the repair runs on the grub.cfg that grub-mkconfig wrote",
)

from installer.gui import theme as theme_module  # noqa: E402

check(
    theme_module.colour_problems() == [],
    "every palette colour is a #rrggbb value Tk can draw: %r"
    % (theme_module.colour_problems(),),
)


class _TamperedPalette:
    pass


for _name in theme_module.COLOUR_NAMES:
    setattr(_TamperedPalette, _name, getattr(theme_module.Theme, _name))
_TamperedPalette.panel = "BACKGROUND"
check(
    theme_module.colour_problems(_TamperedPalette) == [("panel", "BACKGROUND")],
    "a palette entry that is not a colour is named by the check instead of "
    "reaching Tk as \"unknown color name BACKGROUND\": %r"
    % (theme_module.colour_problems(_TamperedPalette),),
)
doctor_source = open(os.path.join(ROOT, "panel", "__main__.py"), encoding="utf-8").read()
check(
    "colour_problems" in doctor_source and "extract_tb" in doctor_source,
    "--doctor reports the palette and the startup message names the failing line",
)

from panel import __main__ as panel_main  # noqa: E402
import traceback  # noqa: E402

frames = [
    traceback.FrameSummary(os.path.join(ROOT, "panel", "pages.py"), 812, "build"),
    traceback.FrameSummary("/usr/lib/python3.13/tkinter/__init__.py", 2505, "configure"),
]
check(
    panel_main._failing_frame(frames).filename.endswith(os.path.join("panel", "pages.py")),
    "a Tk error is reported at the call in this project, not at tkinter/__init__.py: %r"
    % (panel_main._frame_label(panel_main._failing_frame(frames)),),
)
check(
    panel_main._frame_label(frames[0]).replace(os.sep, "/").endswith("panel/pages.py:812"),
    "the reported line is readable beside /opt/kiln-setup: %r"
    % (panel_main._frame_label(frames[0]),),
)
check(
    panel_main._failing_frame([frames[1]]).filename.endswith("__init__.py"),
    "with no frame of its own, the tkinter frame is still reported rather than nothing",
)

check(
    panel_main._resource_colour_problems(
        {"*background": "BACKGROUND", "Xft.dpi": "96", "*foreground": "#1a1a1a"}
    )
    == [("*background", "BACKGROUND")],
    "a colour in the display's X resource database that is not one is named: %r"
    % (
        panel_main._resource_colour_problems(
            {"*background": "BACKGROUND", "Xft.dpi": "96", "*foreground": "#1a1a1a"}
        ),
    ),
)
check(
    panel_main._resource_colour_problems({"*background": "#1a1a1a", "*foreground": "black"})
    == [],
    "valid X resources are left alone",
)
doctor_text = open(os.path.join(ROOT, "panel", "__main__.py"), encoding="utf-8").read()
check(
    "X resources :" in doctor_text and "_resource_colour_problems" in doctor_text,
    "--doctor prints the X resource database, which Tk reads before the panel's code",
)

# The rail is a menu in the panel and a progress indicator in the installer.
import inspect  # noqa: E402
from installer.gui.widgets import StepRail  # noqa: E402

check(
    "on_select" in inspect.signature(StepRail.__init__).parameters,
    "the rail takes a click handler, which the installer leaves unset",
)
widgets_source = open(
    os.path.join(ROOT, "installer", "gui", "widgets.py"), encoding="utf-8"
).read()
check(
    "<Button-1>" in widgets_source
    and "for widget in (row, bar, dot, text):" in widgets_source,
    "a click on the row, its bar, its dot or its label all reach the handler: "
    "Tk does not bubble a child's click to its parent",
)
panel_app_source = open(
    os.path.join(ROOT, "panel", "app.py"), encoding="utf-8"
).read()
check(
    "on_select=self.goto" in panel_app_source,
    "the panel hands the rail its own goto(), so every section can be opened: "
    "without it the rows were drawn but did nothing",
)

# -- the Xfce desktop gets the Kiln look ---------------------------------------
import shutil  # noqa: E402
import struct as struct_module  # noqa: E402
import tempfile  # noqa: E402
import xml.etree.ElementTree as element_tree  # noqa: E402

from installer import options as options_module  # noqa: E402
from installer import xfce  # noqa: E402
from installer.context import Context, Logger  # noqa: E402

check(
    xfce.THEME == "Materia-dark" and xfce.ICONS == "Papirus-Dark",
    "the Xfce look is a dark GTK theme with the Papirus icon set: %r, %r"
    % (xfce.THEME, xfce.ICONS),
)
xfce_packages = options_module.desktop_packages("xfce")
check(
    "materia-gtk-theme" in xfce_packages and "papirus-icon-theme" in xfce_packages,
    "an Xfce install carries the theme and the icons it is told to use: %r"
    % (xfce_packages,),
)
check(
    set(xfce.wallpapers()) >= set(xfce.WALLPAPERS),
    "the wallpapers the desktop points at travel inside the package: %r"
    % (xfce.wallpapers(),),
)
for _name in xfce.WALLPAPERS:
    with open(os.path.join(xfce.wallpaper_source(), _name), "rb") as _handle:
        _header = _handle.read(24)
    check(
        _header[:8] == b"\x89PNG\r\n\x1a\n",
        "%s is a PNG" % _name,
    )
    check(
        struct_module.unpack(">II", _header[16:24]) == (2560, 1440),
        "%s is a 2560x1440 wallpaper, not a placeholder: %r"
        % (_name, struct_module.unpack(">II", _header[16:24])),
    )

_xfce_root = tempfile.mkdtemp(prefix="kiln-xfce-")
try:
    _written = xfce.apply(
        Context(Logger(None), target=_xfce_root), InstallConfig(desktop="xfce")
    )
    check(
        len(_written) == 9 + len(xfce.wallpapers()),
        "the look writes the theme, the window manager, the panel, the desktop, "
        "the session and the greeter, and copies the wallpapers: %d files"
        % (len(_written),),
    )
    for _name in xfce.WALLPAPERS:
        check(
            os.path.isfile(
                os.path.join(_xfce_root, "usr/share/backgrounds/kiln", _name)
            ),
            "the wallpaper is copied into the new system: %s" % _name,
        )
    _channel_dir = os.path.join(
        _xfce_root, "etc/xdg/xfce4/xfconf/xfce-perchannel-xml"
    )
    for _channel in (
        "xsettings.xml", "xfwm4.xml", "xfce4-panel.xml", "xfce4-desktop.xml",
        "xfce4-session.xml", "thunar.xml",
    ):
        try:
            element_tree.parse(os.path.join(_channel_dir, _channel))
        except Exception as _error:
            check(False, "%s is valid XML: %s" % (_channel, _error))
        else:
            check(True, "%s is valid XML that Xfce reads as a channel" % _channel)
    _panel_xml = os.path.join(_channel_dir, "xfce4-panel.xml")
    _desktop_xml = os.path.join(_channel_dir, "xfce4-desktop.xml")
    _xfwm_xml = os.path.join(_channel_dir, "xfwm4.xml")
    _thunar_xml = os.path.join(_channel_dir, "thunar.xml")
    _desktop = open(_desktop_xml, encoding="utf-8").read()
    check(
        xfce.DEFAULT_WALLPAPER in _desktop and "monitor0" in _desktop,
        "the desktop points at a wallpaper that exists, on the monitor names "
        "Xfce uses",
    )
    _panel = open(
        os.path.join(_channel_dir, "xfce4-panel.xml"), encoding="utf-8"
    ).read()
    check(
        "background-rgba" in _panel and "plugin-ids" in _panel,
        "the panel is dark and carries plugins instead of being left empty",
    )

    def _xfconf_value(path, names):
        node = element_tree.parse(path).getroot()
        for name in names:
            node = node.find("property[@name='%s']" % name)
            if node is None:
                return None
        return node.get("value")

    _panel_size = _xfconf_value(_panel_xml, ["panels", "panel-1", "size"])
    check(
        _panel_size == "30",
        "the panel is a slim 30px bar instead of a thick slab: %r" % (_panel_size,),
    )
    _icons_style = _xfconf_value(_desktop_xml, ["desktop-icons", "style"])
    check(
        _icons_style == "0",
        "the desktop shows no icons, so the wallpaper is the desktop: %r"
        % (_icons_style,),
    )
    _buttons = _xfconf_value(_xfwm_xml, ["general", "button_layout"])
    check(
        _buttons == "|HMC",
        "the title bar keeps only hide, maximise and close, on the right: %r"
        % (_buttons,),
    )
    _view = _xfconf_value(_thunar_xml, ["last-view"])
    check(
        _view == "ThunarDetailsView",
        "Thunar opens as a clean details list: %r" % (_view,),
    )
    _gtk3 = open(
        os.path.join(_xfce_root, "etc/gtk-3.0/settings.ini"), encoding="utf-8"
    ).read()
    check(
        "gtk-theme-name=Materia-dark" in _gtk3
        and "gtk-icon-theme-name=Papirus-Dark" in _gtk3
        and "gtk-application-prefer-dark-theme=1" in _gtk3,
        "every GTK application, not only the desktop, opens in the same dark "
        "theme and icon set",
    )
    _gtk2 = open(
        os.path.join(_xfce_root, "etc/gtk-2.0/gtkrc"), encoding="utf-8"
    ).read()
    check(
        'gtk-theme-name = "Materia-dark"' in _gtk2,
        "GTK 2 applications are not left in the stock light theme either",
    )

    # The boot menu is the first screen of the installed system.
    from installer import bootloader as bootloader_module  # noqa: E402

    _theme_root = tempfile.mkdtemp(prefix="kiln-grub-")
    try:
        _theme_written = bootloader_module.write_grub_theme(
            Context(Logger(None), target=_theme_root)
        )
        _theme_txt = os.path.join(
            _theme_root, "boot/grub/themes/kiln/theme.txt"
        )
        _theme_text = open(_theme_txt, encoding="utf-8").read()
        check(
            'desktop-image: "background.png"' in _theme_text
            and "+ boot_menu" in _theme_text
            and _theme_text.count("{") == _theme_text.count("}"),
            "the GRUB menu has a theme with balanced blocks, so GRUB can read it",
        )
        check(
            os.path.isfile(
                os.path.join(_theme_root, "boot/grub/themes/kiln/background.png")
            ),
            "the wallpaper behind the boot menu is written next to it: %r"
            % (_theme_written,),
        )
        check(
            "GRUB_THEME" in bootloader_module.grub_defaults(
                InstallConfig(efi=True, bootloader="grub"), None, "AuthenticAMD"
            ),
            "the installed GRUB is told to use the theme",
        )
        check(
            "GRUB_THEME" not in bootloader_module.grub_defaults(
                InstallConfig(efi=True, bootloader="grub", encrypt_boot=True),
                None,
                "AuthenticAMD",
            ),
            "an encrypted /boot keeps the plain menu rather than pointing at a "
            "theme that is not on that volume",
        )
    finally:
        shutil.rmtree(_theme_root, ignore_errors=True)
    check(
        xfce.apply(
            Context(Logger(None), target=_xfce_root), InstallConfig(desktop="kde")
        )
        == [],
        "another desktop gets none of it",
    )
finally:
    shutil.rmtree(_xfce_root, ignore_errors=True)

_orchestrator_source = open(
    os.path.join(ROOT, "installer", "orchestrator.py"), encoding="utf-8"
).read()
check(
    "xfce.apply(ctx, self.cfg)" in _orchestrator_source,
    "the installation runs the look for an Xfce install",
)

# -- the look is clean: text has contrast, and nothing bypasses the palette -----
for _fg, _bg, _label in (
    (theme_module.INK, theme_module.PANEL, "body text on the content sheet"),
    (theme_module.MUTED, theme_module.PANEL, "secondary text"),
    (theme_module.FAINT, theme_module.PANEL, "the faintest label"),
    (theme_module.RAIL_FAINT, theme_module.RAIL, "the rail's footer"),
    ("#ffffff", theme_module.ACCENT, "a white label on the primary button"),
    (theme_module.OK, theme_module.PANEL, "success text"),
    (theme_module.WARN, theme_module.PANEL, "warning text"),
    (theme_module.FAIL, theme_module.PANEL, "failure text"),
    (theme_module.FAIL, theme_module.FAIL_SOFT, "failure text on its banner"),
    (theme_module.WARN, theme_module.WARN_SOFT, "warning text on its banner"),
    (theme_module.OK, theme_module.OK_SOFT, "success text on its banner"),
):
    _ratio = theme_module.contrast(_fg, _bg)
    check(
        _ratio >= 4.5,
        "%s has 4.5:1 contrast: %.2f:1 (%s on %s)"
        % (_label, _ratio, _fg, _bg),
    )

_gui_widgets = open(
    os.path.join(ROOT, "installer", "gui", "widgets.py"), encoding="utf-8"
).read()
for _stray in (
    "#fde7e9", "#a4262c", "#fff4ce", "#9d5d00", "#dff6dd", "#f0f6fc",
    "#8a8a8a", "#c7c7c7",
):
    check(
        _stray not in _gui_widgets,
        "the widgets draw with the palette, not %s of their own" % _stray,
    )

print("\n%s: %d failure(s)"
      % ("INSTALLER CHECK PASS" if not failures else "INSTALLER CHECK FAIL", len(failures)))
sys.exit(1 if failures else 0)
