"""The wizard pages.

Each page builds its widgets in :meth:`Page.build`, writes the answers back to
``app.form`` in :meth:`Page.collect` and reports any problem in
:meth:`Page.errors`.  The window validates the collected form once more through
:class:`installer.config.InstallConfig` before the engine starts.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from .. import catalog, disk as disk_module, options, packages, system
from ..config import InstallConfig
from ..context import human_bytes
from ..disk import MIN_DISK_BYTES, recommended_swap
from ..live_network import LiveNetwork
from .widgets import (
    Banner,
    Card,
    Form,
    Notice,
    OptionCard,
    Pill,
    ScrollArea,
    bullets,
    fit_labels,
    hint,
)

GIB = 1024 ** 3


class Page(tk.Frame):
    name = ""
    title = ""
    subtitle = ""
    footer_note = ""
    next_text = "Next"
    show_back = True
    rail_key = ""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=app.theme.panel)
        self.app = app
        self.theme = app.theme
        self._error = Banner(self, self.theme)
        self._error.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self._error.grid_remove()
        self.area = ScrollArea(self, self.theme)
        self.area.grid(row=1, column=0, sticky="nsew")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.build(self.area.body)
        fit_labels(self)

    # -- overridable -------------------------------------------------------
    def build(self, body: tk.Misc) -> None:
        raise NotImplementedError

    def enter(self) -> None:
        pass

    def leave(self) -> None:
        pass

    def collect(self) -> None:
        pass

    def errors(self) -> list:
        return []

    # -- messages ----------------------------------------------------------
    def show_errors(self, problems) -> None:
        if isinstance(problems, str):
            problems = [problems]
        self._error.show("\n".join("\u2022  " + problem for problem in problems))

    def clear_errors(self) -> None:
        self._error.grid_remove()


# --------------------------------------------------------------------------- #
# Welcome
# --------------------------------------------------------------------------- #
class WelcomePage(Page):
    name = "welcome"
    title = "Install Arch Linux"
    subtitle = "This takes about 10 to 25 minutes and keeps your answers on every page."
    footer_note = "Everything can still be changed on the following pages."
    next_text = "Get started"
    show_back = False

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        tk.Label(
            body,
            text=(
                "The installer partitions the disk, installs Arch Linux from the official "
                "repositories and configures the boot loader. It follows the steps of the "
                "official installation guide, so you end up with a normal Arch system that "
                "you can maintain with pacman."
            ),
            bg=theme.panel, fg=theme.muted, font=theme.body, anchor="w",
            justify="left", wraplength=700,
        ).pack(fill="x", pady=(0, 20))

        card = Card(body, theme, "What this installation does", "Seven steps, then about 10 to 25 minutes of work.")
        card.pack(fill="x", pady=(0, 16))
        bullets(
            card.body, theme,
            (
                "Partitions and formats the disk you choose, with an EFI system partition, swap and an optional separate /home.",
                "Installs the base system and the kernel you pick with pacstrap, then configures locale, keyboard, hostname and accounts.",
                "Installs a desktop (KDE Plasma, GNOME, Xfce, Cinnamon, MATE, LXQt or Hyprland) and any extra software you select by category.",
                "Turns on the multilib repository when you want 32-bit software such as Steam or Wine.",
                "Installs systemd-boot or GRUB and enables networking, so the machine boots into a working system.",
            ),
        )

        summary = Card(body, theme, "This computer")
        summary.pack(fill="x", pady=(0, 16))
        hw = self.app.hardware
        rows = [
            ("Processor", "%s (%d cores, %d threads)" % (hw.cpu_model, hw.cpu_cores, hw.cpu_threads)),
            ("Memory", human_bytes(hw.ram_bytes)),
            ("Firmware", hw.firmware.upper() + (", Secure Boot enabled" if hw.secure_boot else "")),
            ("Graphics", ", ".join(g.to_dict()["vendor"] for g in hw.gpus) or "not detected"),
            ("Disks", ", ".join("%s (%s)" % (d.device, d.to_dict()["sizeText"]) for d in hw.disks) or "none found"),
        ]
        for label, value in rows:
            row = tk.Frame(summary.body, bg=theme.panel)
            row.pack(fill="x", pady=(0, 6))
            tk.Label(
                row, text=label, bg=theme.panel, fg=theme.muted, font=theme.small,
                width=14, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=value, bg=theme.panel, fg=theme.ink_soft, font=theme.body,
                anchor="w", justify="left", wraplength=520,
            ).pack(side="left", fill="x", expand=True)

        if not self.app.hardware.disks:
            Notice(
                body, theme,
                "No hard disk was detected. Check the controller mode in the firmware settings "
                "and start the installer again.",
                kind="warn",
            ).pack(fill="x", pady=(0, 12))

        Notice(
            body, theme,
            "Installing Arch Linux erases the disk you select. Back up anything you want to "
            "keep before you continue. The installation also needs a working internet "
            "connection: the packages are downloaded from the Arch mirrors.",
            kind="warn",
        ).pack(fill="x")


# --------------------------------------------------------------------------- #
# Hardware
# --------------------------------------------------------------------------- #
class HardwarePage(Page):
    name = "hardware"
    title = "This computer"
    subtitle = "Detected from the machine the installer is running on."
    footer_note = "The kernel, firmware and microcode are chosen to match this hardware."
    rail_key = "hardware"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        hw = self.app.hardware

        specs = Card(body, theme, "System", "Read from /proc and /sys on the live medium.")
        specs.pack(fill="x", pady=(0, 16))
        rows = [
            ("Processor", hw.cpu_model),
            ("Vendor", hw.cpu_vendor_label),
            ("Cores / threads", "%d / %d" % (hw.cpu_cores, hw.cpu_threads)),
            ("Memory", human_bytes(hw.ram_bytes)),
            ("Firmware", hw.firmware.upper() + (" (Secure Boot enabled)" if hw.secure_boot else "")),
            ("Microcode", hw.microcode or "none needed"),
            ("Graphics", ", ".join("%s %s" % (g.vendor, g.device) for g in hw.gpus) or "not detected"),
            ("Network", ", ".join(hw.network) or "not detected"),
            ("Audio", ", ".join(hw.audio) or "not detected"),
        ]
        for label, value in rows:
            row = tk.Frame(specs.body, bg=theme.panel)
            row.pack(fill="x", pady=(0, 6))
            tk.Label(
                row, text=label, bg=theme.panel, fg=theme.muted, font=theme.small,
                width=16, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=value, bg=theme.panel, fg=theme.ink_soft, font=theme.body,
                anchor="w", justify="left", wraplength=520,
            ).pack(side="left", fill="x", expand=True)

        disks = Card(body, theme, "Storage", "Removable media are listed but not offered as a target.")
        disks.pack(fill="x", pady=(0, 16))
        if not hw.disks:
            hint(disks.body, theme, "No disks found.").pack(fill="x")
        for found in hw.disks:
            row = tk.Frame(disks.body, bg=theme.panel)
            row.pack(fill="x", pady=(0, 8))
            tk.Label(
                row, text=found.device, bg=theme.panel, fg=theme.ink, font=theme.body_bold,
                width=18, anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text="%s  %s%s" % (
                    found.to_dict()["sizeText"],
                    found.model or found.name,
                    "   (removable)" if found.removable else "",
                ),
                bg=theme.panel, fg=theme.ink_soft, font=theme.body, anchor="w",
            ).pack(side="left")
            if found.partitions:
                for partition in found.partitions:
                    tk.Label(
                        disks.body,
                        text="      %s  %s  %s %s" % (
                            partition.device, partition.to_dict()["sizeText"],
                            partition.fstype or "unknown", partition.mountpoint,
                        ),
                        bg=theme.panel, fg=theme.muted, font=theme.small, anchor="w",
                    ).pack(fill="x")

        Notice(
            body, theme,
            "The live medium does not support Secure Boot. If it is enabled, the machine had "
            "to have it turned off to reach this screen; you can enrol your own keys after "
            "the installation.",
            kind="info",
        ).pack(fill="x")


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #
class NetworkPage(Page):
    name = "network"
    title = "Connect to the internet"
    subtitle = "The packages are downloaded while installing, so this computer has to be online."
    footer_note = "The Wi-Fi profile is saved in the new system as well."
    rail_key = "network"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        form_data = self.app.form
        self.net = LiveNetwork(self.app.ctx)
        self._networks: dict = {}

        state = Card(body, theme, "This computer")
        state.pack(fill="x", pady=(0, 16))
        self.state_label = tk.Label(
            state.body, text="Checking the connection\u2026", bg=theme.panel, fg=theme.muted,
            font=theme.body, anchor="w", justify="left", wraplength=640,
        )
        self.state_label.pack(fill="x")

        wifi = Card(
            body, theme, "Wi-Fi",
            "Pick the network this machine uses. A cable is used automatically when one is plugged in.",
        )
        wifi.pack(fill="x")
        form = Form(wifi.body, theme)
        form.pack(fill="x")

        holder = form.field("Network", "Press Scan, then choose the name of your network.")
        self.network_var = tk.StringVar()
        self.network_box = ttk.Combobox(
            holder, textvariable=self.network_var, values=[], width=32, state="readonly"
        )
        self.network_box.pack(side="left")
        ttk.Button(holder, text="Scan", style="Secondary.TButton", command=self.scan).pack(
            side="left", padx=(8, 0)
        )

        holder = form.field("Password", "8 to 63 characters. Leave empty for an open network.")
        self.wifi_password = tk.StringVar()
        ttk.Entry(holder, textvariable=self.wifi_password, show="\u2022", width=32).pack(side="left")

        holder = form.field("Connection")
        ttk.Button(holder, text="Connect", style="Accent.TButton", command=self.connect).pack(side="left")
        self.connect_label = tk.Label(
            holder, text="", bg=theme.panel, fg=theme.muted, font=theme.small, anchor="w",
            justify="left", wraplength=380,
        )
        self.connect_label.pack(side="left", padx=(12, 0))

        self.saved_ssid = tk.StringVar(value=form_data.get("wifiSsid", ""))

    # -- actions -----------------------------------------------------------
    def _say(self, text: str) -> None:
        self.connect_label.configure(text=text)

    def scan(self) -> None:
        if not self.net.usable:
            self._say("NetworkManager is not available on this medium.")
            return
        devices = self.net.wifi_devices()
        if not devices:
            self._say("No wireless adapter was found. A cable is used instead.")
            self._refresh_state()
            return
        self._say("Scanning %s\u2026" % devices[0])
        self.update_idletasks()
        found = self.net.scan(devices[0])
        self._networks = {network.label: network for network in found}
        self.network_box.configure(values=list(self._networks))
        if self._networks:
            if self.network_var.get() not in self._networks:
                self.network_var.set(next(iter(self._networks)))
            self._say("%d network(s) in range. Choose one and press Connect." % len(self._networks))
        else:
            self._say("No networks in range yet. Move closer and press Scan again.")
        self._refresh_state()

    def connect(self) -> None:
        network = self._networks.get(self.network_var.get())
        if network is None:
            self._say("Press Scan first, then choose a network.")
            return
        devices = self.net.wifi_devices()
        if not devices:
            self._say("No wireless adapter was found.")
            return
        self._say("Connecting to %s\u2026" % network.ssid)
        self.update_idletasks()
        ok, problem = self.net.connect(devices[0], network.ssid, self.wifi_password.get())
        if ok:
            self.saved_ssid.set(network.ssid)
            self._say("Connected to %s." % network.ssid)
        else:
            self._say("Not connected: %s" % problem)
        self._refresh_state()

    def _refresh_state(self) -> None:
        if self.net.ctx.dry_run:
            self.state_label.configure(text="Simulation: the network is not touched.")
            return
        device = self.net.connected_device()
        if device and self.net.online():
            self.state_label.configure(
                text="Online through %s. The packages can be downloaded." % device
            )
        elif device:
            self.state_label.configure(
                text="%s is connected, but no address came back yet." % device
            )
        else:
            self.state_label.configure(
                text="Not connected. A cable is used automatically when one is plugged in; "
                "otherwise choose a Wi-Fi network below."
            )

    def enter(self) -> None:
        self._refresh_state()
        if self.net.usable and not self._networks:
            try:
                self.scan()
            except Exception as error:  # nmcli can fail in a dozen harmless ways
                self._say("Scanning failed: %s" % error)

    def collect(self) -> None:
        form = self.app.form
        chosen = self.saved_ssid.get().strip()
        form["wifiSsid"] = chosen
        form["wifiPassword"] = self.wifi_password.get() if chosen else ""

    def errors(self) -> list:
        if self.net.ctx.dry_run or self.net.online():
            return []
        return [
            "The installation downloads its packages, so this computer has to be online: "
            "choose a Wi-Fi network and press Connect."
        ]


# --------------------------------------------------------------------------- #
# Disk
# --------------------------------------------------------------------------- #
class DiskPage(Page):
    name = "disk"
    title = "Where do you want to install Arch Linux?"
    subtitle = "The selected disk is erased completely: every partition on it will be removed."
    footer_note = "Only whole-disk installation is offered by this version."
    rail_key = "disk"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        hw = self.app.hardware
        candidates = hw.internal_disks() or list(hw.disks)

        card = Card(body, theme, "Disk", "Internal disks are offered; removable media are not.")
        card.pack(fill="x", pady=(0, 16))
        if not candidates:
            Notice(
                card.body, theme,
                "No disk is available to install on. Connect a disk and start again.",
                kind="warn",
            ).pack(fill="x")
        self._cards = []
        for found in candidates:
            subtitle = "%s   %s%s" % (
                found.to_dict()["sizeText"],
                found.model or found.name,
                "   \u00b7 %d existing partitions" % len(found.partitions) if found.partitions else "",
            )
            item = OptionCard(
                card.body, theme, found.device, subtitle, found.device,
                self.app.form_var("disk"), command=self._refresh_preview,
            )
            item.pack(fill="x", pady=(0, 8))
            self._cards.append(item)

        layout = Card(body, theme, "Layout", "The example layout from the installation guide.")
        layout.pack(fill="x", pady=(0, 16))
        form = Form(layout.body, theme)
        form.pack(fill="x")

        holder = form.field("File system", "ext4 is the safe default; btrfs adds snapshots; XFS is fast for large files.")
        self.rootfs = tk.StringVar(value=self.app.form["rootFs"])
        ttk.Combobox(
            holder, textvariable=self.rootfs, values=list(disk_module.FILESYSTEMS),
            state="readonly", width=16,
        ).pack(side="left")
        self.rootfs.trace_add("write", lambda *_: self._refresh_preview())

        holder = form.field("Swap", "Swap lets the machine hibernate and survives a full memory.")
        self.swap = tk.BooleanVar(value=self.app.form["swap"])
        ttk.Checkbutton(
            holder, text="Create a swap partition", variable=self.swap,
            style="Panel.TCheckbutton", command=self._refresh_preview,
        ).pack(anchor="w")
        self.swapgb = tk.StringVar(value="%.1f" % (self.app.form["swapGb"] or 8.0))
        ttk.Spinbox(
            holder, textvariable=self.swapgb, from_=0, to=2048, increment=0.5,
            width=8, command=self._refresh_preview,
        ).pack(anchor="w", pady=(4, 0))
        self.swapgb.trace_add("write", lambda *_: self._refresh_preview())

        holder = form.field("Separate /home", "Keeps documents on their own partition, so a reinstall can leave them alone.")
        self.home = tk.BooleanVar(value=self.app.form["separateHome"])
        ttk.Checkbutton(
            holder, text="Create a separate /home partition", variable=self.home,
            style="Panel.TCheckbutton", command=self._refresh_preview,
        ).pack(anchor="w")

        holder = form.field("EFI partition", "1 GiB is enough for several kernels and systemd-boot.")
        self.esp = tk.StringVar(value="%.1f" % self.app.form["espGb"])
        ttk.Combobox(
            holder, textvariable=self.esp, values=["0.5", "1.0", "2.0"], width=8, state="readonly",
        ).pack(side="left")
        self.esp.trace_add("write", lambda *_: self._refresh_preview())

        preview = Card(body, theme, "What will be created")
        preview.pack(fill="x", pady=(0, 16))
        self._preview = tk.Frame(preview.body, bg=theme.panel)
        self._preview.pack(fill="x")
        self._refresh_preview()

        danger = Card(body, theme, "Confirm", "This cannot be undone.")
        danger.pack(fill="x")
        self.confirm = tk.BooleanVar(value=self.app.form["confirmErase"])
        ttk.Checkbutton(
            danger.body,
            text="I understand that every partition and file on this disk will be destroyed.",
            variable=self.confirm, style="Panel.TCheckbutton",
        ).pack(anchor="w")

    def _refresh_preview(self) -> None:
        theme = self.theme
        for child in self._preview.winfo_children():
            child.destroy()
        device = self.app.form_var("disk").get()
        found = disk_module.find_disk(self.app.hardware, device)
        if found is None:
            hint(self._preview, theme, "Select a disk to see the layout.").pack(fill="x")
            return
        try:
            swap_gb = float(self.swapgb.get() or 0)
        except ValueError:
            swap_gb = 0.0
        try:
            esp_gb = float(self.esp.get() or 1)
        except ValueError:
            esp_gb = 1.0
        plan = disk_module.plan_layout(
            device, found.size, efi=self.app.form["efi"], root_fs=self.rootfs.get(),
            swap=self.swap.get(), swap_gb=swap_gb or None, esp_gb=esp_gb,
            separate_home=self.home.get(), ram_bytes=self.app.hardware.ram_bytes,
        )
        header = tk.Frame(self._preview, bg=theme.panel)
        header.pack(fill="x", pady=(0, 6))
        for text, width in (("PARTITION", 16), ("ROLE", 8), ("FILE SYSTEM", 14), ("SIZE", 12), ("MOUNT", 10)):
            tk.Label(
                header, text=text, bg=theme.panel, fg=theme.faint, font=theme.small_bold,
                width=width, anchor="w",
            ).pack(side="left")
        for name, role, fs, size, mountpoint in plan.summary():
            row = tk.Frame(self._preview, bg=theme.panel)
            row.pack(fill="x", pady=(0, 4))
            for text, width in (
                (name, 16), (str(role), 8), (fs, 14), (size, 12), (mountpoint or "", 10),
            ):
                tk.Label(
                    row, text=text, bg=theme.panel, fg=theme.ink_soft, font=theme.body,
                    width=width, anchor="w",
                ).pack(side="left")
        if found.size < MIN_DISK_BYTES:
            Notice(
                self._preview, theme,
                "This disk is smaller than 20 GiB, which is too small for a desktop installation.",
                kind="warn",
            ).pack(fill="x", pady=(8, 0))
        if found.partitions:
            Notice(
                self._preview, theme,
                "%d existing partition(s) on %s will be deleted, including any other operating "
                "system and its boot loader." % (len(found.partitions), device),
                kind="warn",
            ).pack(fill="x", pady=(8, 0))

    def enter(self) -> None:
        for card in self._cards:
            card.refresh()
        self._refresh_preview()

    def collect(self) -> None:
        form = self.app.form
        form["rootFs"] = self.rootfs.get()
        form["swap"] = bool(self.swap.get())
        try:
            form["swapGb"] = float(self.swapgb.get())
        except ValueError:
            form["swapGb"] = None
        form["separateHome"] = bool(self.home.get())
        form["espGb"] = float(self.esp.get() or 1)
        form["confirmErase"] = bool(self.confirm.get())

    def errors(self) -> list:
        self.collect()
        found = disk_module.find_disk(self.app.hardware, self.app.form["disk"])
        size = found.size if found else 0
        try:
            config = InstallConfig.from_dict(self.app.form)
        except (TypeError, ValueError) as error:
            return ["The answers could not be read: %s" % error]
        return config.disk_errors(disk_size=size)


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
class SystemPage(Page):
    name = "system"
    title = "Name, language and accounts"
    subtitle = "These settings are written into the installed system."
    footer_note = "The Wi-Fi password is only used for the profile saved in the new system."
    rail_key = "system"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        form_data = self.app.form

        # The accounts card is first on purpose: the sheet is around 560 pixels
        # tall, so a required password that starts below the fold reads as if the
        # installer never asks for one.
        accounts = Card(
            body, theme, "Accounts",
            "The root password is required; a daily account is strongly recommended.",
        )
        accounts.pack(fill="x", pady=(0, 16))
        form = Form(accounts.body, theme)
        form.pack(fill="x")

        holder = form.field("Root password", "At least 8 characters.")
        self.root_password = tk.StringVar()
        ttk.Entry(holder, textvariable=self.root_password, show="\u2022", width=32).pack(side="left")

        holder = form.field("Repeat password")
        self.root_password2 = tk.StringVar()
        ttk.Entry(holder, textvariable=self.root_password2, show="\u2022", width=32).pack(side="left")

        holder = form.field("Your name", "The daily account, added to wheel, audio, video and storage.")
        self.username = tk.StringVar(value=(form_data.get("user") or {}).get("name", ""))
        ttk.Entry(holder, textvariable=self.username, width=32).pack(side="left")

        holder = form.field("Password")
        self.user_password = tk.StringVar()
        ttk.Entry(holder, textvariable=self.user_password, show="\u2022", width=32).pack(side="left")

        holder = form.field("Repeat password")
        self.user_password2 = tk.StringVar()
        ttk.Entry(holder, textvariable=self.user_password2, show="\u2022", width=32).pack(side="left")

        computer = Card(body, theme, "Computer")
        computer.pack(fill="x", pady=(0, 16))
        form = Form(computer.body, theme)
        form.pack(fill="x")

        holder = form.field("Computer name", "Letters, digits and hyphens; no spaces.")
        self.hostname = tk.StringVar(value=form_data["hostname"])
        ttk.Entry(holder, textvariable=self.hostname, width=32).pack(side="left")

        holder = form.field("Time zone", "Start typing to filter the list.")
        self.timezone = tk.StringVar(value=form_data["timezone"])
        ttk.Combobox(holder, textvariable=self.timezone, values=options.TIMEZONES, width=34).pack(side="left")

        holder = form.field("Language", "Locale for messages, dates and numbers.")
        self.locale = tk.StringVar(value=form_data["locale"])
        ttk.Combobox(holder, textvariable=self.locale, values=list(options.LOCALES), width=24).pack(side="left")

        holder = form.field("Keyboard", "Console and desktop layout.")
        self.keymap = tk.StringVar(value=form_data["keymap"])
        ttk.Combobox(holder, textvariable=self.keymap, values=list(options.KEYMAPS), width=24).pack(side="left")

        holder = form.field("Boot loader", options.BOOTLOADERS[form_data["bootloader"]][1])
        labels = {key: value[0] for key, value in options.BOOTLOADERS.items()}
        self.bootloader_label = tk.StringVar(value=labels.get(form_data["bootloader"], "Choose for me"))
        combo = ttk.Combobox(
            holder, textvariable=self.bootloader_label, values=[value[0] for value in options.BOOTLOADERS.values()],
            state="readonly", width=24,
        )
        combo.pack(side="left")
        self._bootloader_labels = labels

        # The Wi-Fi answers live on the Network page: that is where the medium
        # itself is connected, and the profile written into the new system is
        # the one that was actually used.

    def enter(self) -> None:
        pass

    def collect(self) -> None:
        form = self.app.form
        form["hostname"] = self.hostname.get().strip()
        form["timezone"] = self.timezone.get().strip()
        form["locale"] = self.locale.get().strip()
        form["keymap"] = self.keymap.get().strip()
        reverse = {label: key for key, label in self._bootloader_labels.items()}
        form["bootloader"] = reverse.get(self.bootloader_label.get(), "auto")
        form["rootPassword"] = self.root_password.get()
        name = self.username.get().strip()
        if name:
            form["user"] = {
                "name": name,
                "password": self.user_password.get(),
                "groups": list(InstallConfig().user.groups) if InstallConfig().user else ["wheel"],
            }
        else:
            form["user"] = None

    def errors(self) -> list:
        form = self.app.form
        problems = []
        try:
            config = InstallConfig.from_dict(form)
        except (TypeError, ValueError) as error:
            problems.append("The answers could not be read: %s" % error)
        else:
            problems.extend(
                problem for problem in config.system_errors() if problem not in problems
            )
        if self.root_password.get() != self.root_password2.get():
            problems.append("The two root passwords do not match.")
        if self.username.get().strip():
            if self.user_password.get() != self.user_password2.get():
                problems.append("The two user passwords do not match.")
        return problems


# --------------------------------------------------------------------------- #
# Software
# --------------------------------------------------------------------------- #
class SoftwarePage(Page):
    name = "software"
    title = "Choose what to install"
    subtitle = "A desktop, the kernel, 32-bit support and any extra packages."
    footer_note = "Packages come from the official repositories; the AUR is not used."
    rail_key = "software"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        form_data = self.app.form

        desktop_card = Card(body, theme, "Desktop", "Every option includes sound, fonts and a display manager.")
        desktop_card.pack(fill="x", pady=(0, 16))
        grid = tk.Frame(desktop_card.body, bg=theme.panel)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self.desktop_var = self.app.form_var("desktop")
        self._desktop_cards = []
        for index, (key, (label, description)) in enumerate(options.DESKTOPS.items()):
            card = OptionCard(
                grid, theme, label, description, key, self.desktop_var,
                command=self._refresh_cards, width=320,
            )
            card.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 8), pady=(0, 8))
            self._desktop_cards.append(card)

        kernel_card = Card(body, theme, "Kernel and hardware support")
        kernel_card.pack(fill="x", pady=(0, 16))
        form = Form(kernel_card.body, theme)
        form.pack(fill="x")
        holder = form.field("Kernel", "linux is the current stable kernel.")
        self.kernel = tk.StringVar(value=form_data["kernel"])
        ttk.Combobox(
            holder, textvariable=self.kernel, values=list(options.KERNELS), state="readonly", width=20,
        ).pack(side="left")

        holder = form.field("Microcode", "CPU bug and security fixes, loaded at boot.")
        microcode_labels = {key: value[0] for key, value in options.MICROCODE.items()}
        self.microcode_label = tk.StringVar(
            value=microcode_labels.get(form_data["microcode"], microcode_labels["auto"])
        )
        self._microcode_labels = microcode_labels
        ttk.Combobox(
            holder, textvariable=self.microcode_label,
            values=list(microcode_labels.values()), state="readonly", width=24,
        ).pack(side="left")

        multilib_card = Card(
            body, theme, "32-bit software (multilib)",
            "Arch installs 64-bit only by default. Turning this on adds the [multilib] "
            "repository and the 32-bit libraries Steam and Wine need.",
        )
        multilib_card.pack(fill="x", pady=(0, 16))
        self.multilib = tk.BooleanVar(value=form_data["multilib"])
        ttk.Checkbutton(
            multilib_card.body,
            text="Enable the multilib repository and install 32-bit libraries",
            variable=self.multilib, style="Panel.TCheckbutton",
            command=self._refresh_selection_count,
        ).pack(anchor="w")

        picker = Card(
            body, theme, "Packages",
            "Pick a category, then click a package to add or remove it. "
            "Anything you add here is installed with the base system.",
        )
        picker.pack(fill="x")
        self._selected = set(form_data["packages"])

        top = tk.Frame(picker.body, bg=theme.panel)
        top.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.search_var, width=34).pack(side="left")
        ttk.Button(
            top, text="Search repositories", style="Secondary.TButton", command=self._search,
        ).pack(side="left", padx=(8, 0))
        self.count_pill = Pill(top, theme, "", "info")
        self.count_pill.pack(side="right")

        panes = tk.Frame(picker.body, bg=theme.panel)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, minsize=190)
        panes.columnconfigure(1, weight=1)

        self.category_list = tk.Listbox(
            panes, height=11, width=26, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.canvas, fg=theme.ink,
            selectbackground=theme.accent_soft, selectforeground=theme.ink,
            font=theme.body, exportselection=False,
        )
        self.category_list.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 4))
        self.category_list.bind("<<ListboxSelect>>", self._category_changed)

        self.package_list = tk.Listbox(
            panes, height=11, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.panel, fg=theme.ink_soft,
            selectbackground=theme.accent_soft, selectforeground=theme.ink,
            font=theme.body, exportselection=False,
        )
        self.package_list.grid(row=0, column=1, sticky="nsew")
        self.package_list.bind("<ButtonRelease-1>", self._package_clicked)

        self.picker_note = hint(picker.body, theme, "")
        self.picker_note.pack(fill="x", pady=(6, 0))
        ttk.Button(
            picker.body, text="Clear all extra packages", style="Ghost.TButton",
            command=self._clear_selection,
        ).pack(anchor="w", pady=(6, 0))

        self._categories = list(catalog.categories())
        self._view: list = []
        self._load_categories()
        self._refresh_selection_count()

    # -- catalogue ---------------------------------------------------------
    def _load_categories(self) -> None:
        self.category_list.delete(0, "end")
        for title, _note, multilib, items in self._categories:
            suffix = "  (32-bit)" if multilib else ""
            self.category_list.insert("end", title + suffix)
        if self._categories:
            self.category_list.selection_set(0)
            self._category_changed()

    def _category_changed(self, _event=None) -> None:
        selection = self.category_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self._categories):
            return
        title, note, multilib, items = self._categories[index]
        self._view = [name for name, _label, _detail in items]
        self._note = "%s  \u00b7  %s" % (title, note)
        if multilib and not self.multilib.get():
            self._note += "   Turn on multilib to install these."
        self.picker_note.configure(text=self._note)
        self._refresh_package_list()

    def _refresh_package_list(self) -> None:
        self.package_list.delete(0, "end")
        for name in self._view:
            label, detail = catalog.details(name)
            mark = "[x]" if name in self._selected else "[ ]"
            self.package_list.insert("end", "  %s  %-24s %s" % (mark, name, label or detail))

    def _package_clicked(self, event: tk.Event) -> None:
        index = self.package_list.nearest(event.y)
        if index < 0 or index >= len(self._view):
            return
        name = self._view[index]
        if name in self._selected:
            self._selected.discard(name)
        else:
            if catalog.is_multilib_package(name) and not self.multilib.get():
                self.show_errors(
                    ["%s needs 32-bit libraries. Turn on multilib above first." % name]
                )
                return
            self._selected.add(name)
        self.clear_errors()
        self._refresh_package_list()
        self._refresh_selection_count()

    def _clear_selection(self) -> None:
        self._selected.clear()
        self._refresh_package_list()
        self._refresh_selection_count()

    def _refresh_selection_count(self) -> None:
        count = len(self._selected)
        total = count + len(packages.required_packages(
            InstallConfig.from_dict(self.app.form), self.app.hardware
        ))
        self.count_pill.configure(text="%d extra \u00b7 about %d packages" % (count, total))

    # -- repository search -------------------------------------------------
    def _search(self) -> None:
        term = self.search_var.get().strip()
        if not term:
            return
        self.picker_note.configure(text="Searching the official repositories for %r\u2026" % term)
        thread = threading.Thread(target=self._search_worker, args=(term,), daemon=True)
        thread.start()

    def _search_worker(self, term: str) -> None:
        results = packages.search(self.app.search_ctx, term)
        try:
            self.after(0, self._show_results, term, results)
        except (tk.TclError, RuntimeError):
            pass

    def _show_results(self, term: str, results: list) -> None:
        if not results:
            self.picker_note.configure(
                text="No package matched %r. The search needs the live medium and a working "
                "internet connection." % term
            )
            return
        items = tuple((row["name"], "%s %s" % (row["repo"], row["version"]), row["description"]) for row in results)
        self._categories.append(("Search: %s" % term, "%d matches" % len(items), False, items))
        self.category_list.insert("end", "Search: %s" % term)
        self.category_list.selection_clear(0, "end")
        self.category_list.selection_set("end")
        self._category_changed()

    # -- form --------------------------------------------------------------
    def enter(self) -> None:
        self._refresh_cards()
        self._refresh_package_list()
        self._refresh_selection_count()

    def _refresh_cards(self) -> None:
        for card in self._desktop_cards:
            card.refresh()
        self._refresh_selection_count()

    def collect(self) -> None:
        form = self.app.form
        form["kernel"] = self.kernel.get()
        form["multilib"] = bool(self.multilib.get())
        form["desktop"] = self.desktop_var.get()
        reverse = {label: key for key, label in self._microcode_labels.items()}
        form["microcode"] = reverse.get(self.microcode_label.get(), "auto")
        form["packages"] = sorted(self._selected)

    def errors(self) -> list:
        problems = []
        if not self.multilib.get():
            blocked = [p for p in self._selected if catalog.is_multilib_package(p)]
            if blocked:
                problems.append(
                    "%s need(s) multilib, which is turned off." % ", ".join(sorted(blocked))
                )
        if not self.app.hardware.disks:
            problems.append("No disk was detected, so there is nothing to install on.")
        try:
            config = InstallConfig.from_dict(self.app.form)
        except (TypeError, ValueError) as error:
            problems.append("The answers could not be read: %s" % error)
        else:
            problems.extend(
                problem for problem in config.software_errors() if problem not in problems
            )
        return problems


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #
class SummaryPage(Page):
    name = "summary"
    title = "Ready to install"
    subtitle = "Check the choices below. The next step erases the disk and installs the system."
    footer_note = "Press Install to begin. The progress and the full log are shown on the next page."
    next_text = "Install"
    rail_key = "summary"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        self._container = tk.Frame(body, bg=theme.panel)
        self._container.pack(fill="x")

    def enter(self) -> None:
        theme = self.theme
        for child in self._container.winfo_children():
            child.destroy()
        form = self.app.form
        card = Card(self._container, theme, "Your choices")
        card.pack(fill="x", pady=(0, 16))
        try:
            config = InstallConfig.from_dict(form)
        except (TypeError, ValueError):
            config = InstallConfig()
        for label, value in system.summary(config, self.app.hardware):
            row = tk.Frame(card.body, bg=theme.panel)
            row.pack(fill="x", pady=(0, 6))
            tk.Label(
                row, text=label, bg=theme.panel, fg=theme.muted, font=theme.small,
                width=16, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=str(value), bg=theme.panel, fg=theme.ink_soft, font=theme.body,
                anchor="w", justify="left", wraplength=520,
            ).pack(side="left", fill="x", expand=True)

        found = disk_module.find_disk(self.app.hardware, config.disk)
        if found is not None:
            plan = disk_module.plan_layout(
                config.disk, found.size, efi=config.efi, root_fs=config.root_fs,
                swap=config.swap, swap_gb=config.swap_gb, esp_gb=config.esp_gb,
                separate_home=config.separate_home, ram_bytes=self.app.hardware.ram_bytes,
            )
            layout = Card(self._container, theme, "Partitions on %s" % config.disk)
            layout.pack(fill="x", pady=(0, 16))
            for name, role, fs, size, mountpoint in plan.summary():
                tk.Label(
                    layout.body,
                    text="%-14s %-7s %-8s %-10s %s" % (name, role, fs, size, mountpoint),
                    bg=theme.panel, fg=theme.ink_soft, font=theme.mono, anchor="w",
                ).pack(fill="x")

        chosen = Card(
            self._container, theme, "Software",
            "%d extra package(s) beyond the desktop and base system." % len(config.packages),
        )
        chosen.pack(fill="x", pady=(0, 16))
        if config.packages:
            for name, label, note in packages.selected_details(config.packages):
                tk.Label(
                    chosen.body, text="%s  \u00b7  %s" % (name, label),
                    bg=theme.panel, fg=theme.ink_soft, font=theme.body, anchor="w",
                ).pack(fill="x", pady=(0, 2))
        else:
            hint(chosen.body, theme, "No extra packages were selected.").pack(fill="x")

        Notice(
            self._container, theme,
            "The installation cannot be cancelled once the disk has been partitioned. "
            "Keep the machine connected to power and to the network.",
            kind="warn",
        ).pack(fill="x")


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #
class ProgressPage(Page):
    name = "progress"
    title = "Installing Arch Linux"
    subtitle = "The installer is running the steps of the installation guide."
    footer_note = "This can take a while: the packages are downloaded and unpacked."
    next_text = "Next"
    show_back = False
    rail_key = "install"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        card = Card(body, theme, "Progress")
        card.pack(fill="x", pady=(0, 16))
        self.bar = ttk.Progressbar(
            card.body, style="Accent.Horizontal.TProgressbar", mode="determinate", maximum=100,
        )
        self.bar.pack(fill="x", pady=(0, 10))
        self.percent_label = tk.Label(
            card.body, text="Waiting to start", bg=theme.panel, fg=theme.ink_soft,
            font=theme.body_bold, anchor="w",
        )
        self.percent_label.pack(fill="x")
        self.detail_label = tk.Label(
            card.body, text="", bg=theme.panel, fg=theme.muted, font=theme.small,
            anchor="w", justify="left", wraplength=660,
        )
        self.detail_label.pack(fill="x", pady=(4, 0))

        phases = Card(body, theme, "Steps")
        phases.pack(fill="x", pady=(0, 16))
        self.tree = ttk.Treeview(
            phases.body, columns=("state",), show="tree", height=11, selectmode="none",
        )
        self.tree.column("#0", width=520)
        self.tree.pack(fill="x")
        from ..orchestrator import PHASES

        self._phase_ids = []
        self._phase_labels = {}
        for pid, label, _weight in PHASES:
            # The label is kept here, not read back out of the row: rebuilding the
            # text from the previous text drifted the names to the right on every
            # repaint.
            self._phase_labels[pid] = label
            item = self.tree.insert(
                "", "end", text=self._phase_text("pending", label), values=("pending",),
            )
            self._phase_ids.append((pid, item))

        log_card = Card(body, theme, "Log", "The same lines are written to /var/log/arch-installer.log.")
        log_card.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_card.body, height=12, bg=theme.mono_bg, fg=theme.mono_fg, bd=0,
            font=theme.mono, wrap="none", padx=10, pady=8,
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    def enter(self) -> None:
        self.app.set_back_enabled(False)
        if self.app.session is None:
            problems = self.app.start_install()
            if problems:
                self.show_errors(problems)
                self.percent_label.configure(text="Not started")
                self.app.set_next_text("Back")
                return
        self.app.set_next_text("Waiting\u2026")
        self._poll()

    def _poll(self) -> None:
        session = self.app.session
        if session is None:
            return
        status = session.status()
        self.bar["value"] = status["percent"]
        if status["error"]:
            self.percent_label.configure(text="Installation failed", fg=self.theme.fail)
            self.detail_label.configure(text=status["error"])
            self.app.mark_rail_failed()
            self.app.set_next_text("Close")
            self.app.set_back_enabled(False)
            self.show_errors(
                ["The installation stopped: %s" % status["error"], "See the log above for the last command."]
            )
        elif status["done"]:
            self.percent_label.configure(text="Installation complete", fg=self.theme.ok)
            self.detail_label.configure(text="All steps finished in %d minutes." % max(1, status["elapsed"] // 60))
            self.app.set_next_text("Finish")
        else:
            self.percent_label.configure(text="%d%%  \u00b7  %s" % (status["percent"], status["current"]))
            self.detail_label.configure(text=status["detail"])
            self.app.set_next_text("Installing\u2026")
        self._paint_phases(status["phases"])
        self._paint_log(session.logger.tail(400))
        if not status["finished"]:
            self.after(400, self._poll)

    MARKS = {"done": "\u2713", "running": "\u25b6", "pending": "\u00b7", "failed": "!"}

    def _phase_text(self, state: str, label: str) -> str:
        return "  %s  %s" % (self.MARKS.get(state, "\u00b7"), label)

    def _paint_phases(self, phases) -> None:
        states = {row["id"]: row["state"] for row in phases}
        for pid, item in self._phase_ids:
            state = states.get(pid, "pending")
            self.tree.item(
                item, text=self._phase_text(state, self._phase_labels.get(pid, "")),
                values=(state,),
            )

    def _paint_log(self, lines) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.see("end")
        self.log.configure(state="disabled")


# --------------------------------------------------------------------------- #
# Finish
# --------------------------------------------------------------------------- #
class FinishPage(Page):
    name = "finish"
    title = "Arch Linux is installed"
    subtitle = "Remove the installation medium and restart into the new system."
    footer_note = "The installation log is kept at /var/log/arch-installer.log in the new system."
    next_text = "Close"
    show_back = False

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        tk.Label(
            body, text="\u2713", bg=theme.panel, fg=theme.ok, font=theme.big, anchor="w",
        ).pack(fill="x")
        tk.Label(
            body,
            text="Everything finished. The computer can now start Arch Linux from its own disk.",
            bg=theme.panel, fg=theme.ink, font=theme.heading, anchor="w", justify="left",
            wraplength=680,
        ).pack(fill="x", pady=(6, 18))

        card = Card(body, theme, "After the restart")
        card.pack(fill="x", pady=(0, 16))
        bullets(
            card.body, theme,
            (
                "Log in with the account you created; the root password also works.",
                "Update the system with sudo pacman -Syu, and install more software with pacman -S <package>.",
                "The multilib repository stays enabled if you turned it on, so Steam and Wine keep working.",
            ),
        )

        actions = tk.Frame(body, bg=theme.panel)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Button(
            actions, text="Restart now", style="Accent.TButton",
            command=lambda: self.app.power("reboot"),
        ).pack(side="left")
        ttk.Button(
            actions, text="Power off", style="Secondary.TButton",
            command=lambda: self.app.power("poweroff"),
        ).pack(side="left", padx=(10, 0))
