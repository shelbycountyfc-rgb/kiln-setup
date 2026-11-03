"""Arch Linux graphical installer.

A Windows-setup-style wizard that installs Arch Linux from a bootable live
medium, following the steps of the official installation guide
(https://wiki.archlinux.org/title/Installation_guide): partition, format,
mount, pacstrap, fstab, chroot configuration and boot loader.

The window and the engine are one process.  Nothing listens on a port.
"""

__version__ = "1.0.0"

APP_NAME = "Arch Linux Installer"
