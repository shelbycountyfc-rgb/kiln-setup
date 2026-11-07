"""Answer files: the same installation, repeated on another machine.

A JSON file holds the answers the window collects; ``InstallConfig.from_dict``
reads the same keys.  ``python3 -m installer --answers file.json`` runs the whole
pipeline without a window, which is what makes an installation repeatable, and
what lets a real install run in CI instead of only the simulated one.
"""

from __future__ import annotations

import json
import os
import stat

from . import adapt, hardware
from .config import InstallConfig

#: Keys a file has to carry: without them the pipeline cannot start.
REQUIRED = ("disk", "rootPassword")


def read(path: str) -> dict:
    """The answers in a file, as a mapping."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as error:
        raise ValueError("cannot read %s: %s" % (path, error))
    except ValueError as error:
        raise ValueError("%s is not valid JSON: %s" % (path, error))
    if not isinstance(data, dict):
        raise ValueError("%s has to hold one JSON object" % path)
    if isinstance(data.get("answers"), dict):
        data = data["answers"]
    return data


def load(path: str, disk_size: int = 0) -> InstallConfig:
    """A validated configuration from a file, or ``ValueError`` saying why not."""
    data = read(path)
    missing = [key for key in REQUIRED if not str(data.get(key, "")).strip()]
    if missing:
        raise ValueError("%s does not answer %s" % (path, ", ".join(missing)))
    config = InstallConfig.from_dict(data)
    problems = config.validate(disk_size=disk_size)
    if problems:
        raise ValueError(
            "%s cannot be installed:\n  %s" % (path, "\n  ".join(problems))
        )
    return config


def example(hw=None) -> dict:
    """A complete starting point for this machine, without the passwords."""
    if hw is None:
        hw = hardware.simulate()
    recommendation = adapt.recommend(hw, live={})
    chosen = recommendation.values
    disks = hw.installable_disks() or list(hw.disks)
    target = max(disks, key=lambda item: item.size).device if disks else "/dev/sda"
    return {
        "disk": target,
        "mode": "erase",
        "confirmErase": True,
        "efi": chosen["efi"],
        "bootloader": chosen["bootloader"],
        "rootFs": "ext4",
        "swap": True,
        "swapFile": False,
        "swapGb": chosen["swapGb"],
        "espGb": 1.0,
        "separateHome": False,
        "encryption": "none",
        "luksPassphrase": "",
        "volumes": "partitions",
        "raid": "none",
        "arch": "x86_64",
        "hostname": "archlinux",
        "timezone": chosen["timezone"],
        "locale": chosen["locale"],
        "keymap": chosen["keymap"],
        "desktop": chosen["desktop"],
        "kernel": chosen["kernel"],
        "microcode": chosen["microcode"],
        "firmware": chosen["firmware"],
        "multilib": True,
        "packages": list(recommendation.packages),
        "rootPassword": "",
        "user": {
            "name": "arch",
            "password": "",
            "groups": ["wheel", "audio", "video"],
        },
    }


def write(path: str, hw=None) -> None:
    """Write the example answers, readable only by their owner."""
    data = example(hw)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def secret_warning(path: str) -> str:
    """A sentence about the file's permissions, or ``""`` when it is private."""
    if os.name == "nt":
        # Windows has no group/other permission bits to read here.
        return ""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return ""
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        return (
            "%s can be read by other users and holds the root password: "
            "chmod 600 %s" % (path, path)
        )
    return ""
