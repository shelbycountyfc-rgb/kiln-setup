"""Building packages from the AUR inside the new system.

The AUR ships recipes, not binaries: ``makepkg`` runs as an unprivileged user,
needs ``base-devel``, and refuses to run as root.  So the installer creates a
throw-away build account, resolves which dependencies are themselves AUR
packages, and builds that list bottom-up before the packages the user asked for.

Resolution is best-effort and honest about it: a dependency it cannot classify
is left to ``pacman``/``makepkg``, and a failed build stops the installation
with the package name rather than leaving a half-built system silently.
"""

from __future__ import annotations

import re
import shlex

from .config import InstallConfig
from .context import Context

BUILD_USER = "aurbuild"
BUILD_HOME = "/var/lib/aurbuild"
AUR_GIT = "https://aur.archlinux.org"
SRCINFO = AUR_GIT + "/cgit/aur.git/plain/.SRCINFO?h=%s"
MAX_DEPTH = 12

#: Dependency fields that have to exist before a package can be built.
DEP_FIELDS = ("depends", "makedepends", "checkdepends")

_VERSION_OPERATORS = re.compile(r"(>=|<=|=|>|<).*$")


def clean_dependency(entry: str) -> str:
    """``libfoo>=1.2`` -> ``libfoo``; ``glibc: for X`` -> ``glibc``."""
    name = entry.split(":", 1)[0].strip()
    name = _VERSION_OPERATORS.sub("", name).strip()
    return name


def parse_srcinfo(text: str) -> dict:
    """The handful of fields a build needs, from a .SRCINFO file."""
    fields: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key in DEP_FIELDS or key in ("pkgname", "pkgver", "pkgbase", "arch"):
            fields.setdefault(key, []).append(value)
    return fields


def dependencies(srcinfo: dict) -> list:
    found = []
    for key in DEP_FIELDS:
        for entry in srcinfo.get(key, []):
            name = clean_dependency(entry)
            if name and name not in found:
                found.append(name)
    return found


def _in_repositories(ctx: Context, name: str) -> bool:
    """True when pacman can install the dependency from a configured repo."""
    try:
        result = ctx.chroot("pacman -Sddp --print-format '%%n' %s >/dev/null 2>&1" % shlex.quote(name), check=False)
    except Exception:
        return False
    return getattr(result, "returncode", 1) == 0


def _srcinfo(ctx: Context, name: str) -> dict:
    text = ctx.chroot_capture(
        "curl -fsSL --max-time 30 %s" % shlex.quote(SRCINFO % name), check=False
    )
    return parse_srcinfo(text) if text else {}


def resolve(ctx: Context, names) -> list:
    """Order the requested AUR packages so every dependency is built first."""
    ordered: list = []
    seen: set = set()

    def visit(name: str, depth: int) -> None:
        if name in seen or depth > MAX_DEPTH:
            return
        seen.add(name)
        info = _srcinfo(ctx, name)
        for dependency in dependencies(info):
            if _in_repositories(ctx, dependency):
                continue
            # A dependency that is not in a repository is either another AUR
            # package or a virtual name; fetching .SRCINFO tells the two apart.
            if _srcinfo(ctx, dependency):
                visit(dependency, depth + 1)
        if info:
            ordered.append(name)

    for name in names:
        visit(name, 0)
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _prepare_build_user(ctx: Context) -> None:
    ctx.chroot(
        "id -u %s >/dev/null 2>&1 || useradd --system --create-home "
        "--home-dir %s --shell /bin/bash %s"
        % (BUILD_USER, BUILD_HOME, BUILD_USER)
    )
    ctx.write_target(
        "etc/sudoers.d/90-kiln-aurbuild",
        "# Written by Kiln Setup: makepkg cannot run as root, and the build steps\n"
        "# need to install dependencies while running unattended.\n"
        "%s ALL=(ALL:ALL) NOPASSWD: ALL\n" % BUILD_USER,
        mode=0o440,
    )


def install(ctx: Context, cfg: InstallConfig) -> None:
    """Build and install every AUR package the user asked for."""
    names = list(cfg.aur_packages)
    if not cfg.aur or not names:
        return
    ctx.log.log("building %d package(s) from the AUR" % len(names), "step")
    ctx.chroot("pacman -S --needed --noconfirm base-devel git curl", check=True)
    _prepare_build_user(ctx)
    ordered = resolve(ctx, names)
    ctx.log.log("AUR build order: %s" % " ".join(ordered), "plan")
    for name in ordered:
        work = "%s/%s" % (BUILD_HOME, name)
        ctx.chroot("rm -rf %s" % shlex.quote(work), check=False)
        ctx.chroot(
            "runuser -u %s -- git clone --depth 1 %s %s"
            % (BUILD_USER, shlex.quote("%s/%s.git" % (AUR_GIT, name)), shlex.quote(work))
        )
        ctx.chroot(
            "runuser -u %s -- bash -lc %s"
            % (
                BUILD_USER,
                shlex.quote(
                    "cd %s && makepkg --syncdeps --install --needed --noconfirm" % work
                ),
            )
        )
        ctx.chroot("rm -rf %s" % shlex.quote(work), check=False)


def names(cfg: InstallConfig) -> list:
    return list(cfg.aur_packages) if cfg.aur else []
