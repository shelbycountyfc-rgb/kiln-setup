"""The kernel side of Epic Games' anti-cheat (EAC Linux v2).

EAC Linux v2 loads no anti-cheat kernel module.  It uses what the kernel already
offers, and it refuses a machine that does not provide it: a kernel and modules
signed with well-known keys, Secure Boot, a measured boot from a TPM 2.0, the
IOMMU on without passthrough, lockdown in integrity mode, the BPF LSM active,
and kexec disabled.

Three of those are parameters on the kernel command line, and they are written
here so every boot entry the installer produces carries the same set.  The rest
are firmware and signing decisions (Secure Boot keys, TPM 2.0, distro-signed
kernels) that no command line can satisfy, so this module does not pretend to.

Turning the profile on does not make Fortnite run: Epic has to ship EAC Linux v2
and release the game for Linux first.
"""

from .hardware import AMD, INTEL

#: Lockdown in integrity mode closes /dev/mem, kprobes, unsigned module loading
#: and the rest of the live-patching surface the anti-cheat needs closed.
LOCKDOWN = "lockdown=integrity"

#: kexec starts a second kernel behind the running one, out of reach of both
#: lockdown and Secure Boot.
NO_KEXEC = "kexec_load_disabled=1"

#: The IOMMU is what stops a device from reading memory it was never given.  The
#: parameter is per vendor, and passthrough (iommu=pt) is deliberately not set:
#: it is the DMA remapping EAC wants to find switched on.
IOMMU = {INTEL: "intel_iommu=on", AMD: "amd_iommu=on"}


def iommu_parameters(cpu_vendor: str = "") -> list:
    """The IOMMU parameter for this CPU, or both when the CPU is unknown."""
    parameter = IOMMU.get(cpu_vendor)
    if parameter:
        return [parameter]
    # Each parameter is read only by the driver it belongs to, so on a machine
    # whose vendor was not detected one of the two applies and the other is
    # ignored.
    return [IOMMU[INTEL], IOMMU[AMD]]


def kernel_parameters(cfg, cpu_vendor: str = "") -> list:
    """The kernel command line EAC Linux v2 asks for, when it was chosen."""
    if not getattr(cfg, "eac", False):
        return []
    return [LOCKDOWN, NO_KEXEC] + iommu_parameters(cpu_vendor)
