"""Choosing the package mirrors on the live medium.

``reflector`` asks every mirror how fast it answers and writes the quickest
ones into the list pacman reads.  The medium already ships a working list, so
this is an optimisation: when reflector is missing, has no network, or finds
nothing, the list that was there is put back and the installation still works.
"""

from __future__ import annotations

MIRRORLIST = "/etc/pacman.d/mirrorlist"


def available() -> bool:
    from .context import which

    return which("reflector") is not None


def servers(path: str = MIRRORLIST) -> int:
    """How many mirrors the list currently names."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for line in handle if line.lstrip().startswith("Server"))
    except OSError:
        return 0


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def refresh(ctx, country: str = "", protocol: str = "https", count: int = 20) -> tuple:
    """Ask reflector for a fresh list; returns ``(servers, message)``.

    A reflector run that changes nothing is not an error the installation
    should stop for, so the previous list is restored and a message is returned.
    """
    from .context import which

    if not ctx.dry_run and which("reflector") is None:
        raise RuntimeError("reflector is not installed on this medium")
    before = "" if ctx.dry_run else _read(MIRRORLIST)
    args = [
        "reflector",
        "--latest",
        str(max(1, count)),
        "--sort",
        "rate",
        "--protocol",
        protocol,
        "--save",
        MIRRORLIST,
    ]
    if country:
        args[1:1] = ["--country", country]
    ctx.run(args, check=False, quiet=True)
    if ctx.dry_run:
        return servers(MIRRORLIST), "Simulation: reflector would rewrite %s." % MIRRORLIST
    # Read the path explicitly: a default argument would still point at the
    # module-level value from import time.
    found = servers(MIRRORLIST)
    if found == 0:
        if before:
            try:
                with open(MIRRORLIST, "w", encoding="utf-8") as handle:
                    handle.write(before)
            except OSError:
                pass
        raise RuntimeError(
            "no mirror answered, so the list that was there is unchanged"
        )
    where = country or "the whole world"
    return found, "%d mirrors answered for %s; the list is updated." % (found, where)
