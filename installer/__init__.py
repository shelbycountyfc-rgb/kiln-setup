"""Kiln Setup: an unofficial graphical installer for Arch Linux.

A Windows-setup-style wizard that installs Arch Linux from a bootable live
medium, following the steps of the official installation guide
(https://wiki.archlinux.org/title/Installation_guide): partition, format,
mount, pacstrap, fstab, chroot configuration and boot loader.

The window and the engine are one process.  Nothing listens on a port.

Not affiliated with, endorsed by, or supported by the Arch Linux project; the
name and the disclaimer live in :mod:`installer.brand`.
"""

from . import brand

__version__ = "1.2.4"

APP_NAME = brand.PRODUCT_TITLE
