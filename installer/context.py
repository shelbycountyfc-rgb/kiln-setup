"""Logging, process execution and target-filesystem helpers.

Everything that touches the host machine goes through :class:`Context`, so a
whole installation can be replayed with ``dry_run`` (used by ``--simulate``, by
``--dry-run`` and by the self-test) without a live Arch medium.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

MAX_LOG_LINES = 6000

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|secret|token|psk)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bchpasswd\b.*$"),
    re.compile(r"(?i)--password(-file)?[ =]\S+"),
)


def redact(text: str) -> str:
    """Remove anything that looks like a credential before it reaches a log."""
    out = str(text)
    out = _SECRET_PATTERNS[0].sub(lambda m: m.group(1) + "=<redacted>", out)
    out = _SECRET_PATTERNS[1].sub("chpasswd <redacted>", out)
    out = _SECRET_PATTERNS[2].sub("--password=<redacted>", out)
    return out


class Logger:
    """Thread-safe line logger with an in-memory tail for the GUI."""

    def __init__(self, path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self.path = path
        if path:
            try:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("")
            except OSError:
                self.path = None

    def log(self, message: str, level: str = "info") -> None:
        stamp = time.strftime("%H:%M:%S")
        line = "%s [%s] %s" % (stamp, level, redact(str(message)))
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > MAX_LOG_LINES:
                del self._lines[: len(self._lines) - MAX_LOG_LINES]
            path = self.path
        print(line, flush=True)
        if path:
            try:
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    def tail(self, count: int = 250) -> list[str]:
        with self._lock:
            return self._lines[-count:]

    def clear(self) -> None:
        with self._lock:
            self._lines = []


@dataclass
class Result:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(RuntimeError):
    def __init__(self, result: Result) -> None:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = "\n".join(detail[-12:])
        super().__init__(
            "command failed (%d): %s%s"
            % (
                result.returncode,
                " ".join(shlex.quote(a) for a in result.args),
                ("\n" + tail) if tail else "",
            )
        )
        self.result = result


def which(name: str) -> str | None:
    return shutil.which(name)


class Context:
    """Carries the logger, the dry-run switch and the target root."""

    def __init__(self, log: Logger | None = None, dry_run: bool = False, target: str = "/mnt", hardware=None) -> None:
        self.log = log or Logger()
        self.dry_run = dry_run
        self.target = target
        self.hardware = hardware
        self._mounted: list[str] = []

    # -- process execution -------------------------------------------------
    def run(
        self,
        cmd,
        *,
        check: bool = True,
        capture: bool = False,
        stream: bool = False,
        input_text: str | None = None,
        env: dict | None = None,
        cwd: str | None = None,
        quiet: bool = False,
    ) -> Result:
        args = [str(part) for part in cmd]
        printable = " ".join(shlex.quote(a) for a in args)
        if not quiet:
            self.log.log("$ " + printable, "cmd")

        if self.dry_run:
            return Result(args, 0, "", "", dry_run=True)

        run_env = dict(os.environ)
        if env:
            run_env.update(env)

        try:
            if stream or not capture:
                proc = subprocess.Popen(
                    args,
                    stdin=subprocess.PIPE if input_text is not None else None,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=run_env,
                    cwd=cwd,
                    bufsize=1,
                )
                chunks: list[str] = []
                if input_text is not None and proc.stdin is not None:
                    try:
                        proc.stdin.write(input_text)
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    chunks.append(line)
                    if not quiet or stream:
                        self.log.log("  " + line, "out")
                proc.wait()
                result = Result(args, proc.returncode, "\n".join(chunks), "")
            else:
                completed = subprocess.run(
                    args,
                    input=input_text,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=run_env,
                    cwd=cwd,
                    check=False,
                )
                result = Result(args, completed.returncode, completed.stdout or "", completed.stderr or "")
                if not quiet and result.stderr.strip():
                    self.log.log("  " + result.stderr.strip(), "err")
        except FileNotFoundError:
            result = Result(args, 127, "", "executable not found: %s" % args[0])
        except OSError as error:  # pragma: no cover - defensive
            result = Result(args, 126, "", str(error))

        if check and result.returncode != 0:
            raise CommandError(result)
        return result

    def shell(self, command: str, **kwargs) -> Result:
        """Run a shell snippet on the host (pipelines only)."""
        return self.run(["bash", "-eo", "pipefail", "-c", command], **kwargs)

    def chroot(self, command: str, **kwargs) -> Result:
        """Run a command inside the target system.

        ``arch-chroot`` from arch-install-scripts is preferred: it bind-mounts
        /dev, /proc and /sys and copies the resolver config, which pacman and
        systemctl need.  A plain chroot is the fallback for a non-Arch host.
        """
        if which("arch-chroot"):
            return self.run(["arch-chroot", self.target, "/bin/bash", "-c", command], **kwargs)
        return self.run(["chroot", self.target, "/bin/bash", "-c", command], **kwargs)

    def chroot_capture(self, command: str, **kwargs) -> str:
        kwargs.setdefault("capture", True)
        result = self.chroot(command, **kwargs)
        return result.stdout.strip() if result else ""

    def captured(self, cmd, **kwargs) -> str:
        kwargs.setdefault("capture", True)
        result = self.run(cmd, **kwargs)
        return result.stdout.strip() if result else ""

    # -- files -------------------------------------------------------------
    def target_path(self, relpath: str) -> str:
        return os.path.join(self.target, relpath.lstrip("/"))

    def make_dir(self, path: str) -> str:
        """Create a directory on the target, unless this is a dry run."""
        if not self.dry_run:
            os.makedirs(path, exist_ok=True)
        return path

    def write_target(self, relpath: str, content: str, mode: int = 0o644) -> str:
        path = self.target_path(relpath)
        self.log.log("write %s" % path, "file")
        if self.dry_run:
            return path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(path, mode)
        return path

    def append_target(self, relpath: str, content: str) -> str:
        path = self.target_path(relpath)
        if self.dry_run:
            return path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def read_target(self, relpath: str) -> str:
        path = self.target_path(relpath)
        if self.dry_run:
            return ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    # -- mounts ------------------------------------------------------------
    def mount(self, device: str, mountpoint: str, *, options: str | None = None, fstype: str | None = None) -> None:
        args = ["mount"]
        if fstype:
            args += ["-t", fstype]
        if options:
            args += ["-o", options]
        args += [device, mountpoint]
        self.run(args)
        self._mounted.append(mountpoint)

    def umount_all(self) -> None:
        for mountpoint in reversed(self._mounted):
            self.run(["umount", "-R", mountpoint], check=False)
        self._mounted.clear()


def human_bytes(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return "%d %s" % (int(size), unit)
            return "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%.1f PiB" % size


def parse_size(text: str) -> int:
    """Parse ``20G``/``512M`` into bytes."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B?\s*$", str(text), re.I)
    if not match:
        raise ValueError("cannot parse size: %r" % text)
    amount = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    factor = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[unit]
    return int(amount * factor)


def uuid_of(ctx: Context, device: str, kind: str = "UUID") -> str:
    """Read a filesystem UUID/PARTUUID, or a stable placeholder in dry run."""
    if ctx.dry_run:
        return "00000000-0000-0000-0000-%012x" % (abs(hash(device)) % (16 ** 12))
    try:
        out = ctx.captured(["blkid", "-s", kind, "-o", "value", device])
    except CommandError:
        out = ""
    return out.splitlines()[0].strip() if out else ""


def part_path(disk: str, number: int) -> str:
    """``/dev/nvme0n1`` + 1 -> ``/dev/nvme0n1p1``; ``/dev/sda`` + 1 -> ``/dev/sda1``."""
    disk = disk.rstrip("/")
    if re.search(r"(nvme\d+n\d+|mmcblk\d+|loop\d+|nbd\d+|md\d+)$", disk):
        return "%sp%d" % (disk, number)
    if re.search(r"p\d+$", disk):
        return "%sp%d" % (re.sub(r"p\d+$", "", disk), number)
    return "%s%d" % (disk, number)
