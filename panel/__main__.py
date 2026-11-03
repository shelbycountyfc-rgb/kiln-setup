"""Entry point: ``python3 -m panel``."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__


def _check() -> int:
    from . import core
    from installer.context import Logger, human_bytes

    ctx = core.PanelContext(Logger(None))
    total, available = core.memory()
    disk_total, disk_used, disk_free = core.disk_usage("/")
    release = core.os_release()
    print("%s %s" % (APP_NAME, __version__))
    print("System   : %s" % release.get("PRETTY_NAME", "unknown"))
    print("Kernel   : %s" % core.kernel())
    print("Hostname : %s" % core.hostname())
    print("CPU      : %s" % core.cpu_model())
    print("Memory   : %s available of %s" % (human_bytes(available), human_bytes(total)))
    print("Storage  : %s free of %s" % (human_bytes(disk_free), human_bytes(disk_total)))
    print("Packages : %d installed" % core.installed_count(ctx))
    print("Multilib : %s" % ("enabled" if core.multilib_enabled() else "disabled"))
    print("Updates  : %d available" % len(core.updates(ctx)))
    print("Elevation: %s" % (" ".join(ctx.elevator()) or "none available"))
    for name, uid, groups, _home, _shell in core.users():
        print("User     : %s (uid %d) %s" % (name, uid, ",".join(groups)))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="arch-control-panel",
        description="%s: manage the installed Arch system (software, services, users, network)." % APP_NAME,
    )
    parser.add_argument("--kiosk", action="store_true", help="fill the screen")
    parser.add_argument("--check", action="store_true", help="print the system state and exit")
    parser.add_argument("--version", action="version", version="arch-control-panel " + __version__)
    args = parser.parse_args(argv)

    if args.check:
        return _check()

    from .app import Panel

    Panel(kiosk=args.kiosk).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
