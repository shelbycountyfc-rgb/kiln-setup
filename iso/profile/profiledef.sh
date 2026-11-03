#!/usr/bin/env bash
# Profile definition for mkarchiso.
#
# The medium boots on UEFI through systemd-boot and on legacy BIOS through
# syslinux, and starts the graphical installer on tty1 through an autologin.

iso_name="arch-gui-installer"
iso_label="ARCH_GUI"
iso_publisher="Arch GUI Installer <https://archlinux.org>"
iso_application="Arch Linux GUI installer"
iso_version="$(date +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=('uefi.systemd-boot' 'bios.syslinux')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/etc/gshadow"]="0:0:400"
  ["/root"]="0:0:750"
  ["/root/.bash_profile"]="0:0:644"
  ["/root/.xinitrc"]="0:0:644"
  ["/usr/local/bin/arch-installer-gui"]="0:0:755"
  ["/usr/local/bin/arch-installer-session"]="0:0:755"
)
