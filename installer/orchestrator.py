"""The installation pipeline.

One :class:`Session` runs one installation on a background thread and publishes
progress for the window: phase states, a weighted percentage, the current
sub-step and the log tail.

The phase order follows the installation guide:
prepare -> partition/format/mount -> pacstrap -> extra packages -> fstab ->
locale/keyboard/hostname -> initramfs hooks and crypttab -> accounts ->
initramfs -> boot loader -> AUR packages -> services.
"""

from __future__ import annotations

import os
import platform
import threading
import time
import traceback

from . import aur, bootloader, disk, network, packages, system
from . import hardening
from .config import InstallConfig
from .context import Context, Logger
from .hardware import HardwareProfile

LOG_PATH = "/var/log/kiln-setup.log"
GIB = 1024 ** 3

#: (id, label, weight); the weights add up to 100, so the bar is an estimate
#: weighted by how long each step really takes rather than a timer.
PHASES = (
    ("prepare", "Preparing", 3),
    ("disk", "Partitioning and formatting", 9),
    ("pacstrap", "Installing the base system", 42),
    ("extras", "Installing selected software", 5),
    ("fstab", "Writing the file system table", 2),
    ("locale", "Locale, keyboard and hostname", 4),
    ("crypto", "Encryption and volumes", 2),
    ("users", "Accounts and passwords", 3),
    ("initramfs", "Building the initial ramdisk", 4),
    ("bootloader", "Installing the boot loader", 11),
    ("aur", "Building AUR packages", 6),
    ("services", "Enabling system services", 6),
    ("finish", "Finishing up", 3),
)

PHASE_LABELS = {pid: label for pid, label, _ in PHASES}

#: Tool -> the choice that makes it necessary on the live medium.
REQUIRED_TOOLS = {
    "pacstrap": "the base system",
    "pacman": "every package",
    "sgdisk": "partitioning",
    "mkfs.fat": "the EFI system partition",
}


class Session:
    def __init__(
        self,
        hw: HardwareProfile,
        cfg: InstallConfig,
        dry_run: bool = False,
        log_path: str | None = LOG_PATH,
    ) -> None:
        self.hw = hw
        self.cfg = cfg
        self.logger = Logger(log_path)
        self.ctx = Context(self.logger, dry_run=dry_run, hardware=hw)
        self.state = {pid: "pending" for pid, _, _ in PHASES}
        self.current = ""
        self.detail = ""
        self.error = ""
        self.running = False
        self.done = False
        self.started_at = 0.0
        self.finished_at = 0.0
        self.layout: disk.DiskPlan | None = None
        self.package_list: list = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- progress ----------------------------------------------------------
    @property
    def percent(self) -> int:
        total = 0
        for pid, _label, weight in PHASES:
            if self.state.get(pid) == "done":
                total += weight
            elif self.state.get(pid) == "running":
                total += int(weight * 0.35)
        return min(int(total), 99 if self.running else 100)

    def status(self) -> dict:
        with self._lock:
            elapsed = 0.0
            if self.started_at:
                end = self.finished_at or time.time()
                elapsed = end - self.started_at
            return {
                "running": self.running,
                "done": self.done,
                "error": self.error,
                "percent": self.percent,
                "current": self.current,
                "detail": self.detail,
                "elapsed": int(elapsed),
                "phases": [
                    {"id": pid, "label": label, "state": self.state.get(pid, "pending")}
                    for pid, label, _ in PHASES
                ],
                "finished": self.done or bool(self.error),
            }

    # -- helpers -----------------------------------------------------------
    def _phase(self, pid: str, function, *args, **kwargs):
        with self._lock:
            self.state[pid] = "running"
            self.current = pid
        self.logger.log("=== %s ===" % PHASE_LABELS[pid], "step")
        result = function(*args, **kwargs)
        with self._lock:
            self.state[pid] = "done"
            self.detail = ""
        return result

    def _substep(self, text: str) -> None:
        self.detail = text
        self.logger.log(text, "step")

    # -- phases ------------------------------------------------------------
    def _prepare(self) -> None:
        ctx = self.ctx
        self._substep("Checking the live medium")
        for tool, needed_for in REQUIRED_TOOLS.items():
            if not ctx.dry_run and not _has(tool):
                raise RuntimeError(
                    "%s is missing from the live medium (%s); boot the Arch installer medium"
                    % (tool, needed_for)
                )
        for tool, choice in (
            ("cryptsetup", self.cfg.encrypted),
            ("pvcreate", self.cfg.uses_lvm),
            ("mdadm", self.cfg.uses_raid),
        ):
            if choice and not ctx.dry_run and not _has(tool):
                raise RuntimeError(
                    "%s is missing from the live medium; the chosen storage layout needs it"
                    % tool
                )
        if self.cfg.shrink_partition and not ctx.dry_run and not _has("sfdisk"):
            raise RuntimeError(
                "sfdisk is missing from the live medium; shrinking %s needs it"
                % self.cfg.shrink_partition
            )
        if self.cfg.shrink_partition and not ctx.dry_run:
            # A FAT file system has no resizer of its own: parted does both the
            # file system and the table entry, so it has to be there.
            try:
                fstype = ctx.captured(
                    ["blkid", "-s", "TYPE", "-o", "value", self.cfg.shrink_partition],
                    quiet=True,
                ).strip().lower()
            except Exception:
                fstype = ""
            if fstype in ("vfat", "fat16", "fat32") and not _has("parted"):
                raise RuntimeError(
                    "parted is missing from the live medium; shrinking a FAT "
                    "partition needs it"
                )
        if self.cfg.multilib:
            self._substep("Enabling the 32-bit multilib repository")
            packages.enable_live_multilib(ctx)

        self._check_architecture()

        found = disk.find_disk(self.hw, self.cfg.disk)
        size = found.size if found else self._disk_size()
        if not ctx.dry_run and size <= 0:
            raise RuntimeError("cannot read the size of %s" % self.cfg.disk)

        if self.cfg.alongside and not self.cfg.keep_esp:
            self.cfg.keep_esp = disk.find_esp(self.hw, self.cfg.disk)
        if self.cfg.alongside and not self.cfg.keep_esp:
            raise RuntimeError(
                "no EFI system partition was found to keep; an installation alongside "
                "another system has to reuse its boot partition"
            )
        alongside_number = (
            disk.next_partition_number(self.hw, self.cfg.disk) if self.cfg.alongside else 0
        )

        raid_members = []
        if self.cfg.uses_raid:
            for device in self.cfg.raid_disks:
                member = disk.find_disk(self.hw, device)
                if member is None:
                    raise RuntimeError(
                        "%s is not present on this machine, so the array cannot be built"
                        % device
                    )
                raid_members.append((device, member.size))

        self._substep("Planning the layout for %s" % self.cfg.disk)
        self.layout = disk.plan_layout(
            self.cfg.disk,
            size,
            efi=self.cfg.efi,
            root_fs=self.cfg.root_fs,
            swap=self.cfg.swap,
            swap_gb=self.cfg.swap_gb,
            esp_gb=self.cfg.esp_gb,
            separate_home=self.cfg.separate_home,
            ram_bytes=self.hw.ram_bytes,
            mode=self.cfg.mode,
            encryption=self.cfg.encryption,
            volumes=self.cfg.volumes,
            raid=self.cfg.raid,
            raid_members=raid_members,
            swap_file=self.cfg.swap_file,
            keep_esp=self.cfg.keep_esp,
            alongside_number=alongside_number,
            encrypt_boot=self.cfg.encrypt_boot,
            hibernate=self.cfg.hibernate,
            shrink_partition=self.cfg.shrink_partition,
            shrink_gb=self.cfg.shrink_gb,
        )
        self.logger.log("layout: %s" % self.layout.to_dict(), "plan")
        self.logger.log("storage stack: %s" % " -> ".join(self.layout.stack()), "plan")
        self.package_list = packages.required_packages(self.cfg, self.hw)
        self.logger.log("packages: %s" % " ".join(self.package_list), "plan")
        if not ctx.dry_run:
            os.makedirs(ctx.target, exist_ok=True)

    def _check_architecture(self) -> None:
        """Refuse to place a system the medium cannot supply packages for."""
        if self.ctx.dry_run:
            return
        machine = platform.machine().lower()
        host = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
        if self.cfg.arch != host:
            raise RuntimeError(
                "this medium runs on %s but an %s system was requested. Packages and the "
                "EFI boot loader have to come from a medium built for %s."
                % (host, self.cfg.arch, self.cfg.arch)
            )

    def _disk_size(self) -> int:
        try:
            return int(self.ctx.captured(["blockdev", "--getsize64", self.cfg.disk]))
        except Exception:
            return 0

    def _disk(self) -> None:
        assert self.layout is not None
        if self.cfg.alongside:
            if self.cfg.shrink_partition:
                self._substep(
                    "Shrinking %s to %d GiB, then using the space it freed"
                    % (self.cfg.shrink_partition, self.cfg.shrink_gb)
                )
            else:
                self._substep(
                    "Adding one partition in the free space of %s" % self.cfg.disk
                )
        else:
            self._substep("Erasing %s and creating partitions" % self.cfg.disk)
            disk.wipe_signatures(self.ctx, self.layout)
        disk.apply_layout(self.ctx, self.layout, passphrase=self.cfg.luks_passphrase)

    def _pacstrap(self) -> None:
        packages.pacstrap(self.ctx, self.cfg, self.package_list)

    def _extras(self) -> None:
        ctx = self.ctx
        self._substep("Writing pacman.conf%s" % (" with multilib" if self.cfg.multilib else ""))
        packages.write_target_conf(ctx, self.cfg)
        chosen = packages.multilib_packages(self.cfg)
        if chosen:
            self._substep("Installing 32-bit libraries: %s" % ", ".join(chosen))
            packages.refresh(ctx)
            packages.install(ctx, chosen)
        else:
            # Keep the target's sync database in step with its own pacman.conf.
            packages.refresh(ctx)

    def _fstab(self) -> None:
        ctx = self.ctx
        self._substep("Generating /etc/fstab from the mounted file systems")
        if ctx.dry_run:
            return
        text = ctx.captured(["genfstab", "-U", ctx.target], quiet=True)
        if not text.strip():
            raise RuntimeError("genfstab produced an empty /etc/fstab")
        ctx.write_target("etc/fstab", text.rstrip() + "\n")
        if self.layout is not None and self.layout.swap_file:
            # genfstab only knows devices, so the swap file is added here.
            current = ctx.read_target("etc/fstab")
            ctx.write_target(
                "etc/fstab",
                current.rstrip()
                + "\n%s none swap defaults 0 0\n" % self.layout.swap_file,
            )

    def _locale(self) -> None:
        system.configure(self.ctx, self.cfg, self.hw, self.layout)  # type: ignore[arg-type]

    def _crypto(self) -> None:
        self._substep(
            "Setting the initramfs hooks for %s"
            % ("an encrypted root" if self.cfg.encrypted else "this layout")
        )
        system.configure_crypto(self.ctx, self.cfg, self.layout)
        # The key file and the TPM enrolment happen while the container is open:
        # both add a key slot to the container that /mnt is mounted from.
        self._substep("Adding the second way into the container")
        system.install_keyfile(self.ctx, self.cfg, self.layout)
        system.enroll_tpm(self.ctx, self.cfg, self.layout)

    def _users(self) -> None:
        system.accounts(self.ctx, self.cfg)

    def _initramfs(self) -> None:
        # Signing comes first: the initramfs should carry signed modules.
        hardening.sign_modules(self.ctx, self.cfg)
        self._substep("Running mkinitcpio for the installed kernel(s)")
        system.build_initramfs(self.ctx)

    def _bootloader(self) -> None:
        assert self.layout is not None
        bootloader.install(self.ctx, self.cfg, self.layout)

    def _aur(self) -> None:
        aur.install(self.ctx, self.cfg)

    def _services(self) -> None:
        hardening.apply(self.ctx, self.cfg, self.layout)
        network.write_profiles(self.ctx, self.cfg)
        system.enable_services(self.ctx, self.cfg)
        system.enable_fstrim(self.ctx)

    def _finish(self) -> None:
        ctx = self.ctx
        system.install_panel(ctx, self.cfg)
        system.copy_log(ctx, LOG_PATH)
        system.finalize(ctx)
        self.logger.log("installation complete", "step")

    # -- driver ------------------------------------------------------------
    def run(self) -> None:
        self.running = True
        self.started_at = time.time()
        self.logger.log("installation started for %s" % self.cfg.disk, "step")
        steps = [
            ("prepare", self._prepare),
            ("disk", self._disk),
            ("pacstrap", self._pacstrap),
            ("extras", self._extras),
            ("fstab", self._fstab),
            ("locale", self._locale),
            ("crypto", self._crypto),
            ("users", self._users),
            ("initramfs", self._initramfs),
            ("bootloader", self._bootloader),
            ("aur", self._aur),
            ("services", self._services),
            ("finish", self._finish),
        ]
        try:
            for pid, function in steps:
                self._phase(pid, function)
            self.done = True
        except Exception as error:  # keep the window alive and show why
            with self._lock:
                self.error = str(error)
                if self.current:
                    self.state[self.current] = "failed"
            self.logger.log("FAILED: %s" % error, "error")
            for line in traceback.format_exc().splitlines():
                self.logger.log(line, "error")
        finally:
            with self._lock:
                self.running = False
                self.finished_at = time.time()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("an installation is already running")
        self._thread = threading.Thread(target=self.run, name="arch-install", daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)


def _has(tool: str) -> bool:
    from .context import which

    return which(tool) is not None
