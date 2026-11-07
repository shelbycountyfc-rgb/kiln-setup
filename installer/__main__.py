"""Entry point: ``python3 -m installer``."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__, brand

GIB = 1024 ** 3


def _check() -> int:
    """Print what the installer detects and the layout it would create."""
    from . import adapt, disk, hardware, packages
    from .config import InstallConfig
    from .context import Context, Logger, human_bytes

    ctx = Context(Logger(None), dry_run=True)
    hw = hardware.detect(ctx)
    print("Computer : %s (%d cores, %d threads)" % (hw.cpu_model, hw.cpu_cores, hw.cpu_threads))
    print("Memory   : %s" % human_bytes(hw.ram_bytes))
    print("Firmware : %s%s" % (hw.firmware.upper(), ", Secure Boot enabled" if hw.secure_boot else ""))
    print("Microcode: %s" % (hw.microcode or "none detected"))
    for gpu in hw.gpus:
        info = gpu.to_dict()
        print("Graphics : %s %s" % (info["vendor"], info["device"]))
    print("Network  : %s" % (", ".join(hw.network) or "none"))
    print("Form     : %s" % hw.form_factor)
    chosen = adapt.recommend(hw, live={})
    print("Chosen   : desktop=%s kernel=%s swap=%s GB" % (
        chosen.values["desktop"], chosen.values["kernel"], chosen.values["swapGb"]))
    print("Extras   : %s" % (" ".join(chosen.packages) or "none"))
    if not hw.disks:
        print("Disks    : none found (is this a live medium?)")
        return 0
    for found in hw.disks:
        print(
            "Disk     : %s %s %s%s"
            % (
                found.device,
                found.to_dict()["sizeText"],
                found.model or found.name,
                " (removable)" if found.removable else "",
            )
        )
    target = max(hw.internal_disks() or hw.disks, key=lambda item: item.size)
    plan = disk.plan_layout(
        target.device,
        target.size,
        efi=hw.firmware == "uefi",
        root_fs="ext4",
        swap=True,
        esp_gb=1.0,
        ram_bytes=hw.ram_bytes,
    )
    print("Layout   :")
    for name, role, fs, size, mountpoint in plan.summary():
        print("           %-14s %-6s %-6s %-10s %s" % (name, role, fs, size, mountpoint))
    cfg = InstallConfig(disk=target.device, efi=hw.firmware == "uefi", confirm_erase=True)
    print("Packages : %s" % " ".join(packages.required_packages(cfg, hw)))
    print("Firmware : %s (%s)" % (" ".join(packages.firmware_packages(cfg, hw)), cfg.firmware))
    return 0


def _selftest() -> int:
    """Run the whole pipeline with a simulated machine and every command dry.

    This proves the phase wiring, the package lists and the validation, without
    partitioning anything.  It is what can be checked on a non-Arch host.
    """
    from . import hardware
    from .config import InstallConfig
    from .orchestrator import Session

    hw = hardware.simulate()
    cfg = InstallConfig.from_dict(
        {
            "disk": hw.disks[0].device,
            "efi": True,
            "confirmErase": True,
            "rootFs": "ext4",
            "desktop": "kde",
            "kernel": "linux",
            "multilib": True,
            "bootloader": "auto",
            "hostname": "archbox",
            "timezone": "Europe/Berlin",
            "locale": "en_US.UTF-8",
            "keymap": "de",
            "rootPassword": "correcthorse1",
            "user": {"name": "ada", "password": "correcthorse2", "groups": ["wheel", "audio"]},
            "packages": ["firefox", "steam", "lib32-mesa"],
        }
    )
    problems = cfg.validate(disk_size=hw.disks[0].size)
    if problems:
        print("SELFTEST FAIL: validation rejected the sample request: %s" % problems)
        return 1
    session = Session(hw, cfg, dry_run=True, log_path=None)
    session.run()
    print("phases  : %s" % ", ".join("%s=%s" % (pid, session.state[pid]) for pid in session.state))
    print("percent : %d" % session.percent)
    if session.error:
        print("SELFTEST FAIL: %s" % session.error)
        return 1
    unfinished = [pid for pid, state in session.state.items() if state != "done"]
    if unfinished:
        print("SELFTEST FAIL: phases not done: %s" % ", ".join(unfinished))
        return 1
    print("SELFTEST PASS: %d phases, %d packages, no command executed" % (len(session.state), len(session.package_list)))
    return 0


def _unattended(path: str, dry_run: bool) -> int:
    """Install from an answer file: the same pipeline, no window."""
    from . import answers, hardware
    from .context import Context, Logger
    from .orchestrator import Session

    hw = hardware.detect(Context(Logger(None), dry_run=True))
    if not dry_run:
        warning = answers.secret_warning(path)
        if warning:
            print("REFUSED: %s" % warning)
            return 2
    try:
        data = answers.read(path)
        device = str(data.get("disk", "")).strip()
        found = next((item for item in hw.disks if item.device == device), None)
        config = answers.load(path, disk_size=found.size if found else 0)
    except ValueError as error:
        print("ANSWERS REJECTED: %s" % error)
        return 2
    session = Session(hw, config, dry_run=dry_run)
    session.run()
    for phase in session.status()["phases"]:
        print("%-10s %s" % (phase["state"], phase["label"]))
    if session.error:
        print("INSTALL FAILED: %s" % session.error)
        if session.note:
            print(session.note)
        return 1
    print("INSTALL COMPLETE: %s" % config.disk)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="kiln-setup",
        description=(
            "%s: %s, following the official installation guide."
            % (APP_NAME, brand.TAGLINE)
        ),
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="use a simulated laptop instead of the real hardware (enables dry run)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="log every command without executing it; no disk is touched",
    )
    parser.add_argument(
        "--kiosk", action="store_true",
        help="fill the screen and take the input focus (used by the live medium)",
    )
    parser.add_argument("--check", action="store_true", help="print the detected hardware and layout, then exit")
    parser.add_argument("--selftest", action="store_true", help="run the pipeline against simulated hardware, dry")
    parser.add_argument(
        "--answers", metavar="FILE",
        help="install from a JSON answer file instead of the window",
    )
    parser.add_argument(
        "--write-answers", metavar="FILE", nargs="?", const="kiln-answers.json",
        help="write a starting point for --answers on this machine, then exit",
    )
    parser.add_argument("--version", action="version", version="kiln-setup " + __version__)
    args = parser.parse_args(argv)

    if args.write_answers:
        from . import answers, hardware
        from .context import Context, Logger

        hw = hardware.detect(Context(Logger(None), dry_run=True))
        answers.write(args.write_answers, hw)
        print(
            "wrote %s; fill in rootPassword and the user password, then run "
            "--answers %s" % (args.write_answers, args.write_answers)
        )
        return 0
    if args.answers:
        return _unattended(args.answers, args.dry_run)
    if args.check:
        return _check()
    if args.selftest:
        return _selftest()

    from .gui import run

    return run(simulate=args.simulate, dry_run=args.dry_run, kiosk=args.kiosk)


if __name__ == "__main__":
    sys.exit(main())
