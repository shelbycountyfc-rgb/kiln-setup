"""The software catalogue behind the package picker.

Every name here is a package (or a package group) in the official repositories:
core, extra, or multilib for the ``lib32-`` entries.  The page can also search
the live repositories, so this list is a starting point rather than a limit.

Format: ``(category, description, needs_multilib, ((package, label, note), ...))``
"""

from __future__ import annotations

#: Packages that only exist in the multilib repository, so they can be installed
#: only when the user enabled 32-bit support.
MULTILIB_PACKAGES = ("steam", "wine")


def is_multilib_package(package: str) -> bool:
    return package.startswith("lib32-") or package in MULTILIB_PACKAGES


GROUPS = (
    (
        "Desktop & browsers",
        "What most people open first: a browser, mail, files, media.",
        False,
        (
            ("firefox", "Firefox", "Mozilla browser, in the official repos"),
            ("chromium", "Chromium", "Chrome-compatible browser"),
            ("thunderbird", "Thunderbird", "Mail, calendar and news"),
            ("nautilus", "Files (Nautilus)", "GNOME file manager"),
            ("dolphin", "Dolphin", "KDE file manager"),
            ("papers", "Papers", "Document viewer"),
            ("file-roller", "Archive Manager", "Zip and tar archives"),
            ("flameshot", "Flameshot", "Screenshot and annotation"),
            ("keepassxc", "KeePassXC", "Offline password manager"),
        ),
    ),
    (
        "Office & documents",
        "Writing, spreadsheets, PDFs.",
        False,
        (
            ("libreoffice-fresh", "LibreOffice (fresh)", "Full office suite, newest branch"),
            ("libreoffice-still", "LibreOffice (still)", "Full office suite, conservative branch"),
            ("onlyoffice-bin", "ONLYOFFICE", "Microsoft-format office suite"),
            ("okular", "Okular", "PDF and document reader"),
            ("zathura", "Zathura", "Keyboard-driven PDF viewer"),
            ("pandoc", "Pandoc", "Convert between document formats"),
            ("hunspell-en_us", "English spell check", "Dictionary for LibreOffice and friends"),
            ("tesseract", "Tesseract OCR", "Text recognition from images"),
        ),
    ),
    (
        "Internet & networking",
        "Transfers, remote access, network tools.",
        False,
        (
            ("qbittorrent", "qBittorrent", "BitTorrent client"),
            ("transmission-gtk", "Transmission", "Light BitTorrent client"),
            ("remmina", "Remmina", "Remote desktop (RDP, VNC)"),
            ("wireguard-tools", "WireGuard", "Modern VPN tools"),
            ("openvpn", "OpenVPN", "VPN client"),
            ("networkmanager-openvpn", "NetworkManager OpenVPN", "VPN in the network applet"),
            ("filezilla", "FileZilla", "FTP and SFTP client"),
            ("nmap", "nmap", "Network scanner"),
            ("wireshark-qt", "Wireshark", "Packet analyser"),
            ("openssh", "OpenSSH", "Secure shell server and client"),
        ),
    ),
    (
        "Development",
        "Compilers, editors, containers and version control.",
        False,
        (
            ("base-devel", "Build tools (base-devel)", "gcc, make, pkgconf and friends"),
            ("git", "Git", "Version control"),
            ("neovim", "Neovim", "Modal editor"),
            ("code", "Visual Studio Code", "Editor with extensions (official build)"),
            ("python-pip", "pip", "Python package installer"),
            ("nodejs", "Node.js", "JavaScript runtime"),
            ("npm", "npm", "JavaScript package manager"),
            ("rustup", "Rust (rustup)", "Rust toolchain manager"),
            ("go", "Go", "Compiler and toolchain"),
            ("jdk-openjdk", "OpenJDK", "Java development kit"),
            ("cmake", "CMake", "Build system generator"),
            ("clang", "Clang", "LLVM C/C++ compiler"),
            ("gdb", "GDB", "Debugger"),
            ("docker", "Docker", "Containers"),
            ("docker-compose", "Docker Compose", "Multi-container apps"),
            ("podman", "Podman", "Daemonless containers"),
        ),
    ),
    (
        "Multimedia",
        "Playback, editing, recording and images.",
        False,
        (
            ("vlc", "VLC", "Plays almost anything"),
            ("mpv", "mpv", "Light, high-quality player"),
            ("gimp", "GIMP", "Image editor"),
            ("inkscape", "Inkscape", "Vector drawing"),
            ("krita", "Krita", "Digital painting"),
            ("obs-studio", "OBS Studio", "Screen recording and streaming"),
            ("audacity", "Audacity", "Audio editor"),
            ("kdenlive", "Kdenlive", "Video editor"),
            ("ffmpeg", "FFmpeg", "Convert and record"),
            ("blender", "Blender", "3D modelling and animation"),
            ("darktable", "darktable", "Photo workflow"),
            ("handbrake", "HandBrake", "Video transcoder"),
        ),
    ),
    (
        "Gaming",
        "Steam, Wine and the 32-bit libraries they need.",
        True,
        (
            ("steam", "Steam", "Steam client (needs multilib)"),
            ("lutris", "Lutris", "Game launcher for Wine and emulators"),
            ("wine", "Wine", "Run Windows programs (needs multilib)"),
            ("winetricks", "Winetricks", "Helpers for Wine prefixes"),
            ("gamemode", "GameMode", "On-demand CPU/GPU performance"),
            ("mangohud", "MangoHud", "Overlay with frame rate and sensors"),
            ("gamescope", "Gamescope", "Micro-compositor for games"),
            ("lib32-mesa", "32-bit Mesa", "32-bit graphics drivers"),
            ("lib32-alsa-plugins", "32-bit ALSA plugins", "32-bit audio"),
            ("lib32-libpulse", "32-bit PulseAudio client", "32-bit audio compatibility"),
            ("lib32-vulkan-icd-loader", "32-bit Vulkan loader", "Vulkan for 32-bit games"),
        ),
    ),
    (
        "Virtualisation",
        "Run other systems in a window.",
        False,
        (
            ("qemu-full", "QEMU", "Full virtual machine emulator"),
            ("virt-manager", "virt-manager", "Graphical VM manager"),
            ("libvirt", "libvirt", "Virtualisation API used by virt-manager"),
            ("edk2-ovmf", "OVMF", "UEFI firmware for VMs"),
            ("swtpm", "swtpm", "Software TPM for Windows 11 guests"),
            ("virt-viewer", "virt-viewer", "VM display client"),
            ("virtualbox", "VirtualBox", "Oracle's virtualiser"),
            ("looking-glass", "Looking Glass", "Low-latency VM display client"),
        ),
    ),
    (
        "Fonts & appearance",
        "Typefaces, icons and themes.",
        False,
        (
            ("noto-fonts", "Noto Sans", "Broad script coverage"),
            ("noto-fonts-emoji", "Noto Emoji", "Colour emoji"),
            ("noto-fonts-cjk", "Noto CJK", "Chinese, Japanese and Korean"),
            ("ttf-dejavu", "DejaVu", "Classic desktop fonts"),
            ("ttf-liberation", "Liberation", "Metric-compatible with Arial and Times"),
            ("ttf-jetbrains-mono", "JetBrains Mono", "Programming font"),
            ("ttf-fira-code", "Fira Code", "Programming font with ligatures"),
            ("papirus-icon-theme", "Papirus icons", "Popular icon theme"),
            ("arc-gtk-theme", "Arc theme", "Clean GTK theme"),
        ),
    ),
    (
        "System & maintenance",
        "Looking after the machine.",
        False,
        (
            ("htop", "htop", "Process viewer"),
            ("btop", "btop", "Resource monitor"),
            ("fastfetch", "fastfetch", "System information"),
            ("tmux", "tmux", "Terminal multiplexer"),
            ("rsync", "rsync", "File synchronisation"),
            ("gparted", "GParted", "Partition editor"),
            ("timeshift", "Timeshift", "System snapshots"),
            ("reflector", "reflector", "Mirror ranking"),
            ("pacman-contrib", "pacman-contrib", "paccache and other helpers"),
            ("inxi", "inxi", "Hardware summary"),
            ("smartmontools", "smartmontools", "Disk health"),
            ("ufw", "ufw", "Firewall front end"),
            ("firejail", "Firejail", "Sandbox for applications"),
            ("unzip", "unzip", "Zip archives"),
            ("p7zip", "7-Zip", "7z archives"),
            ("bash-completion", "bash-completion", "Tab completion for bash"),
        ),
    ),
    (
        "Hardware support",
        "Bluetooth, printing, audio and power management.",
        False,
        (
            ("bluez", "BlueZ", "Bluetooth stack"),
            ("bluez-utils", "bluetoothctl", "Command-line Bluetooth control"),
            ("cups", "CUPS", "Printing service"),
            ("cups-pdf", "CUPS PDF", "Print to PDF"),
            ("pipewire", "PipeWire", "Modern audio server"),
            ("pipewire-pulse", "PipeWire PulseAudio", "PulseAudio compatibility"),
            ("wireplumber", "WirePlumber", "PipeWire session manager"),
            ("alsa-utils", "ALSA utilities", "alsamixer and friends"),
            ("sof-firmware", "SOF firmware", "Audio on recent Intel laptops"),
            ("tlp", "TLP", "Battery life on laptops"),
            ("thermald", "thermald", "Intel thermal management"),
            ("fwupd", "fwupd", "Firmware updates"),
            ("ntfs-3g", "NTFS-3G", "Read and write NTFS"),
            ("exfatprogs", "exFAT", "Read and write exFAT"),
            ("sane", "SANE", "Scanner support"),
            ("cups-pk-helper", "CUPS policy helper", "Print dialog integration"),
        ),
    ),
)


def categories() -> list:
    """``[(title, description, needs_multilib, items), ...]`` for the page."""
    return [(title, note, multilib, items) for title, note, multilib, items in GROUPS]


def all_packages() -> list:
    return [name for _title, _note, _m, items in GROUPS for name, _label, _detail in items]


def details(package: str) -> tuple:
    """``(label, description)`` for a catalogue package, or ``("", "")``."""
    for _title, _note, _m, items in GROUPS:
        for name, label, detail in items:
            if name == package:
                return label, detail
    return "", ""


def requires_multilib() -> set:
    """Packages that live in the multilib repository itself.

    ``steam`` and ``wine`` are published in multilib, and every ``lib32-``
    package is by definition 32-bit.  The other entries of the Gaming category
    (Lutris, GameMode, MangoHud) are in extra and install without it.
    """
    return {
        name
        for _title, _note, _m, items in GROUPS
        for name, _label, _detail in items
        if name.startswith("lib32-") or name in ("steam", "wine")
    }


def category_of(package: str) -> str:
    for title, _note, _m, items in GROUPS:
        if any(name == package for name, _label, _detail in items):
            return title
    return "Search results"
