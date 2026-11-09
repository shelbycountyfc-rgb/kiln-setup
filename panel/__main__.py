"""Entry point: ``python3 -m panel``."""

from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__


def _failing_frame(frames, root: str | None = None):
    """The innermost frame of this project.

    Tk raises its errors from inside tkinter itself, so the last frame of a
    colour or option error is ``tkinter/__init__.py``: the line that matters is
    the one in the panel or the installer that handed the value over.
    """
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inside = [
        frame
        for frame in frames
        if os.path.abspath(frame.filename).startswith(root + os.sep)
    ]
    if inside:
        return inside[-1]
    return frames[-1] if frames else None


def _frame_label(frame) -> str:
    """``panel/pages.py:812``: the path as it reads beside /opt/kiln-setup."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        path = os.path.relpath(os.path.abspath(frame.filename), root)
    except ValueError:  # a different drive on Windows
        path = os.path.basename(frame.filename)
    return "%s:%d" % (path, frame.lineno)


def _check() -> int:
    from . import core
    from installer.context import Logger, human_bytes

    ctx = core.PanelContext(Logger(None))
    total, available = core.memory()
    disk_total, disk_used, disk_free = core.disk_usage("/")
    release = core.os_release()
    print("%s %s" % (APP_NAME, __version__))
    print("System   : %s" % release.get("PRETTY_NAME", "unknown"))
    print("Kernel   : %s" % core.kernel())
    print("Hostname : %s" % core.hostname())
    print("CPU      : %s" % core.cpu_model())
    print("Memory   : %s available of %s" % (human_bytes(available), human_bytes(total)))
    print("Storage  : %s free of %s" % (human_bytes(disk_free), human_bytes(disk_total)))
    print("Packages : %d installed" % core.installed_count(ctx))
    print("Multilib : %s" % ("enabled" if core.multilib_enabled() else "disabled"))
    print("Updates  : %d available" % len(core.updates(ctx)))
    print("Elevation: %s" % (" ".join(ctx.elevator()) or "none available"))
    for name, uid, groups, _home, _shell in core.users():
        print("User     : %s (uid %d) %s" % (name, uid, ",".join(groups)))
    return 0


def _fonts():
    """Ask Tk for a font.  Without one, no window can draw anything at all."""
    try:
        import tkinter
        from tkinter import font as tkfont
    except Exception as error:  # a missing Tk is already reported above
        return "unknown (%s)" % error, None
    try:
        root = tkinter.Tk()
    except Exception as error:
        return "unknown (%s)" % error, None
    try:
        root.withdraw()
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        tkfont.Font(root=root, family=family, size=10).measure("Kiln")
        return "%s (%d families)" % (family, len(tkfont.families(root))), None
    except Exception as error:
        return "none (%s)" % error, (
            "Tk cannot allocate a font, so the window cannot draw: "
            "sudo pacman -S ttf-dejavu  (%s)" % error
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _doctor() -> int:
    """Say why the panel would not start, before a desktop entry hides it."""
    problems = []
    print("%s %s" % (APP_NAME, __version__))
    print("Interpreter : %s" % sys.executable)

    try:
        import tkinter  # noqa: F401

        print("Tk          : available")
    except Exception as error:  # a missing tk is a package, not a crash
        print("Tk          : missing (%s)" % error)
        problems.append("Tk is missing: sudo pacman -S tk")

    root = "/opt/kiln-setup"
    for name in ("panel", "installer"):
        present = os.path.isdir(os.path.join(root, name))
        print("%-12s: %s" % (name.capitalize(), "present" if present else "missing"))
        if not present:
            problems.append(
                "%s/%s is missing; install again from the medium, or copy the "
                "package there" % (root, name)
            )

    display = os.environ.get("DISPLAY", "") or os.environ.get("WAYLAND_DISPLAY", "")
    print("Display     : %s" % (display or "none"))
    if not display:
        problems.append("there is no display: start it from the desktop session")

    fonts, font_problem = _fonts()
    print("Fonts       : %s" % fonts)
    if font_problem:
        problems.append(font_problem)

    from installer.gui import theme as theme_module

    print("Theme from  : %s" % getattr(theme_module, "__file__", "?"))
    bad_colours = theme_module.colour_problems()
    print(
        "Theme       : %d colours%s"
        % (
            len(theme_module.COLOUR_NAMES),
            ""
            if not bad_colours
            else ", not #rrggbb: "
            + ", ".join("%s=%r" % pair for pair in bad_colours),
        )
    )
    if bad_colours:
        problems.append(
            "the theme palette is not valid, so the files under /opt/kiln-setup "
            "are not the ones this panel was built with: copy them again from "
            "the medium"
        )

    log = os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "kiln-panel.log",
    )
    print("Log         : %s" % log)

    if problems:
        print("\nProblems:")
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("\nNo problem found: 'kiln-panel' should open the window.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="kiln-panel",
        description="%s: manage the installed Arch system (software, services, users, network)." % APP_NAME,
    )
    parser.add_argument("--kiosk", action="store_true", help="fill the screen")
    parser.add_argument("--check", action="store_true", help="print the system state and exit")
    parser.add_argument(
        "--doctor", action="store_true",
        help="say why the window would not open, then exit",
    )
    parser.add_argument("--version", action="version", version="kiln-panel " + __version__)
    args = parser.parse_args(argv)

    if args.doctor:
        return _doctor()
    if args.check:
        return _check()

    from .app import Panel

    try:
        Panel(kiosk=args.kiosk).mainloop()
    except Exception as error:  # the launcher shows this instead of an empty desktop
        import traceback

        traceback.print_exc()
        # The traceback lands in the log; this is the line the dialog shows.  The
        # file and line are what turn "a colour went wrong somewhere in Tk" into
        # a place to look, and a broken palette is named outright.
        frames = traceback.extract_tb(sys.exc_info()[2])
        frame = _failing_frame(frames)
        where = " at %s" % _frame_label(frame) if frame is not None else ""
        tk_frame = ""
        if frames and frame is not None and frames[-1] is not frame:
            tk_frame = " (Tk raised it in %s:%d)" % (
                os.path.basename(frames[-1].filename), frames[-1].lineno,
            )
        hint = ""
        if "unknown color" in str(error):
            from installer.gui import theme as theme_module

            bad = theme_module.colour_problems()
            if bad:
                hint = (
                    "\nThe palette in /opt/kiln-setup is not valid: %s."
                    % ", ".join("%s=%r" % pair for pair in bad)
                )
            else:
                hint = (
                    "\nThe palette itself is valid (%s), so that colour comes "
                    "from a widget option in the files at /opt/kiln-setup."
                    % ", ".join(theme_module.COLOUR_NAMES)
                )
        print(
            "kiln-panel: the window could not open (%s%s)%s%s\n"
            "Run 'kiln-panel --doctor': it names the missing piece and the "
            "package that provides it." % (error, where, tk_frame, hint),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
