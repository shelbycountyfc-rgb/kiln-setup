"""Network configuration written into the new system.

The live medium uses NetworkManager, and the installed system does too, so the
only thing that has to be handed over is the Wi-Fi profile the user typed in.
The connection is stored where NetworkManager looks for it, with the 0600 mode
it insists on.
"""

from __future__ import annotations

from .config import InstallConfig
from .context import Context


def _safe_name(ssid: str) -> str:
    keep = "".join(char if char.isalnum() or char in "-_." else "_" for char in ssid)
    return (keep.strip("._") or "wifi")[:48]


def profile_text(cfg: InstallConfig) -> str:
    """A NetworkManager keyfile for the network on the Network page."""
    return wifi_profile(cfg.wifi_ssid, cfg.wifi_password)


def wifi_profile(ssid: str, password: str) -> str:
    """A NetworkManager keyfile for one network; shared with the live medium."""
    lines = [
        "[connection]",
        "id=%s" % ssid,
        "uuid=%s" % _uuid(ssid),
        "type=wifi",
        "autoconnect=true",
        "",
        "[wifi]",
        "mode=infrastructure",
        "ssid=%s" % ssid,
    ]
    if password:
        lines += [
            "",
            "[wifi-security]",
            "key-mgmt=wpa-psk",
            "psk=%s" % password,
        ]
    lines += ["", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def _uuid(seed: str) -> str:
    """A stable UUID for the profile, derived from the network name."""
    import hashlib

    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return "%s-%s-%s-%s-%s" % (
        digest[0:8],
        digest[8:12],
        digest[12:16],
        digest[16:20],
        digest[20:32],
    )


def write_profiles(ctx: Context, cfg: InstallConfig) -> None:
    if not cfg.wifi_ssid:
        return
    name = _safe_name(cfg.wifi_ssid) + ".nmconnection"
    ctx.write_target(
        "etc/NetworkManager/system-connections/%s" % name,
        profile_text(cfg),
        mode=0o600,
    )
    ctx.log.log("saved the Wi-Fi profile for %s" % cfg.wifi_ssid, "step")


def disable_mac_randomisation(ctx: Context) -> None:
    """A predictable MAC is friendlier to home routers than a random one."""
    ctx.write_target(
        "etc/NetworkManager/conf.d/10-mac.conf",
        "[device]\nwifi.scan-rand-mac-address=no\n",
    )
