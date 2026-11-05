"""The installation pipeline.

One :class:`Session` runs one installation on a background thread and publishes
progress for the window: phase states, a weighted percentage, the current
sub-step and the log tail.

The phase order follows the installation guide:
prepare -> partition/format/mount -> pacstrap -> extra packages -> fstab ->
locale/keyboard/hostname -> accounts -> initramfs -> boot loader -> services.
"""

from __future__ import annotations

import os
import threading
import time
import traceback

from . import bootloader, disk, network, packages, system
from .config import InstallConfig
from .context import Context, Logger
from .hardware import HardwareProfile

LOG_PATH = "/var/log/kiln-setup.log"
GIB = 1024 ** 3

#: (id, label, weight); the weights add up to 100, so the bar is an estimate
#: weighted by how long each step really takes rather than a timer.
PHASES = (
    ("prepare", "Preparing", 3),
    ("disk", "Partitioning and formatting", 10),
    ("pacstrap", "Installing the base system", 46),
    ("extras", "Installing selected software", 6),
    ("fstab", "Writing the file system table", 2),
    ("locale", "Locale, keyboard and hostname", 5),
    ("users", "Accounts and passwords", 4),
    ("initramfs", "Building the initial ramdisk", 4),
    ("bootloader", "Installing the boot loader", 12),
    ("services", "Enabling system services", 5),
    ("finish", "Finishing up", 3),
)

PHASE_LABELS = {pid: label for pid, label, _ in PHASES}


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
        for tool in ("pacstrap", "pacman", "sgdisk", "mkfs.fat"):
            if not ctx.dry_run and not _has(tool):
                raise RuntimeError(
                    "%s is missing from the live medium; boot the Arch installer medium"
                    % tool
                )
        if self.cfg.multilib:
            self._substep("Enabling the 32-bit multilib repository")
            packages.enable_live_multilib(ctx)

        found = disk.find_disk(self.hw, self.cfg.disk)
        size = found.size if found else self._disk_size()
        if not ctx.dry_run and size <= 0:
            raise RuntimeError("cannot read the size of %s" % self.cfg.disk)
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
        )
        self.logger.log("layout: %s" % self.layout.to_dict(), "plan")
        self.package_list = packages.required_packages(self.cfg, self.hw)
        self.logger.log("packages: %s" % " ".join(self.package_list), "plan")
        if not ctx.dry_run:
            os.makedirs(ctx.target, exist_ok=True)

    def _disk_size(self) -> int:
        try:
            return int(self.ctx.captured(["blockdev", "--getsize64", self.cfg.disk]))
        except Exception:
            return 0

    def _disk(self) -> None:
        assert self.layout is not None
        self._substep("Erasing %s and creating partitions" % self.cfg.disk)
        disk.wipe_signatures(self.ctx, self.layout)
        disk.apply_layout(self.ctx, self.layout)

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

    def _locale(self) -> None:
        system.configure(self.ctx, self.cfg, self.hw, self.layout)  # type: ignore[arg-type]

    def _users(self) -> None:
        system.accounts(self.ctx, self.cfg)

    def _initramfs(self) -> None:
        self._substep("Running mkinitcpio for the installed kernel(s)")
        system.build_initramfs(self.ctx)

    def _bootloader(self) -> None:
        assert self.layout is not None
        bootloader.install(self.ctx, self.cfg, self.layout)

    def _services(self) -> None:
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
            ("users", self._users),
            ("initramfs", self._initramfs),
            ("bootloader", self._bootloader),
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
