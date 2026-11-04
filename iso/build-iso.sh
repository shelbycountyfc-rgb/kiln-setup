#!/usr/bin/env bash
# Build a bootable Arch Linux GUI installer ISO.
#
# The image is a normal archiso image: the live root is a squashfs, the kernel
# and initramfs are written by mkinitcpio's archiso hook, and the medium boots
# on UEFI (systemd-boot) and legacy BIOS (syslinux).
#
# What makes it an installer:
#   /opt/arch-installer          this repository's Python package
#   /usr/local/bin/arch-installer-gui
#   /usr/local/bin/arch-installer-session
#   /etc/systemd/system/getty@tty1.service.d/autologin.conf
#   /root/.bash_profile          starts the session on tty1
#
# On a real Arch host:
#   sudo pacman -S --needed archiso
#   sudo ./iso/build-iso.sh
#
# Needs root (mkarchiso mounts a loop device and builds in a chroot) and about
# 6 GiB of free space. Any Linux host with Docker can use build-in-docker.sh.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

OUT_DIR="$REPO_ROOT"
WORK_DIR=""
PROFILE_SRC="$SCRIPT_DIR/profile"
KEEP=0
JOBS="$(nproc 2>/dev/null || echo 2)"

log()  { printf '\033[1;34m[iso]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[iso]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[iso] error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: build-iso.sh [options]

  --out <dir>    directory the image is written to (default: the repo root)
  --work <dir>   build directory (default: mktemp -d)
  --keep         keep the work directory for debugging
  --jobs <n>     parallel compression jobs (default: number of CPUs)
  -h, --help     this message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_DIR="$2"; shift 2 ;;
    --work) WORK_DIR="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --jobs) JOBS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

cleanup() {
  local status=$?
  if [[ $KEEP -eq 0 && -n "$WORK_DIR" && $status -eq 0 ]]; then
    rm -rf "$WORK_DIR"
  elif [[ -n "$WORK_DIR" ]]; then
    warn "work directory kept: $WORK_DIR"
  fi
  exit $status
}
trap cleanup EXIT INT TERM

[[ "$(id -u)" -eq 0 ]] || die "mkarchiso needs root (it mounts loop devices)"
command -v mkarchiso >/dev/null 2>&1 \
  || die "mkarchiso is missing: install the 'archiso' package (pacman -S archiso)"
# mkarchiso only warns when mmd/mcopy are missing and then drops the UEFI boot
# mode, leaving a BIOS-only image that a UEFI machine cannot start at all.
for tool in mmd mcopy; do
  command -v "$tool" >/dev/null 2>&1 \
    || die "$tool is missing: install mtools, or the image gets no UEFI boot entry"
done

mkdir -p "$OUT_DIR"
[[ -z "$WORK_DIR" ]] && WORK_DIR="$(mktemp -d /tmp/arch-gui-installer.XXXXXX)"
mkdir -p "$WORK_DIR"

# The profile is copied: mkarchiso fills airootfs and writes boot files, and a
# build must never leave generated files inside the git repository.
PROFILE="$WORK_DIR/profile"
log "copying the profile to $PROFILE"
cp -a "$PROFILE_SRC" "$PROFILE"

# mkarchiso validates the boot configuration of the profile before it builds:
# without efiboot/ and syslinux/ it aborts with "directory is missing".  The
# menu files are taken from the releng profile that ships with the installed
# archiso, so they always match the mkarchiso doing the build instead of
# hard-coding placeholders that only one version understands.
seed_boot_configs() {
  local releng="/usr/share/archiso/configs/releng"
  local mode
  for mode in efiboot syslinux; do
    [[ -d "$PROFILE/$mode" ]] && continue
    [[ -d "$releng/$mode" ]] \
      || die "the profile has no $mode/ and $releng/$mode is missing; install archiso"
    log "seeding the $mode boot configuration from $releng"
    cp -a "$releng/$mode" "$PROFILE/$mode"
  done
  # Say what this medium is on the boot menu.
  { grep -rl 'Arch Linux install medium' "$PROFILE/efiboot" "$PROFILE/syslinux" 2>/dev/null || true; } \
    | while read -r menu; do
        sed -i 's/Arch Linux install medium/Arch Linux GUI installer/g' "$menu"
      done
  # Keep the kernel messages. With 'quiet' a boot that ends in the emergency
  # shell looks like a blank screen, and this medium has to be usable for
  # finding out what went wrong.
  { grep -rl 'quiet' "$PROFILE/efiboot" "$PROFILE/syslinux" 2>/dev/null || true; } \
    | while read -r menu; do
        sed -i 's/ quiet//g' "$menu"
      done
  [[ -d "$PROFILE/efiboot/loader/entries" ]] \
    || die "efiboot/loader/entries is missing from the profile"
  [[ -f "$PROFILE/syslinux/syslinux.cfg" ]] \
    || die "syslinux/syslinux.cfg is missing from the profile"
}

seed_boot_configs

# The live root is a squashfs that the initramfs has to find and mount.  That
# needs the archiso hooks, and it must not use "autodetect": autodetect writes an
# initramfs for the machine that built the image, so the medium would miss the
# storage and USB drivers of the PC it is booted on.  archiso ships both settings
# as a mkinitcpio drop-in; take it from the installed profile, so it always
# matches the archiso doing the build.
seed_initramfs_config() {
  local releng="/usr/share/archiso/configs/releng"
  local dropin_dir="$PROFILE/airootfs/etc/mkinitcpio.conf.d"
  local dropin="$dropin_dir/archiso.conf"
  if [[ ! -f "$dropin" ]]; then
    if [[ -f "$releng/airootfs/etc/mkinitcpio.conf.d/archiso.conf" ]]; then
      log "seeding the archiso mkinitcpio drop-in"
      mkdir -p "$dropin_dir"
      cp -a "$releng/airootfs/etc/mkinitcpio.conf.d/." "$dropin_dir/"
    elif [[ -f "$releng/airootfs/etc/mkinitcpio.conf" ]]; then
      # Older archiso: one complete configuration instead of a drop-in.
      log "seeding mkinitcpio.conf with the archiso hooks"
      mkdir -p "$PROFILE/airootfs/etc"
      cp -a "$releng/airootfs/etc/mkinitcpio.conf" "$PROFILE/airootfs/etc/mkinitcpio.conf"
    else
      die "archiso ships no mkinitcpio configuration; a bootable medium cannot be built"
    fi
  fi
  if ! grep -rqs 'archiso' "$dropin_dir" "$PROFILE/airootfs/etc/mkinitcpio.conf" 2>/dev/null; then
    die "no archiso hook in the mkinitcpio configuration: the medium could not mount its own root"
  fi
}
seed_initramfs_config

log "installing the Python installer into the live root"
mkdir -p "$PROFILE/airootfs/opt/arch-installer"
cp -a "$REPO_ROOT/installer" "$PROFILE/airootfs/opt/arch-installer/"
# The panel shares the installer's theme, catalogue and pacman helpers, and is
# copied into the installed system during the finish phase: it has to be on the
# medium even though it is not what the medium runs.
cp -a "$REPO_ROOT/panel" "$PROFILE/airootfs/opt/arch-installer/"
cp -a "$REPO_ROOT/README.md" "$PROFILE/airootfs/opt/arch-installer/"
find "$PROFILE/airootfs/opt/arch-installer" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
[[ -f "$PROFILE/airootfs/opt/arch-installer/installer/__main__.py" ]] \
  || die "the installer package did not get copied into the profile"
[[ -f "$PROFILE/airootfs/opt/arch-installer/panel/__main__.py" ]] \
  || die "the control panel did not get copied into the profile"

chmod 0755 \
  "$PROFILE/airootfs/usr/local/bin/arch-installer-session" \
  "$PROFILE/airootfs/usr/local/bin/arch-installer-gui"

# A syntax error in the window would only show up as a black screen on the
# finished medium, so the sources are compiled before the long build starts.
if command -v python3 >/dev/null 2>&1; then
  log "checking the installer sources"
  python3 -m compileall -q "$PROFILE/airootfs/opt/arch-installer/installer" \
    || die "the installer sources do not compile"
else
  warn "python3 is not installed on the build host; skipping the source check"
fi

# A live root whose /etc/machine-id says "uninitialized" counts as a first boot
# to systemd, so systemd-firstboot claims tty1 with its Initial Setup wizard and
# the installer never appears.  Write a real id and mask the unit outright: the
# console of this medium belongs to the installer whatever the build leaves.
log "masking systemd-firstboot on the live medium"
mkdir -p "$PROFILE/airootfs/etc/systemd/system"
ln -sf /dev/null "$PROFILE/airootfs/etc/systemd/system/systemd-firstboot.service"
printf '0123456789abcdef0123456789abcdef\n' > "$PROFILE/airootfs/etc/machine-id"

# reflector runs at boot and rewrites the mirrorlist.  On a machine that boots
# without a network it finishes with an empty list, and pacstrap then fails with
# "no servers configured for repository" -- a dead end for someone installing
# from Wi-Fi.  Ship working mirrors and keep reflector from replacing them; the
# installer can still optimise the list later.
log "shipping a fallback mirrorlist"
mkdir -p "$PROFILE/airootfs/etc/pacman.d"
cat > "$PROFILE/airootfs/etc/pacman.d/mirrorlist" <<'MIRRORS'
## Arch Linux repository mirrorlist
## Shipped by the installer medium so an installation can start without a
## network-generated list; reflector is masked and cannot empty it.
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch
Server = https://mirrors.kernel.org/archlinux/$repo/os/$arch
MIRRORS
ln -sf /dev/null "$PROFILE/airootfs/etc/systemd/system/reflector.service"
ln -sf /dev/null "$PROFILE/airootfs/etc/systemd/system/reflector.timer"

before="$(find "$OUT_DIR" -maxdepth 1 -name 'arch-gui-installer-*.iso' -printf '%f\n' 2>/dev/null | sort || true)"

log "running mkarchiso (this takes a while: the live root is downloaded and squashed)"
mkarchiso -v -w "$WORK_DIR/mkarchiso" -o "$OUT_DIR" -P "$JOBS" "$PROFILE"

# Newest by modification time, not by name: a revision suffix such as -r2 sorts
# before the plain name, and picking the wrong file checksums yesterday's image.
mapfile -t built < <(
  find "$OUT_DIR" -maxdepth 1 -name 'arch-gui-installer-*.iso' -printf '%T@ %f\n' 2>/dev/null \
    | sort -n | cut -d' ' -f2-
)
[[ ${#built[@]} -gt 0 ]] || die "mkarchiso finished but produced no image in $OUT_DIR"
IMAGE="$OUT_DIR/${built[-1]}"
[[ -s "$IMAGE" ]] || die "the produced image is empty: $IMAGE"

# Refuse to hand over an image without a kernel, an initramfs and the installer:
# all three are silent failures until someone boots the medium.
if command -v xorriso >/dev/null 2>&1; then
  log "checking the finished image"
  listing="$(xorriso -indev "$IMAGE" -find /arch/boot -maxdepth 2 -type f 2>/dev/null || true)"
  grep -q 'vmlinuz' <<<"$listing" || die "no kernel in the image"
  grep -q 'initramfs' <<<"$listing" || die "no initramfs in the image"
  # A missing boot entry is not cosmetic: a UEFI machine cannot start a BIOS-only
  # image, and a legacy machine cannot start a UEFI-only one.  Warn loudly rather
  # than fail, so a build that only needs the BIOS path is still usable.
  torito="$(xorriso -indev "$IMAGE" -report_el_torito plain 2>/dev/null || true)"
  grep -qi 'uefi' <<<"$torito" \
    || warn "the image has no UEFI boot entry: UEFI machines can only boot it through CSM"
  grep -qi 'bios' <<<"$torito" \
    || warn "the image has no BIOS boot entry: legacy machines cannot boot it"

  # The installer is inside the squashfs, not in the ISO directory tree: the
  # ISO only carries /arch/x86_64/airootfs.sfs and the boot files.  Check the
  # live root itself, which is what the medium actually runs.
  rootfs="$WORK_DIR/mkarchiso/iso/arch/x86_64/airootfs.sfs"
  [[ -f "$rootfs" ]] || rootfs="$(find "$WORK_DIR" -name 'airootfs.sfs' -print -quit 2>/dev/null || true)"
  if [[ -n "$rootfs" && -f "$rootfs" ]] && command -v unsquashfs >/dev/null 2>&1; then
    live_listing="$(unsquashfs -l "$rootfs" 2>/dev/null || true)"
    grep -q 'opt/arch-installer/installer/__main__.py' <<<"$live_listing" \
      || die "the installer is not inside the live root"
    grep -q 'usr/local/bin/arch-installer-gui' <<<"$live_listing" \
      || die "the installer launcher is not inside the live root"
    grep -q 'opt/arch-installer/panel/app.py' <<<"$live_listing" \
      || die "the control panel is not inside the live root"
    grep -q 'etc/systemd/system/getty@tty1.service.d/autologin.conf' <<<"$live_listing" \
      || die "the autologin that starts the installer at boot is missing"

    # Without the archiso hook the initramfs cannot mount the squashfs and the
    # medium stops in the emergency shell.  This is the check that catches it.
    initramfs="$(find "$WORK_DIR" -name 'initramfs-linux*.img' -print -quit 2>/dev/null || true)"
    if [[ -n "$initramfs" ]] && command -v lsinitcpio >/dev/null 2>&1; then
      lsinitcpio "$initramfs" 2>/dev/null | grep -q 'hooks/archiso' \
        || die "the initramfs has no archiso hook: the medium cannot mount its own root"
    else
      warn "the initramfs could not be inspected; archiso hook support is unproven"
    fi
  else
    warn "the live root could not be inspected; the image was not fully verified"
  fi
fi

log "done: $IMAGE ($(du -h "$IMAGE" | cut -f1))"
( cd "$(dirname "$IMAGE")" && sha256sum "$(basename "$IMAGE")" ) | tee "$IMAGE.sha256"

# One image of this installer is enough; an older one beside it is how a machine
# ends up booting last week's code.
shopt -s nullglob
for old in "$OUT_DIR"/arch-gui-installer-*.iso "$OUT_DIR"/arch-gui-installer-*.iso.sha256; do
  [[ "$(basename "$old")" == "$(basename "$IMAGE")" ]] && continue
  [[ "$(basename "$old")" == "$(basename "$IMAGE").sha256" ]] && continue
  # A previous image that is still open elsewhere (a VM, an antivirus scan)
  # cannot be deleted; that is no reason to fail a build that already produced
  # a verified image.
  if rm -f "$old" 2>/dev/null; then
    log "removed the previous image $(basename "$old")"
  else
    warn "could not remove $(basename "$old"); it is in use elsewhere"
  fi
done
shopt -u nullglob

cat <<EOF

Write it to a USB stick with:
  sudo dd if=$(basename "$IMAGE") of=/dev/sdX bs=4M status=progress oflag=sync

or boot it directly:
  qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd -cdrom $(basename "$IMAGE")
EOF
