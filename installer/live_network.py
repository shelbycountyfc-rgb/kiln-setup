"""Connecting the live medium itself to the network.

Every package is downloaded from the Arch mirrors, so a machine with only a
wireless adapter has to be associated *before* pacstrap runs.  NetworkManager
ships on the medium and does the association, the DHCP lease and the resolver,
so this module is a thin, testable wrapper around nmcli.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .context import Context, which

WIFI = "wifi"


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int
    security: str

    @property
    def is_open(self) -> bool:
        return self.security in ("", "--", "open")

    @property
    def label(self) -> str:
        kind = "open" if self.is_open else self.security
        return "%s   %d%%  (%s)" % (self.ssid, self.signal, kind)


def parse_devices(text: str) -> list:
    """Wireless interfaces from ``nmcli -t -f DEVICE,TYPE device status``."""
    names = []
    for line in str(text).splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1].strip() == WIFI and parts[0] not in names:
            names.append(parts[0])
    return names


def parse_scan(text: str) -> list:
    """Networks from ``nmcli -t -f SSID,SIGNAL,SECURITY device wifi list``."""
    best = {}
    for line in str(text).splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[1].strip())
        except ValueError:
            signal = 0
        found = Network(ssid, signal, parts[2].strip() or "open")
        if ssid not in best or found.signal > best[ssid].signal:
            best[ssid] = found
    return sorted(best.values(), key=lambda item: (-item.signal, item.ssid))


def parse_connected(text: str) -> str:
    """The first connected interface from ``nmcli -t -f DEVICE,TYPE,STATE``."""
    for line in str(text).splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[2].strip().startswith("connected"):
            return parts[0]
    return ""


class LiveNetwork:
    """NetworkManager on the machine the installer is running on."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    @property
    def usable(self) -> bool:
        return not self.ctx.dry_run and which("nmcli") is not None

    def _capture(self, args) -> str:
        return self.ctx.run(args, capture=True, check=False, quiet=True).stdout

    def wifi_devices(self) -> list:
        return parse_devices(self._capture(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]))

    def connected_device(self) -> str:
        return parse_connected(self._capture(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]))

    def scan(self, device: str, settle: float = 6.0) -> list:
        self.ctx.run(["nmcli", "device", "wifi", "rescan", "ifname", device], check=False, quiet=True)
        deadline = time.time() + settle
        found = []
        while time.time() < deadline:
            found = parse_scan(
                self._capture(
                    ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", device]
                )
            )
            if found:
                break
            time.sleep(1.0)
        return found

    def connect(self, device: str, ssid: str, password: str = "", wait: float = 30.0):
        """Associate and wait for an address.  Returns ``(ok, message)``."""
        args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", device]
        if password:
            args += ["password", password]
        # Run without echoing: the passphrase is an argument, and a logged
        # command line would put it in the installer log.
        result = self.ctx.run(args, capture=True, check=False, quiet=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            return False, (detail[-1] if detail else "nmcli refused the connection")
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.online():
                return True, ""
            time.sleep(1.0)
        return False, "the network accepted the key but no address came back"

    def online(self) -> bool:
        if self.ctx.dry_run:
            return True
        return bool(self._capture(["ip", "-4", "route", "show", "default"]).strip())
