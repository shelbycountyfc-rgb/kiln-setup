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

mkdir -p "$OUT_DIR"
[[ -z "$WORK_DIR" ]] && WORK_DIR="$(mktemp -d /tmp/arch-gui-installer.XXXXXX)"
mkdir -p "$WORK_DIR"

# The profile is copied: mkarchiso fills airootfs and writes boot files, and a
# build must never leave generated files inside the git repository.
PROFILE="$WORK_DIR/profile"
log "copying the profile to $PROFILE"
cp -a "$PROFILE_SRC" "$PROFILE"

log "installing the Python installer into the live root"
mkdir -p "$PROFILE/airootfs/opt/arch-installer"
cp -a "$REPO_ROOT/installer" "$PROFILE/airootfs/opt/arch-installer/"
cp -a "$REPO_ROOT/README.md" "$PROFILE/airootfs/opt/arch-installer/"
find "$PROFILE/airootfs/opt/arch-installer" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
[[ -f "$PROFILE/airootfs/opt/arch-installer/installer/__main__.py" ]] \
  || die "the installer package did not get copied into the profile"

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

before="$(find "$OUT_DIR" -maxdepth 1 -name 'arch-gui-installer-*.iso' -printf '%f\n' 2>/dev/null | sort || true)"

log "running mkarchiso (this takes a while: the live root is downloaded and squashed)"
mkarchiso -v -w "$WORK_DIR/mkarchiso" -o "$OUT_DIR" -P "$JOBS" "$PROFILE"

mapfile -t built < <(
  find "$OUT_DIR" -maxdepth 1 -name 'arch-gui-installer-*.iso' -printf '%f\n' 2>/dev/null | sort
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
  xorriso -indev "$IMAGE" -find /opt/arch-installer -maxdepth 2 -type f 2>/dev/null | grep -q '__main__.py' \
    || die "the installer is not inside the image"
  grep -q 'uefi' <<<"$(xorriso -indev "$IMAGE" -report_el_torito plain 2>/dev/null || true)" \
    || warn "the image reports no UEFI boot entry"
fi

log "done: $IMAGE ($(du -h "$IMAGE" | cut -f1))"
( cd "$(dirname "$IMAGE")" && sha256sum "$(basename "$IMAGE")" ) | tee "$IMAGE.sha256"

# One image of this installer is enough; an older one beside it is how a machine
# ends up booting last week's code.
shopt -s nullglob
for old in "$OUT_DIR"/arch-gui-installer-*.iso "$OUT_DIR"/arch-gui-installer-*.iso.sha256; do
  [[ "$(basename "$old")" == "$(basename "$IMAGE")" ]] && continue
  [[ "$(basename "$old")" == "$(basename "$IMAGE").sha256" ]] && continue
  log "removing the previous image $(basename "$old")"
  rm -f "$old"
done
shopt -u nullglob

cat <<EOF

Write it to a USB stick with:
  sudo dd if=$(basename "$IMAGE") of=/dev/sdX bs=4M status=progress oflag=sync

or boot it directly:
  qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/edk2/x64/OVMF.4m.fd -cdrom $(basename "$IMAGE")
EOF
