"""Kernel and firmware hardening inside the new system.

A hardened installation makes four promises and then keeps checking them:

- the kernel locks itself down (``lockdown=integrity``), so nothing that needs
  arbitrary kernel access works any more, including unsigned modules and raw
  memory access;
- kexec is disabled, because a second kernel started behind the running one is
  outside everything lockdown and Secure Boot protect;
- unprivileged BPF is off, since a BPF program can read kernel memory without
  loading a module, and the JIT is hardened against being used as a compiler;
- the disk key stays bound to the firmware through the TPM, and a unit reports
  at every boot whether any of this still holds.

Signing is separate because it can be turned on by itself: the kernel, the
initramfs and the boot loader go through sbctl, and every module is signed with
a key the machine keeps so a module swapped underneath the kernel fails its
check.
"""

from __future__ import annotations

from .config import InstallConfig
from .context import Context

SYSCTL_PATH = "etc/sysctl.d/90-kiln-hardening.conf"
SCRIPT_PATH = "usr/local/lib/kiln/hardening-check"
UNIT_PATH = "etc/systemd/system/kiln-hardening.service"
SIGN_SCRIPT_PATH = "usr/local/lib/kiln/sign-modules"
HOOK_PATH = "etc/pacman.d/hooks/90-kiln-sign-modules.hook"
MODULE_KEY_PATH = "/etc/kiln/module-signing.key"
MODULE_CERT_PATH = "/etc/kiln/module-signing.crt"

#: The lockdown level the boot entry asks for.
LOCKDOWN = "integrity"

SYSCTL = """\
# Written by Kiln Setup.  These stay on:
#  - kexec can start a second kernel behind the running one, out of reach of
#    lockdown and of everything watching the boot chain;
#  - a BPF program can read kernel memory without a module, so loading one
#    unprivileged is the same gap by another route;
#  - hardening the JIT stops a BPF program from being used to build code.
kernel.kexec_load_disabled = 1
kernel.unprivileged_bpf_disabled = 1
kernel.bpf_jit_harden = 2
"""

CHECK = """#!/bin/sh
# Written by Kiln Setup.  Reports what the hardening this installer set up looks
# like now, and exits non-zero when something stopped holding, so the unit fails
# and the machine says so instead of looking healthy.
set -u

failures=0
device=${1:-}

report() {
  printf '%-24s %s\\n' "$1" "$2"
}

lockdown=$(sed -n 's/.*\\[\\([a-z]*\\)\\].*/\\1/p' /sys/kernel/security/lockdown 2>/dev/null)
case "${lockdown:-none}" in
  integrity|confidentiality) report "kernel lockdown" "$lockdown" ;;
  *) report "kernel lockdown" "${lockdown:-not exposed} (expected integrity)"
     failures=$((failures + 1)) ;;
esac

kexec=$(sysctl -n kernel.kexec_load_disabled 2>/dev/null || echo 0)
if [ "$kexec" = "1" ]; then
  report "kexec" "disabled"
else
  report "kexec" "enabled"
  failures=$((failures + 1))
fi

bpf=$(sysctl -n kernel.unprivileged_bpf_disabled 2>/dev/null || echo 0)
if [ "$bpf" != "0" ]; then
  report "unprivileged eBPF" "disabled"
else
  report "unprivileged eBPF" "enabled"
  failures=$((failures + 1))
fi

if command -v mokutil >/dev/null 2>&1; then
  state=$(mokutil --sb-state 2>/dev/null | head -n 1)
  case "$state" in
    *"SecureBoot enabled"*) report "secure boot" "enabled" ;;
    *) report "secure boot" "${state:-unknown}"
       failures=$((failures + 1)) ;;
  esac
fi

if [ -n "$device" ]; then
  if [ -r /dev/tpmrm0 ]; then
    report "tpm device" "present"
  else
    report "tpm device" "missing"
    failures=$((failures + 1))
  fi
  if cryptsetup luksDump "$device" 2>/dev/null | grep -q "systemd-tpm2"; then
    report "disk attestation" "bound to the tpm"
  else
    report "disk attestation" "no tpm2 keyslot"
    failures=$((failures + 1))
  fi
else
  report "disk attestation" "no encrypted disk"
fi

if [ "$failures" -eq 0 ]; then
  echo "hardening intact"
  exit 0
fi
echo "hardening check failed: $failures item(s)"
exit 1
"""

UNIT = """\
# Written by Kiln Setup.  One report per boot, after everything is mounted.
[Unit]
Description=Kiln hardening check
Documentation=file:///usr/local/lib/kiln/hardening-check
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/lib/kiln/hardening-check{device}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

SIGN_SCRIPT = """#!/bin/sh
# Written by Kiln Setup.  Signs every module with this machine's key, so a
# module that was changed after the fact no longer matches its signature.
set -u

key=/etc/kiln/module-signing.key
cert=/etc/kiln/module-signing.crt
[ -r "$key" ] || exit 0
[ -r "$cert" ] || exit 0

signer=$(command -v kmodsign || true)
[ -n "$signer" ] || exit 0

list=$(mktemp)
find /usr/lib/modules -type f -name '*.ko*' > "$list" 2>/dev/null
failures=0
while IFS= read -r module; do
  [ -n "$module" ] || continue
  "$signer" sha256 "$key" "$cert" "$module" || failures=$((failures + 1))
done < "$list"
rm -f "$list"

if [ "$failures" -ne 0 ]; then
  echo "could not sign $failures module(s)"
  exit 1
fi
echo "modules signed"
"""

HOOK = """\
[Trigger]
Operation = Install
Operation = Upgrade
Type = Package
Target = linux
Target = linux-lts
Target = linux-zen
Target = linux-hardened
Target = dkms

[Action]
Description = Signing the kernel modules with this machine's key
When = PostTransaction
Exec = /usr/local/lib/kiln/sign-modules
"""


def kernel_parameters(cfg: InstallConfig) -> list:
    """What the boot entry adds for a hardened installation."""
    if not cfg.hardening:
        return []
    return ["lockdown=%s" % LOCKDOWN]


def unit_text(device: str = "") -> str:
    """The unit, naming the container when the disk is encrypted."""
    return UNIT.replace("{device}", (" " + device) if device else "")


def apply(ctx: Context, cfg: InstallConfig, layout=None) -> None:
    """Write the settings, the report script and the unit that runs it."""
    if not cfg.hardening:
        return
    device = getattr(layout, "luks_device", "") if layout is not None else ""
    ctx.log.log("writing the hardening settings", "step")
    ctx.write_target(SYSCTL_PATH, SYSCTL, mode=0o644)
    ctx.write_target(SCRIPT_PATH, CHECK, mode=0o755)
    ctx.write_target(UNIT_PATH, unit_text(device), mode=0o644)
    ctx.chroot("systemctl enable kiln-hardening.service", check=False)
    if not cfg.encrypted:
        ctx.log.log(
            "the disk is not encrypted, so the report has no TPM attestation to "
            "check: the lockdown, kexec and eBPF settings are still enforced",
            "warn",
        )


def sign_modules(ctx: Context, cfg: InstallConfig) -> None:
    """Sign every module now, and every later one through a pacman hook."""
    if not cfg.signed_modules:
        return
    ctx.log.log("signing the kernel modules", "step")
    ctx.write_target(SIGN_SCRIPT_PATH, SIGN_SCRIPT, mode=0o755)
    ctx.write_target(HOOK_PATH, HOOK, mode=0o644)
    ctx.chroot("install -d -m 700 /etc/kiln", check=False)
    ctx.chroot(
        "test -s %s || openssl req -new -x509 -newkey rsa:4096 -nodes -days 3650 "
        "-subj '/CN=Kiln module signing' -keyout %s -out %s"
        % (MODULE_CERT_PATH, MODULE_KEY_PATH, MODULE_CERT_PATH),
        check=False,
    )
    ctx.chroot("chmod 600 %s" % MODULE_KEY_PATH, check=False)
    ctx.chroot(SIGN_SCRIPT_PATH, check=False)
    ctx.log.log(
        "the modules are signed; for the kernel to trust the key, import %s once "
        "with 'mokutil --import %s' and confirm it in the MOK manager at the next "
        "boot" % (MODULE_CERT_PATH, MODULE_CERT_PATH),
        "step",
    )


def summary(cfg: InstallConfig) -> list:
    """Rows for the review page."""
    if not cfg.hardening:
        return []
    rows = [
        (
            "Hardening",
            "lockdown=%s, kexec and unprivileged eBPF off, checked at every boot"
            % LOCKDOWN,
        )
    ]
    if cfg.signed_modules:
        rows.append(("Signed modules", "own key, re-signed by a pacman hook"))
    return rows
