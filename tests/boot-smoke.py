"""Boot the medium in QEMU and check that the installer window appears.

A boot entry inside the ISO proves nothing: the kernel can panic, the squashfs
can fail to mount, or the window can fail to start.  The framebuffer is read
back through QEMU's monitor, so this runs on a runner without a display.

Usage: python tests/boot-smoke.py <image.iso> [--bios|--uefi] [--wait 180]
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time

OVMF = (
    "/usr/share/OVMF/OVMF_CODE.fd",
    "/usr/share/edk2/x64/OVMF_CODE.fd",
    "/usr/share/qemu/OVMF.fd",
)


def read_ppm(path):
    """Return ``(width, height, pixels)`` for a binary PPM."""
    raw = open(path, "rb").read()
    fields, rest = [], raw
    while len(fields) < 4 and rest:
        line, _sep, tail = rest.partition(b"\n")
        rest = tail
        line = line.strip()
        if line and not line.startswith(b"#"):
            fields.extend(line.split())
    if len(fields) < 4 or fields[0] != b"P6":
        raise RuntimeError("not a binary PPM: %r" % (fields[:4],))
    width, height = int(fields[1]), int(fields[2])
    return width, height, rest


def lit_fraction(width, height, pixels):
    total = max(1, width * height)
    lit = 0
    for index in range(0, min(len(pixels), total * 3), 3):
        if max(pixels[index], pixels[index + 1], pixels[index + 2]) > 24:
            lit += 1
    return lit / float(total)


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--uefi", action="store_true")
    parser.add_argument("--wait", type=int, default=180)
    parser.add_argument("--minimum", type=float, default=0.05)
    args = parser.parse_args(argv[1:])

    work = tempfile.mkdtemp(prefix="kiln-boot-")
    monitor = os.path.join(work, "monitor.sock")
    dump = os.path.join(work, "screen.ppm")
    command = [
        "qemu-system-x86_64", "-m", "3072", "-smp", "2",
        "-cdrom", args.image, "-display", "none", "-vga", "std",
        "-no-reboot", "-monitor", "unix:%s,server=on,wait=off" % monitor,
    ]
    if args.uefi:
        firmware = next((path for path in OVMF if os.path.exists(path)), "")
        if not firmware:
            print("SKIP: no OVMF firmware on this machine")
            return 0
        command += ["-bios", firmware]

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    print("booting %s (%s)" % (args.image, "UEFI" if args.uefi else "BIOS"))
    try:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            if process.poll() is not None:
                break
            time.sleep(5)
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            print("QEMU exited before the deadline:\n%s" % output[-2000:])
            return 1
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(monitor)
            connection.sendall(("screendump %s\n" % dump).encode())
            time.sleep(3)
        if not os.path.exists(dump):
            print("the monitor produced no screen dump")
            return 1
        width, height, pixels = read_ppm(dump)
        lit = lit_fraction(width, height, pixels)
        print("screen %dx%d, %.1f%% of it is lit" % (width, height, lit * 100))
        if lit < args.minimum:
            print("FAIL: the screen stayed dark: the medium did not reach the installer")
            return 1
        print("PASS: the installer drew its window")
        return 0
    finally:
        process.kill()
        try:
            process.wait(timeout=10)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
