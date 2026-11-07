#!/usr/bin/env bash
# Profile definition for mkarchiso.
#
# The medium boots on UEFI through systemd-boot and on legacy BIOS through
# syslinux, and starts the graphical installer on tty1 through an autologin.

iso_name="kiln-setup"
iso_label="KILN_SETUP"
iso_publisher="Kiln Setup, unofficial <https://github.com/shelbycountyfc-rgb/kiln-setup>"
iso_application="Kiln Setup - an unofficial installer for Arch Linux"
# A revision suffix lets a corrected medium be built beside the previous one,
# which matters when the older image is still open on the build machine.
iso_version="$(date +%Y.%m.%d)${ISO_REVISION:+-$ISO_REVISION}"
install_dir="arch"
buildmodes=('iso')
# Explicit modes.  'uefi.systemd-boot' alone builds the ESP partition but
# registers no EFI El Torito image, so firmware that only reads the boot
# catalogue (VirtualBox with EFI on, a UEFI CD) reports no bootable medium.
bootmodes=('uefi-x64.systemd-boot.esp' 'uefi-x64.systemd-boot.eltorito' 'bios.syslinux')
arch="${ISO_ARCH:-x86_64}"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/etc/gshadow"]="0:0:400"
  ["/root"]="0:0:750"
  ["/root/.bash_profile"]="0:0:644"
  ["/root/.xinitrc"]="0:0:644"
  ["/usr/local/bin/kiln-setup"]="0:0:755"
  ["/usr/local/bin/kiln-setup-session"]="0:0:755"
)
