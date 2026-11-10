"""Open the Kiln package manager, or answer a question about it without a window.

``--check`` and ``--search`` exist for machines with no display and for the
tests: they use the same backend the window uses, so a problem in the parsing or
in the command lines shows up without anyone clicking anything.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, core


def check() -> int:
    """Print what this machine can do, and whether every piece is there."""
    abilities = core.summary()
    print("kiln-packages %s" % __version__)
    print("pacman:   %s" % (abilities["pacman"] or "MISSING"))
    print("yay:      %s" % (abilities["yay"] or "not installed (AUR entries need it)"))
    print("elevate:  %s" % (" ".join(abilities["elevator"]) or "MISSING (no pkexec, no sudo)"))
    print("terminal: %s" % (abilities["terminal"] or "MISSING (AUR builds need one)"))
    if not abilities["pacman"]:
        print("This is not an Arch machine: the window would list nothing.", file=sys.stderr)
        return 1
    return 0


def search(text: str) -> int:
    """Search without a window, in the same order the window shows results."""
    groups = [core.parse_search(core.capture(argv)) for argv in core.search_commands(text, "Everything")]
    rows = core.merge(*groups)
    if not rows:
        print("no package matches %r" % text, file=sys.stderr)
        return 1
    for package in rows:
        print("%-10s %-30s %s" % (package.origin, str(package), package.description))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="kiln-packages",
        description="Install and remove pacman packages, and build AUR recipes with yay.",
    )
    parser.add_argument("--check", action="store_true",
                        help="print what this machine has and exit")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--search", metavar="QUERY",
                        help="search the repositories and the AUR in the terminal")
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.check:
        return check()
    if args.search:
        return search(args.search)
    from .app import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
