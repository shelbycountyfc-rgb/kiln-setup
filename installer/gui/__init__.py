"""The installer window (native Tk, no browser and no local server)."""

from __future__ import annotations


def run(simulate: bool = False, dry_run: bool = False, kiosk: bool = False) -> int:
    from .app import Installer

    installer = Installer(simulate=simulate, dry_run=dry_run, kiosk=kiosk)
    installer.mainloop()
    return 0
