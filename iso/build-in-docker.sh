#!/usr/bin/env bash
# Build the installer ISO from any Linux host with Docker.
#
# The build runs inside the official Arch Linux container, so no Arch host and
# no local archiso installation are needed.  --privileged is required because
# mkarchiso mounts loop devices to assemble the image.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${ARCHISO_IMAGE:-archlinux:latest}"

command -v docker >/dev/null 2>&1 || {
  echo "docker is required for this script; use ./build-iso.sh on an Arch host instead" >&2
  exit 1
}

mkdir -p "$REPO_ROOT/out"

docker run --rm --privileged \
  -v "$REPO_ROOT:/build" \
  -w /build \
  "$IMAGE" \
  bash -lc '
    set -Eeuo pipefail
    pacman -Sy --noconfirm --needed archiso python
    ./iso/build-iso.sh --out /build/out
  '
