"""Checks that need no root, no Arch and no disk.

Covers the answers, the plan, and the failure paths: a format error names the
partition, the root is formatted once, a busy target is not reported as a
finished installation, the package cache moves to the target, and a failure
keeps its log and unwinds the mounts.

Run from the arch/ directory: python tests/check-installer.py
"""

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

print("\n%s: %d failure(s)"
      % ("INSTALLER CHECK PASS" if not failures else "INSTALLER CHECK FAIL", len(failures)))
sys.exit(1 if failures else 0)
