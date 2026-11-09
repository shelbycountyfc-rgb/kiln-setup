"""The control panel window.

Same shell as the installer: a navigation rail on the left, a page header, a
content area and a footer.  Work that changes the system runs on a worker
thread and reports back through ``after``.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from installer import __version__ as installer_version
from installer.context import Context, Logger
from installer.gui import theme as theme_module
from installer.gui.widgets import StepRail, fit_labels

from . import __version__
from .pages import (
    AboutPage,
    DashboardPage,
    NetworkPage,
    ServicesPage,
    SoftwarePage,
    UsersPage,
)


class Panel:
    NAV = (
        ("dashboard", "Overview"),
        ("software", "Software"),
        ("services", "Services"),
        ("users", "Users"),
        ("network", "Network"),
        ("about", "About"),
    )

    def __init__(self, kiosk: bool = False) -> None:
        self.kiosk = kiosk
        self._current = ""
        self._compact = None
        self._jobs = 0

        self.root = tk.Tk()
        self.root.title("Arch Control Panel")
        self.theme = theme_module.apply(self.root)
        self._size_window()

        self.logger = Logger()
        self.ctx = Context(self.logger, dry_run=False)

        self._build_shell()
        self._build_pages()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.goto("dashboard")

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
        width = min(1100, max(700, screen_width - 100))
        height = min(800, max(440, screen_height - 140))
        self.root.geometry("%dx%d+%d+%d" % (width, height, 60, 50))
        self.root.minsize(min(820, width), min(560, height))

    def _build_shell(self) -> None:
        theme = self.theme
        outer = tk.Frame(self.root, bg=theme.panel)
        outer.pack(fill="both", expand=True)

        self.rail = StepRail(outer, theme, on_select=self.goto)
        self.rail.pack(side="left", fill="y")
        self.rail.set_steps(self.NAV)
        self.rail.footer.configure(
            text="version %s\ninstaller %s" % (__version__, installer_version)
        )

        main = tk.Frame(outer, bg=theme.panel)
        main.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(main, bg=theme.footer)
        footer.pack(side="bottom", fill="x")
        tk.Frame(main, bg=theme.line, height=1).pack(side="bottom", fill="x")
        strip = tk.Frame(footer, bg=theme.footer)
        strip.pack(fill="x", padx=32, pady=14)
        self._strip = strip
        self.status_label = tk.Label(
            strip, text="Ready", bg=theme.footer, fg=theme.faint, font=theme.small,
            anchor="w", justify="left", wraplength=520,
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        ttk.Button(strip, text="Close", style="Secondary.TButton", command=self.close).pack(side="right")

        header = tk.Frame(main, bg=theme.panel)
        header.pack(side="top", fill="x", padx=32, pady=(22, 0))
        self._header = header
        self.eyebrow = tk.Label(
            header, text="", bg=theme.panel, fg=theme.accent, font=theme.small_bold, anchor="w",
        )
        self.eyebrow.pack(fill="x")
        self.title_label = tk.Label(
            header, text="", bg=theme.panel, fg=theme.ink, font=theme.title, anchor="w",
        )
        self.title_label.pack(fill="x", pady=(6, 0))
        self.subtitle_label = tk.Label(
            header, text="", bg=theme.panel, fg=theme.muted, font=theme.body,
            anchor="w", justify="left", wraplength=640,
        )
        self.subtitle_label.pack(fill="x", pady=(6, 0))

        tk.Frame(main, bg=theme.line, height=1).pack(side="top", fill="x", pady=(16, 0))
        self.body = tk.Frame(main, bg=theme.panel)
        self.body.pack(fill="both", expand=True, padx=32, pady=(18, 14))

        self.root.bind("<Configure>", self._on_resize, add="+")
        fit_labels(main)

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        compact = int(getattr(event, "width", 0)) < 940
        if compact == self._compact:
            return
        self._compact = compact
        padding = 18 if compact else 32
        try:
            self.rail.configure(width=186 if compact else 232)
            self._header.pack_configure(padx=padding)
            self.body.pack_configure(padx=padding)
            self._strip.pack_configure(padx=padding)
        except tk.TclError:
            pass

    def _build_pages(self) -> None:
        self.pages = {}
        for page_class in (
            DashboardPage,
            SoftwarePage,
            ServicesPage,
            UsersPage,
            NetworkPage,
            AboutPage,
        ):
            page = page_class(self.body, self)
            self.pages[page.name] = page

    # -- navigation --------------------------------------------------------
    def goto(self, name: str) -> None:
        if self._current:
            previous = self.pages[self._current]
            previous.pack_forget()
        page = self.pages[name]
        self._current = name
        page.pack(fill="both", expand=True)
        self.title_label.configure(text=page.title)
        self.subtitle_label.configure(text=page.subtitle)
        keys = [key for key, _label in self.NAV]
        self.eyebrow.configure(
            text="SECTION %d OF %d  \u00b7  %s" % (
                keys.index(page.nav_key) + 1, len(keys), dict(self.NAV)[page.nav_key].upper()
            )
        )
        for key in keys:
            self.rail.set_state(key, "active" if key == page.nav_key else "pending")
        try:
            page.enter()
        except Exception as error:  # a failing fact must not kill the window
            self.status_label.configure(text="Could not refresh: %s" % error)

    # -- background work ---------------------------------------------------
    def job(self, function, done, privileged: bool = False) -> None:
        """Run ``function`` off the UI thread and call ``done(result)``.

        An exception is passed to ``done`` instead of being raised: the pages
        show it in their banner, so a missing tool never closes the window.
        """
        self._jobs += 1
        self.status_label.configure(text="Working\u2026")

        def worker() -> None:
            try:
                result = function()
            except Exception as error:
                result = error
            try:
                self.root.after(0, self._finish_job, done, result)
            except (tk.TclError, RuntimeError):
                pass

        threading.Thread(target=worker, name="panel-job", daemon=True).start()

    def _finish_job(self, done, result) -> None:
        self._jobs = max(0, self._jobs - 1)
        self.status_label.configure(text="Ready" if not self._jobs else "Working\u2026")
        try:
            done(result)
        except Exception as error:
            self.status_label.configure(text="Could not apply the result: %s" % error)

    def close(self) -> None:
        if self._jobs:
            if not messagebox.askyesno(
                "Work in progress",
                "A task is still running. Closing now stops the window, not the task.\n\nClose anyway?",
                parent=self.root,
            ):
                return
        self.root.destroy()

    def mainloop(self) -> None:
        self.root.mainloop()
