"""Facts and actions behind the Kiln package manager.

Nothing here draws anything: the parsing of pacman's output and the choice of
command line are plain functions, so they can be checked without pacman, without
an Arch machine and without a network.

Official repositories and the AUR are handled differently on purpose.  A
repository package is a binary pacman installs.  An AUR package is a recipe that
``makepkg`` builds on this machine as an unprivileged user, with ``sudo`` for the
last step, so AUR work is handed to a terminal window: a window owned by this
process has no terminal, and ``sudo`` cannot ask for a password it never gets.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass

PACMAN = "pacman"
YAY = "yay"
AUR_GIT_URL = "https://aur.archlinux.org/yay.git"

#: Build dependencies of yay itself (and of nearly every AUR recipe).
BUILD_TOOLS = ("base-devel", "git")

#: Terminal emulators that can run one command in a window of their own, with
#: the flag that separates their options from that command.
TERMINALS = (
    ("xfce4-terminal", ["-x"]),
    ("konsole", ["-e"]),
    ("xterm", ["-e"]),
    ("gnome-terminal", ["--"]),
)

#: ``repo/name version`` and whatever pacman or yay prints behind it:
#: ``[installed]``, ``[installed: 1.2-1]`` or yay's ``(+1234 5.67)``.
_HEADER = re.compile(
    r"^(?P<repo>[A-Za-z0-9_.-]+)/(?P<name>[^\s/]+)\s+(?P<version>[^\s]+)(?P<rest>.*)$"
)


@dataclass
class Package:
    """One row: what it is called, where it comes from and whether it is here."""

    name: str
    version: str = ""
    repo: str = "local"
    description: str = ""
    installed: bool = False

    @property
    def aur(self) -> bool:
        return self.repo.lower() == "aur"

    @property
    def origin(self) -> str:
        if self.aur:
            return "AUR"
        return self.repo or "local"

    @property
    def category(self) -> str:
        """The shelf the window files this package under."""
        return category_of(self)

    def __str__(self) -> str:
        return ("%s %s" % (self.name, self.version)).strip()


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_search(text: str) -> list:
    """Rows from ``pacman -Ss`` or ``yay -Ss --aur``.

    Both print one header line per package and one indented description line
    underneath it, so a line that starts with whitespace belongs to the package
    before it.
    """
    found: list = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace():
            if found and not found[-1].description:
                found[-1].description = line.strip()
            continue
        match = _HEADER.match(line.strip())
        if not match:
            continue
        found.append(
            Package(
                name=match.group("name"),
                version=match.group("version"),
                repo=match.group("repo"),
                installed="[installed" in match.group("rest"),
            )
        )
    return found


def parse_installed(text: str, foreign: str = "") -> list:
    """Rows from ``pacman -Q``, with the ``pacman -Qm`` names marked as AUR."""
    aur_names = {line.split()[0] for line in foreign.splitlines() if line.split()}
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        rows.append(
            Package(
                name=parts[0],
                version=parts[1] if len(parts) > 1 else "",
                repo="aur" if parts[0] in aur_names else "repo",
                installed=True,
            )
        )
    return rows


def parse_updates(text: str) -> list:
    """Rows from ``pacman -Qu``: ``name old-version -> new-version``."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            rows.append(
                Package(name=parts[0], version=parts[-1], installed=True)
            )
    return rows


#: What the search box can look in.  Kept here so the window, the tests and
#: ``--search`` all mean the same thing by "Everything".
SCOPES = ("Everything", "Repositories", "AUR", "Installed")


def search_commands(query: str, scope: str) -> list:
    """The command lines one search needs, in the order results are shown."""
    plan = []
    if scope in ("Repositories", "Everything"):
        plan.append([PACMAN, "-Ss"] + ([query] if query else []))
    if scope in ("AUR", "Everything"):
        plan.append([YAY, "-Ss", "--aur"] + ([query] if query else []))
    return plan


def filter_installed(packages, query: str) -> list:
    """Installed packages matching ``query`` by name or description."""
    needle = (query or "").strip().lower()
    if not needle:
        return list(packages)
    return [
        package
        for package in packages
        if needle in package.name.lower() or needle in package.description.lower()
    ]


def merge(*groups) -> list:
    """Flatten the search results, keeping the first row seen for each name.

    The groups arrive in the order they are shown, so a package that exists in a
    repository *and* in the AUR is listed as the repository package: that is the
    one pacman can install, and the AUR copy would be a slower way to the same
    thing.
    """
    seen: set = set()
    rows: list = []
    for group in groups:
        for package in group:
            if package.name in seen:
                continue
            seen.add(package.name)
            rows.append(package)
    return rows


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #
#: The shelf headings the window groups results under, in the order it shows
#: them.  "Other" is last because it is what nothing else describes.
CATEGORIES = (
    "Desktop", "Development", "Games", "Graphics", "Internet",
    "Multimedia", "Office", "Security", "System", "Other",
)

#: What puts a package on a shelf.  Matched as whole words against the name and
#: the description, so a codec is not caught by "code" and a game is not caught
#: by a word that merely starts the same way.  Order is the order the shelves are
#: shown in: the first category that matches wins.
CATEGORY_WORDS = (
    ("Desktop", (
        "desktop", "window manager", "panel", "theme", "icon", "wallpaper",
        "xfce", "xfce4", "gnome", "kde", "plasma", "lxqt", "cinnamon", "mate",
        "wayland", "xorg", "screensaver", "greeter",
    )),
    ("Development", (
        "compiler", "debugger", "editor", "code", "ide", "interpreter",
        "programming", "python", "perl", "ruby", "rust", "golang", "java",
        "node", "javascript", "typescript", "sdk", "build", "cmake", "make",
        "git", "linter", "library", "framework", "parser", "vim", "emacs",
    )),
    ("Games", ("game", "games", "emulator", "steam", "nintendo", "playstation")),
    ("Graphics", (
        "graphics", "image", "photo", "drawing", "svg", "render", "font",
        "screenshot", "color", "gimp", "inkscape", "blender", "3d",
    )),
    ("Internet", (
        "browser", "web", "http", "ftp", "mail", "email", "torrent", "chat",
        "irc", "network", "vpn", "download", "ssh", "dns", "proxy",
    )),
    ("Multimedia", (
        "audio", "video", "music", "sound", "player", "codec", "streaming",
        "media", "ffmpeg", "podcast", "recorder", "volume",
    )),
    ("Office", (
        "office", "document", "spreadsheet", "calendar", "note", "pdf",
        "presentation", "accounting",
    )),
    ("Security", (
        "encryption", "password", "firewall", "security", "gpg", "antivirus",
        "crypt", "keyring", "signing", "audit",
    )),
    ("System", (
        "kernel", "boot", "systemd", "filesystem", "firmware", "driver",
        "daemon", "shell", "terminal", "partition", "disk", "storage",
        "logging", "package manager", "pacman", "archive", "compression",
        "process", "backup",
    )),
)

_CATEGORY_PATTERNS = tuple(
    (
        category,
        re.compile(
            r"\b(?:%s)\b" % "|".join(re.escape(word) for word in words), re.IGNORECASE
        ),
    )
    for category, words in CATEGORY_WORDS
)


def category_of(package) -> str:
    """Which shelf a package belongs on, from its name and description."""
    text = "%s %s" % (package.name, package.description)
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            return category
    return "Other"


def group_by_category(packages) -> list:
    """``[(category, [package, ...]), ...]`` in the order the window shows them.

    A shelf with nothing on it is left out, so the list only has headings that
    have packages under them.
    """
    shelves: dict = {name: [] for name in CATEGORIES}
    for package in packages:
        shelves[category_of(package)].append(package)
    return [(name, shelves[name]) for name in CATEGORIES if shelves[name]]


# --------------------------------------------------------------------------- #
# What this machine can do
# --------------------------------------------------------------------------- #
def has_yay() -> bool:
    return shutil.which(YAY) is not None


def elevator() -> list:
    """The prefix that asks the desktop to authorise a change.

    ``pkexec`` shows the polkit prompt; ``sudo -n`` only works when the user
    already has a valid credential cache, and is the fallback for machines
    without a polkit agent.
    """
    if shutil.which("pkexec"):
        return ["pkexec"]
    if shutil.which("sudo"):
        return ["sudo", "-n"]
    return []


def terminal() -> tuple:
    """``(program, prefix)`` of the terminal that can host an AUR build."""
    for program, prefix in TERMINALS:
        if shutil.which(program):
            return program, list(prefix)
    return "", []


def summary() -> dict:
    """What the window shows about its own abilities."""
    program, prefix = terminal()
    return {
        "pacman": shutil.which(PACMAN) or "",
        "yay": shutil.which(YAY) or "",
        "elevator": elevator(),
        "terminal": program,
        "terminal_prefix": prefix,
    }


# --------------------------------------------------------------------------- #
# Command lines
# --------------------------------------------------------------------------- #
def install_command(package: Package) -> list:
    """The command that installs one package from its own source."""
    if package.aur:
        return [YAY, "-S", "--needed", "--noconfirm", package.name]
    return [PACMAN, "-S", "--needed", "--noconfirm", package.name]


def remove_command(package: Package) -> list:
    # -Rns takes the dependencies nothing else needs and the configuration
    # files with it; leaving them behind is how a reinstall comes back broken.
    return [PACMAN, "-Rns", "--noconfirm", package.name]


def upgrade_command() -> list:
    return [PACMAN, "-Syu", "--noconfirm"]


def build_tools_command() -> list:
    return [PACMAN, "-S", "--needed", "--noconfirm"] + list(BUILD_TOOLS)


def yay_bootstrap_command(directory: str) -> list:
    """Build yay from the AUR in ``directory``, as the calling user."""
    script = (
        "set -e; cd %s || exit 1; git clone %s yay || exit 1; cd yay || exit 1; "
        "makepkg -si --noconfirm" % (shlex.quote(directory), shlex.quote(AUR_GIT_URL))
    )
    return ["bash", "-lc", script]


def terminal_command(argv) -> list:
    """``argv`` wrapped so it runs in a terminal window; empty when none exists."""
    program, prefix = terminal()
    if not program:
        return []
    return [program] + prefix + [str(part) for part in argv]


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
def stream(argv, on_line) -> int:
    """Run ``argv`` and hand every output line to ``on_line``.

    Output and errors are merged: pacman writes its progress to stdout and its
    warnings to stderr, and a log that shows only one of them is misleading.
    """
    process = subprocess.Popen(
        [str(part) for part in argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            on_line(line.rstrip("\n"))
    finally:
        if process.stdout is not None:
            process.stdout.close()
    return process.wait()


def capture(argv) -> str:
    """Standard output of ``argv``, or an empty string when it cannot run."""
    try:
        result = subprocess.run(
            [str(part) for part in argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return ""
    return result.stdout
