"""System facts and privileged actions for the control panel.

The panel runs as a normal user.  Anything that changes the system goes through
:meth:`PanelContext.elevated`, which uses ``pkexec`` (the polkit prompt a desktop
shows) and falls back to ``sudo -n``.  Commands are argument lists, never shell
strings, and the package names they carry come from the catalogue or from
pacman's own output.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import time

from installer import catalog  # noqa: F401  (re-exported for the pages)
from installer.context import Context, Logger, human_bytes, which  # noqa: F401

PACMAN_CONF = "/etc/pacman.conf"


class PanelContext(Context):
    """A Context whose privileged actions ask for authorisation."""

    def elevator(self) -> list:
        if which("pkexec"):
            return ["pkexec"]
        if which("sudo"):
            return ["sudo", "-n"]
        return []

    def can_elevate(self) -> bool:
        return bool(self.elevator())

    def elevated(self, cmd, **kwargs):
        prefix = self.elevator()
        if not prefix:
            raise RuntimeError(
                "Neither pkexec nor sudo is available; install polkit or sudo to change "
                "the system from the panel."
            )
        return self.run(prefix + [str(part) for part in cmd], **kwargs)

    def elevated_write(self, path: str, text: str, **kwargs):
        """Replace a root-owned file with privileged ``tee`` input."""
        return self.elevated(["tee", path], input_text=text, capture=True, **kwargs)


# --------------------------------------------------------------------------- #
# Read-only facts
# --------------------------------------------------------------------------- #
def os_release() -> dict:
    data = {}
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "=" in line:
                    key, _, value = line.strip().partition("=")
                    data[key] = value.strip('"')
    except OSError:
        pass
    return data


def kernel() -> str:
    return platform.release()


def hostname() -> str:
    return platform.node() or "archlinux"


def uptime_text() -> str:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return "unknown"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return "%d days, %d hours" % (days, hours)
    if hours:
        return "%d hours, %d minutes" % (hours, minutes)
    return "%d minutes" % minutes


def memory() -> tuple:
    """``(total, available)`` bytes."""
    total = available = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return total, available


def disk_usage(path: str = "/") -> tuple:
    """``(total, used, free)`` bytes; portable, so the panel also runs elsewhere."""
    try:
        stats = os.statvfs(path)
        total = stats.f_blocks * stats.f_frsize
        free = stats.f_bavail * stats.f_frsize
        return (total, total - free, free)
    except (AttributeError, OSError):
        pass
    try:
        usage = shutil.disk_usage(path)
        return (usage.total, usage.used, usage.free)
    except OSError:
        return (0, 0, 0)


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
    except OSError:
        pass
    return platform.processor() or "Unknown CPU"


def updates(ctx: Context) -> list:
    """Packages with a newer version in the configured repositories."""
    out = ""
    try:
        out = ctx.captured(["pacman", "-Qu"], quiet=True)
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            rows.append((parts[0], parts[1], parts[3]))
    return rows


def installed_packages(ctx: Context) -> set:
    """Names of the packages installed on this machine.

    The panel runs on the installed system, so this is a plain ``pacman -Qq``.
    The installer's ``installed_package_names`` helper chroots into /mnt and is
    the wrong tool here.
    """
    try:
        out = ctx.captured(["pacman", "-Qq"], quiet=True)
    except Exception:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def installed_count(ctx: Context) -> int:
    return len(installed_packages(ctx))


def multilib_enabled() -> bool:
    try:
        with open(PACMAN_CONF, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False
    return bool(re.search(r"^\[multilib\]", text, re.M))


def services(ctx: Context) -> list:
    """``[(unit, active, enabled)]`` for every installed service unit."""
    active = {}
    enabled = {}
    try:
        for line in ctx.captured(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"], quiet=True).splitlines():
            parts = line.split()
            if parts:
                active[parts[0]] = parts[2] if len(parts) > 2 else "unknown"
        for line in ctx.captured(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--plain"], quiet=True).splitlines():
            parts = line.split()
            if len(parts) >= 2:
                enabled[parts[0]] = parts[1]
    except Exception:
        pass
    rows = []
    for unit in sorted(set(active) | set(enabled)):
        if unit.endswith(".service"):
            rows.append((unit, active.get(unit, "inactive"), enabled.get(unit, "unknown")))
    return rows


def users() -> list:
    """``[(name, uid, groups)]`` for the human accounts on this machine."""
    groups_by_user: dict = {}
    try:
        with open("/etc/group", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.strip().split(":")
                if len(fields) >= 4 and fields[3]:
                    for member in fields[3].split(","):
                        groups_by_user.setdefault(member, []).append(fields[0])
    except OSError:
        pass
    out = []
    try:
        with open("/etc/passwd", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.strip().split(":")
                if len(fields) < 7:
                    continue
                name, _pw, uid, _gid, _gecos, home, shell = fields[:7]
                if not uid.isdigit() or int(uid) < 1000 or int(uid) >= 65534:
                    continue
                out.append((name, int(uid), sorted(groups_by_user.get(name, [])), home, shell))
    except OSError:
        pass
    return out


def interfaces(ctx: Context) -> list:
    out = ""
    try:
        out = ctx.captured(["ip", "-br", "addr"], quiet=True)
    except Exception:
        return []
    return [line for line in out.splitlines() if line.strip()]


def active_connections(ctx: Context) -> list:
    out = ""
    try:
        out = ctx.captured(["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"], quiet=True)
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def failed_units(ctx: Context) -> list:
    out = ""
    try:
        out = ctx.captured(["systemctl", "--failed", "--no-legend", "--plain"], quiet=True)
    except Exception:
        return []
    return [line.split()[0] for line in out.splitlines() if line.split()]


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
