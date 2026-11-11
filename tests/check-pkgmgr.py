#!/usr/bin/env python3
"""Checks for the Kiln package manager.

No pacman, no Arch machine and no network: the backend keeps every decision in a
plain function so it can be looked at directly, and the launcher and menu entry
are written into a temporary root and read back.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from installer import answers, system  # noqa: E402
from installer.config import InstallConfig  # noqa: E402
from installer.context import Context, Logger  # noqa: E402
from pkgmgr import core  # noqa: E402

failures: list = []


def check(condition, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


PACMAN_SEARCH = """\
extra/vim 9.1.0000-1 [installed]
    Vi Improved, a highly configurable, improved version of the vi text editor
core/neovim 0.10.0-1
    Fork of Vim aiming to improve user experience, plugins, and GUIs
"""

AUR_SEARCH = """\
aur/visual-studio-code-bin 1.90.0-1 (+2345 4.53)
    Visual Studio Code (binary release)
aur/yay 12.3.5-1 (+2100 4.10)
    Yet another yogurt. Pacman wrapper and AUR helper.
"""

INSTALLED = "vim 9.1.0000-1\nneovim 0.10.0-1\nyay 12.3.5-1\n"
FOREIGN = "yay 12.3.5-1\n"
UPDATES = "vim 9.1.0000-1 -> 9.1.0100-1\n"


# -- reading pacman's output ------------------------------------------------
repos = core.parse_search(PACMAN_SEARCH)
check(len(repos) == 2, "two repository packages are read from pacman -Ss")
check(
    [package.name for package in repos] == ["vim", "neovim"],
    "the names come from the repo/name column, not the whole line",
)
check(
    repos[0].repo == "extra" and repos[0].version == "9.1.0000-1",
    "the repository and version are split off the header line",
)
check(repos[0].installed and not repos[1].installed,
      "[installed] marks only the package that carries it")
check(
    repos[0].description.startswith("Vi Improved"),
    "the indented line under a package is its description",
)

aur = core.parse_search(AUR_SEARCH)
check([package.name for package in aur] == ["visual-studio-code-bin", "yay"],
      "AUR rows are read from yay -Ss --aur")
check(all(package.aur and package.origin == "AUR" for package in aur),
      "AUR rows are marked as AUR, which is what decides who installs them")
check(aur[1].version == "12.3.5-1",
      "yay's vote counts behind the version are not swept into it")
check(aur[1].description.startswith("Yet another yogurt"),
      "the AUR description survives the vote counts")

installed = core.parse_installed(INSTALLED, FOREIGN)
check(len(installed) == 3, "three installed packages are read from pacman -Q")
check(
    {package.name for package in installed if package.aur} == {"yay"},
    "pacman -Qm marks the packages that came from the AUR",
)
check(all(package.installed for package in installed),
      "every package from pacman -Q counts as installed")

updates = core.parse_updates(UPDATES)
check(
    len(updates) == 1 and updates[0].name == "vim" and updates[0].version == "9.1.0100-1",
    "an update row keeps the name and the new version",
)

# -- what a search asks for -------------------------------------------------
both = core.search_commands("vim", "Everything")
check(
    both == [["pacman", "-Ss", "vim"], ["yay", "-Ss", "--aur", "vim"]],
    "Everything searches the repositories and then the AUR",
)
check(core.search_commands("vim", "AUR") == [["yay", "-Ss", "--aur", "vim"]],
      "the AUR scope asks only yay")
check(core.search_commands("vim", "Repositories") == [["pacman", "-Ss", "vim"]],
      "the repository scope asks only pacman")
check(core.search_commands("", "Repositories") == [["pacman", "-Ss"]],
      "an empty query lists instead of searching for an empty word")
check(core.search_commands("vim", "Installed") == [],
      "the installed scope runs no command at all")

merged = core.merge(repos, aur)
check([package.name for package in merged] == ["vim", "neovim",
                                               "visual-studio-code-bin", "yay"],
      "one row per name, repository rows before AUR rows")
check(
    core.merge([core.Package("yay", repo="aur")],
               [core.Package("yay", repo="extra")])[0].repo == "aur",
    "the first group wins, so result order decides which source is shown",
)
check(
    [package.name for package in core.filter_installed(installed, "neo")] == ["neovim"],
    "the installed filter matches part of a name",
)
check(
    [
        package.name
        for package in core.filter_installed(
            [core.Package("yay", description="Yet another yogurt")], "yogurt"
        )
    ]
    == ["yay"],
    "a row that carries a description is filtered by it as well",
)
check(len(core.filter_installed(installed, "")) == 3,
      "an empty filter keeps every installed package")

# -- who installs what ------------------------------------------------------
repo_package = core.Package("vim", repo="extra")
aur_package = core.Package("yay", repo="aur")
check(core.install_command(repo_package) ==
      ["pacman", "-S", "--needed", "--noconfirm", "vim"],
      "a repository package is installed by pacman, non-interactively")
check(core.install_command(aur_package) ==
      ["yay", "-S", "--needed", "--noconfirm", "yay"],
      "an AUR package goes through yay, which builds it with makepkg")
check(core.install_command(aur_package)[0] != "pacman",
      "an AUR recipe is never handed to pacman as if it were a binary")
check(core.remove_command(repo_package) ==
      ["pacman", "-Rns", "--noconfirm", "vim"],
      "removal takes unneeded dependencies and configuration with it")
check(core.upgrade_command() == ["pacman", "-Syu", "--noconfirm"],
      "the upgrade is a full -Syu, not a partial one")
check(core.build_tools_command() ==
      ["pacman", "-S", "--needed", "--noconfirm", "base-devel", "git"],
      "yay's build tools are the two the AUR needs")

bootstrap = core.yay_bootstrap_command("/tmp/kiln-yay-123")
check(bootstrap[:2] == ["bash", "-lc"], "yay is built by a shell, as the user")
check("https://aur.archlinux.org/yay.git" in bootstrap[2],
      "yay is cloned from the AUR, not from a mirror of unknown age")
check("makepkg -si --noconfirm" in bootstrap[2],
      "yay is built and installed by makepkg")
check("/tmp/kiln-yay-123" in bootstrap[2],
      "the build happens in the directory the window chose")

with mock.patch.object(core, "terminal", return_value=("xterm", ["-e"])):
    wrapped = core.terminal_command(["yay", "-S", "pkg"])
check(wrapped == ["xterm", "-e", "yay", "-S", "pkg"],
      "AUR work is wrapped so the terminal runs the command, not the other way round")
with mock.patch.object(core, "terminal", return_value=("", [])):
    check(core.terminal_command(["yay"]) == [],
          "with no terminal emulator the window says so instead of hanging on sudo")

# -- running a command ------------------------------------------------------
lines: list = []
code = core.stream([sys.executable, "-c", "print('one'); print('two')"], lines.append)
check(code == 0 and lines == ["one", "two"],
      "stream() hands back every line and the exit status")
lines = []
code = core.stream([sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"], lines.append)
check(code == 3 and lines == ["bad"],
      "a failing command reports its own status, not a success")

# -- the launcher and the menu entry ---------------------------------------
check("kiln-packages.log" in system.PACKAGES_LAUNCHER and "python3 -m pkgmgr"
      in system.PACKAGES_LAUNCHER,
      "the launcher logs and starts the package manager")
check("TryExec=/usr/local/bin/kiln-packages" in system.PACKAGES_DESKTOP,
      "the menu entry points at the launcher that exists")
check("Categories=System;PackageManager;" in system.PACKAGES_DESKTOP,
      "the entry is filed under software management, not left uncategorised")

root = tempfile.mkdtemp(prefix="kiln-pkgmgr-")
os.makedirs(os.path.join(root, "opt/kiln-setup/pkgmgr"))
open(os.path.join(root, "opt/kiln-setup/pkgmgr/__main__.py"), "w").close()
ctx = Context(Logger(None), target=root)
system.install_package_manager(ctx, InstallConfig())
launcher_path = os.path.join(root, "usr/local/bin/kiln-packages")
entry_path = os.path.join(root, "usr/share/applications/kiln-packages.desktop")
check(os.path.isfile(launcher_path), "the launcher is written once the sources are there")
check(os.path.isfile(entry_path), "the menu entry is written once the sources are there")
check(os.access(launcher_path, os.X_OK), "the launcher is executable")
check(os.path.isfile(entry_path) and "Exec=/usr/local/bin/kiln-packages" in
      open(entry_path, encoding="utf-8").read(),
      "the entry runs the launcher, not python directly")

empty_root = tempfile.mkdtemp(prefix="kiln-pkgmgr-empty-")
empty_ctx = Context(Logger(None), target=empty_root)
system.install_package_manager(empty_ctx, InstallConfig())
check(not os.path.exists(os.path.join(empty_root, "usr/share/applications/kiln-packages.desktop")),
      "no menu entry is written for a package manager that did not arrive")

# -- part of the installation ----------------------------------------------
check(InstallConfig().package_manager is True,
      "a new installation includes the package manager unless it is turned off")
check(InstallConfig.from_dict({"packageManager": False}).package_manager is False,
      "an answer file can leave the package manager out")
check(InstallConfig().to_public()["packageManager"] is True,
      "the option is saved into the answers the window writes")
check(answers.example()["packageManager"] is True,
      "the example answers turn it on, like the window does")


def _source(relative: str) -> str:
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


check('"packageManager": True' in _source("installer/gui/app.py"),
      "the window's form starts with the package manager turned on")
check("packageManager" in _source("installer/gui/pages.py"),
      "the Software page offers the package manager as a choice")
check("if self.cfg.package_manager:" in _source("installer/orchestrator.py"),
      "the installation only adds it when the option is on")

# -- results are shown by category ------------------------------------------
check(
    core.category_of(core.Package(
        "vim",
        description=(
            "Vi Improved, a highly configurable, improved version of the vi "
            "text editor"
        ),
    ))
    == "Development",
    "an editor is filed under Development",
)
check(
    core.category_of(
        core.Package("firefox", description="Standalone web browser from Mozilla")
    )
    == "Internet",
    "a browser is filed under Internet",
)
check(
    core.category_of(core.Package("gimp", description="GNU Image Manipulation Program"))
    == "Graphics",
    "an image program is filed under Graphics",
)
check(
    core.category_of(
        core.Package("vlc", description="Multi-platform MPEG, VCD/DVD, and DivX player")
    )
    == "Multimedia",
    "a media player is filed under Multimedia",
)
check(
    core.category_of(core.Package("linux", description="The Linux kernel and modules"))
    == "System",
    "a kernel is filed under System",
)
check(
    core.category_of(
        core.Package("xfce4-panel", description="Panel for the Xfce desktop environment")
    )
    == "Desktop",
    "a panel for a desktop environment is filed under Desktop",
)
_codec = core.Package("thing", description="a codec")
check(
    core.category_of(_codec) == "Multimedia",
    "a codec is not caught by the word code, which belongs to Development: %r"
    % (core.category_of(_codec),),
)
check(
    core.category_of(core.Package("mystery-package", description="")) == "Other",
    "a package nothing describes is filed under Other",
)
check(
    all(core.category_of(package) in core.CATEGORIES
        for package in [repos[0], aur[0], installed[0]]),
    "every package lands on a shelf the window shows",
)

_mixed = [
    core.Package("mystery-package", description=""),
    core.Package("firefox", description="Standalone web browser from Mozilla"),
    core.Package("vim", description="text editor"),
]
_shelves = core.group_by_category(_mixed)
check(
    [name for name, _packages in _shelves] == ["Development", "Internet", "Other"],
    "the shelves come in the order the window lists them: %r"
    % ([name for name, _packages in _shelves],),
)
check(
    sum(len(packages) for _name, packages in _shelves) == len(_mixed),
    "grouping loses no package",
)
check(
    [package.name for _name, packages in _shelves for package in packages]
    == ["vim", "firefox", "mystery-package"],
    "each package appears once, under its own shelf",
)
check(core.group_by_category([]) == [], "an empty result has no empty headings")
check(
    len(core.group_by_category([core.Package("firefox", description="web browser")])) == 1,
    "only shelves that have packages under them are shown",
)

# -- the command line -------------------------------------------------------
version = subprocess.run([sys.executable, "-m", "pkgmgr", "--version"],
                         capture_output=True, text=True, cwd=ROOT)
check(version.returncode == 0 and version.stdout.strip() == "1.0",
      "--version prints the version and exits")
doctor = subprocess.run([sys.executable, "-m", "pkgmgr", "--check"],
                        capture_output=True, text=True, cwd=ROOT)
check("pacman:" in doctor.stdout and "terminal:" in doctor.stdout,
      "--check reports pacman, yay, elevation and terminal without opening a window")
check(doctor.returncode in (0, 1),
      "--check exits 0 on an Arch machine and 1 where pacman is missing")

print()
if failures:
    print("PKGMGR CHECK FAIL: %d failure(s)" % len(failures))
    for message in failures:
        print("  - %s" % message)
    raise SystemExit(1)
print("PKGMGR CHECK PASS: 0 failure(s)")
