"""The installer window: shell, navigation and the installation thread.

A plain Tk application: no browser, no local HTTP server, no extra toolkit.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__, adapt, hardware
from ..config import InstallConfig
from ..context import Context, Logger
from ..orchestrator import Session
from . import theme as theme_module
from .pages import (
    DiskPage,
    FinishPage,
    HardwarePage,
    NetworkPage,
    ProgressPage,
    SoftwarePage,
    SummaryPage,
    SystemPage,
    WelcomePage,
)
from .widgets import StepRail, fit_labels

class Installer:
    """Owns the window, the collected answers and the running installation."""

    ORDER = ("welcome", "hardware", "network", "disk", "system", "software", "summary", "progress", "finish")
    RAIL = (
        ("hardware", "Hardware"),
        ("network", "Network"),
        ("disk", "Disk"),
        ("system", "System"),
        ("software", "Software"),
        ("summary", "Review"),
        ("install", "Install"),
    )

    def __init__(self, simulate: bool = False, dry_run: bool = False, kiosk: bool = False) -> None:
        self.simulate = simulate
        self.dry_run = dry_run or simulate
        self.kiosk = kiosk
        self.session = None
        self.configuration = None
        self._current = ""
        self._compact = None

        from .. import brand

        self.root = tk.Tk()
        self.root.title("%s - %s" % (brand.PRODUCT_TITLE, brand.TAGLINE))
        self.theme = theme_module.apply(self.root)
        self._size_window()
        self._install_cursor()

        self.logger = Logger(None if self.dry_run else "/var/log/kiln-setup-ui.log")
        self.ctx = Context(self.logger, dry_run=self.dry_run)
        # The package search must talk to the real repositories even while the
        # installation itself is only being simulated.
        self.search_ctx = Context(Logger(None), dry_run=False)

        if simulate:
            self.logger.log("starting with a simulated laptop profile", "warn")
            self.hardware = hardware.simulate()
        else:
            self.logger.log("detecting the hardware of this machine", "step")
            self.hardware = hardware.detect(self.ctx)

        # The wizard starts from what this machine needs, not from fixed values.
        self.recommendation = adapt.recommend(self.hardware)
        self.form = self._defaults()
        self._form_vars: dict = {}

        self._build_shell()
        self._build_pages()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.goto("welcome")
        if kiosk:
            self.root.after(400, self._grab_focus)

    # -- construction ------------------------------------------------------
    def _size_window(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        if self.kiosk:
            try:
                self.root.overrideredirect(True)
            except tk.TclError:
                pass
            self.root.geometry("%dx%d+0+0" % (screen_width, screen_height))
            return
        width = min(1180, max(720, screen_width - 80))
        height = min(860, max(460, screen_height - 120))
        width = max(480, min(width, screen_width))
        height = max(360, min(height, screen_height))
        self.root.geometry(
            "%dx%d+%d+%d"
            % (width, height, max(0, (screen_width - width) // 2), max(0, (screen_height - height) // 3))
        )
        self.root.minsize(min(880, width), min(600, height))

    def _install_cursor(self) -> None:
        """Make sure a pointer is drawn at all on the bare X server."""
        for name in ("left_ptr", "default", "arrow", "top_left_arrow", "X_cursor"):
            try:
                self.root.configure(cursor=name)
            except tk.TclError:
                continue
            try:
                self.root.option_add("*Cursor", name)
            except tk.TclError:
                pass
            return

    def _grab_focus(self) -> None:
        try:
            self.root.focus_force()
            self.root.lift()
        except tk.TclError:
            pass

    def form_var(self, key: str) -> tk.StringVar:
        """One shared variable per form key, so pages agree on the value."""
        if key not in self._form_vars:
            self._form_vars[key] = tk.StringVar(value=str(self.form.get(key, "")))
        return self._form_vars[key]

    def _defaults(self) -> dict:
        hw = self.hardware
        internal = hw.internal_disks() or list(hw.disks)
        disk = max(internal, key=lambda item: item.size).device if internal else ""
        chosen = self.recommendation.values
        return {
            "disk": disk,
            "mode": "erase",
            "confirmErase": False,
            "efi": chosen["efi"],
            "bootloader": chosen["bootloader"],
            "rootFs": "ext4",
            "swap": True,
            "swapGb": chosen["swapGb"],
            "espGb": 1.0,
            "separateHome": False,
            "hostname": "archlinux",
            "timezone": chosen["timezone"],
            "locale": chosen["locale"],
            "keymap": chosen["keymap"],
            "desktop": chosen["desktop"],
            "kernel": chosen["kernel"],
            "microcode": chosen["microcode"],
            "firmware": chosen["firmware"],
            "multilib": True,
            "packages": list(self.recommendation.packages),
            "rootPassword": "",
            "user": None,
            "wifiSsid": "",
            "wifiPassword": "",
        }

    def _build_shell(self) -> None:
        theme = self.theme
        outer = tk.Frame(self.root, bg=theme.canvas)
        outer.pack(fill="both", expand=True)

        self.rail = StepRail(outer, theme)
        self.rail.pack(side="left", fill="y")
        self.rail.set_steps(self.RAIL)
        self.rail.footer.configure(
            text="version %s\n%s"
            % (
                __version__,
                "simulated hardware"
                if self.simulate
                else "%s \u00b7 %s" % (
                    self.hardware.firmware.upper(),
                    self.hardware.cpu_model.split("(")[0].strip()[:28],
                ),
            )
        )

        main = tk.Frame(outer, bg=theme.panel)
        main.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(main, bg=theme.footer)
        footer.pack(side="bottom", fill="x")
        tk.Frame(main, bg=theme.line, height=1).pack(side="bottom", fill="x")

        # Windows Setup puts the buttons on the grey chrome below a white sheet.
        strip = tk.Frame(footer, bg=theme.footer)
        strip.pack(fill="x", padx=32, pady=14)
        self._strip = strip
        self.footer_note = tk.Label(
            strip, text="", bg=theme.footer, fg=theme.faint, font=theme.small,
            anchor="w", justify="left", wraplength=520,
        )
        self.footer_note.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self.next_button = ttk.Button(strip, text="Next", style="Accent.TButton", command=self.go_next)
        self.next_button.pack(side="right")
        self.back_button = ttk.Button(strip, text="Back", style="Secondary.TButton", command=self.go_back)
        self.back_button.pack(side="right", padx=(0, 8))

        header = tk.Frame(main, bg=theme.panel)
        header.pack(side="top", fill="x", padx=36, pady=(22, 0))
        self._header = header
        self.eyebrow_label = tk.Label(
            header, text="", bg=theme.panel, fg=theme.accent, font=theme.small_bold, anchor="w",
        )
        self.eyebrow_label.pack(fill="x")
        self.title_label = tk.Label(
            header, text="", bg=theme.panel, fg=theme.ink, font=theme.title, anchor="w", justify="left",
        )
        self.title_label.pack(fill="x", pady=(7, 0))
        self.subtitle_label = tk.Label(
            header, text="", bg=theme.panel, fg=theme.muted, font=theme.body,
            anchor="w", justify="left", wraplength=660,
        )
        self.subtitle_label.pack(fill="x", pady=(7, 0))

        tk.Frame(main, bg=theme.line, height=1).pack(side="top", fill="x", pady=(18, 0))

        self.body = tk.Frame(main, bg=theme.panel)
        self.body.pack(fill="both", expand=True, padx=36, pady=(20, 16))

        self.root.bind("<Configure>", self._on_resize, add="+")
        fit_labels(main)
        fit_labels(self.rail)

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        compact = int(getattr(event, "width", 0)) < 1000
        if compact == self._compact:
            return
        self._compact = compact
        padding = 20 if compact else 32
        try:
            self.rail.configure(width=200 if compact else 236)
            self._header.pack_configure(padx=padding)
            self.body.pack_configure(padx=padding)
            self._strip.pack_configure(padx=padding)
        except tk.TclError:
            pass

    def _build_pages(self) -> None:
        self.pages = {}
        for page_class in (
            WelcomePage,
            HardwarePage,
            NetworkPage,
            DiskPage,
            SystemPage,
            SoftwarePage,
            SummaryPage,
            ProgressPage,
            FinishPage,
        ):
            page = page_class(self.body, self)
            self.pages[page.name] = page

    # -- navigation --------------------------------------------------------
    def goto(self, name: str) -> None:
        if self._current:
            previous = self.pages[self._current]
            previous.leave()
            previous.pack_forget()
        page = self.pages[name]
        self._current = name
        page.pack(fill="both", expand=True)
        self.title_label.configure(text=page.title)
        self.eyebrow_label.configure(text=self._eyebrow(page))
        self.subtitle_label.configure(text=page.subtitle)
        self.footer_note.configure(text=page.footer_note)
        self.next_button.configure(text=page.next_text, state="normal")
        self.back_button.configure(state="normal" if page.show_back else "disabled")
        self._sync_rail()
        page.enter()

    def go_next(self) -> None:
        page = self.pages[self._current]
        page.collect()
        problems = page.errors()
        if problems:
            page.show_errors(problems)
            return
        page.clear_errors()
        if self._current == "progress":
            if self.session is not None and self.session.running:
                return
            if self.session is not None and self.session.error:
                self.goto("finish")
                return
            if self.session is not None and self.session.done:
                self.goto("finish")
                return
        index = self.ORDER.index(self._current)
        if index + 1 >= len(self.ORDER):
            self.close()
            return
        self.goto(self.ORDER[index + 1])

    def go_back(self) -> None:
        index = self.ORDER.index(self._current)
        if index == 0:
            return
        self.goto(self.ORDER[index - 1])

    def _eyebrow(self, page) -> str:
        labels = dict(self.RAIL)
        key = getattr(page, "rail_key", "")
        if key in labels:
            position = [name for name, _label in self.RAIL].index(key) + 1
            return "STEP %d OF %d  \u00b7  %s" % (position, len(self.RAIL), labels[key].upper())
        if page.name == "finish":
            return "INSTALLATION COMPLETE"
        return "GETTING STARTED"

    def _sync_rail(self) -> None:
        keys = [key for key, _label in self.RAIL]
        if self._current == "finish":
            for key in keys:
                self.rail.set_state(key, "done")
            return
        page = self.pages[self._current]
        key = page.rail_key
        active = keys.index(key) if key in keys else -1
        for position, name in enumerate(keys):
            if active < 0:
                state = "pending"
            elif position < active:
                state = "done"
            elif position == active:
                state = "active"
            else:
                state = "pending"
            self.rail.set_state(name, state)

    def mark_rail_failed(self) -> None:
        self.rail.set_state("install", "failed")

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_button.configure(state="normal" if enabled else "disabled")

    def set_next_text(self, text: str) -> None:
        self.next_button.configure(text=text)

    def set_back_enabled(self, enabled: bool) -> None:
        self.back_button.configure(state="normal" if enabled else "disabled")

    # -- installation ------------------------------------------------------
    def start_install(self) -> list:
        """Validate once more and start the pipeline in a worker thread."""
        if self.session is not None and self.session.running:
            return ["An installation is already running."]
        try:
            config = InstallConfig.from_dict(self.form)
        except (TypeError, ValueError) as error:
            return ["The answers could not be read: %s" % error]
        found = None
        for candidate in self.hardware.disks:
            if candidate.device == config.disk:
                found = candidate
        problems = config.validate(disk_size=found.size if found else 0)
        if not self.dry_run and found is None:
            problems.append("The disk %s is not present on this machine." % config.disk)
        if problems:
            return problems
        self.configuration = config
        options = {"dry_run": self.dry_run}
        if self.dry_run:
            options["log_path"] = None
        self.session = Session(self.hardware, config, **options)
        self.session.start()
        return []

    def power(self, action: str) -> None:
        if action not in ("reboot", "poweroff"):
            return
        if self.dry_run:
            messagebox.showinfo(
                "Simulation",
                "The computer would now %s.\n\nNothing was executed because the installer is in "
                "dry-run mode." % ("restart" if action == "reboot" else "power off"),
                parent=self.root,
            )
            return
        ctx = self.session.ctx if self.session is not None else self.ctx
        ctx.run(["sync"], check=False, quiet=True)
        ctx.run([action], check=False)

    def close(self) -> None:
        session = self.session
        if session is not None and session.running:
            keep_going = messagebox.askyesno(
                "Installation in progress",
                "The installation is still running. Quitting now leaves the disk unfinished and "
                "the system will not boot.\n\nQuit anyway?",
                parent=self.root,
            )
            if not keep_going:
                return
            # The media is left mounted: a half-written system must not be booted.
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()
