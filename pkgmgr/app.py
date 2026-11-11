"""The Kiln package manager window.

One window, two jobs: keep the installed packages in order (remove, upgrade) and
find new ones in the official repositories or the AUR.  Repository work runs
in-app with the desktop's authorisation prompt; AUR work is handed to a terminal
window, because ``makepkg`` builds as the normal user and ``sudo`` has to be able
to ask for a password.

Long work runs on a worker thread and reports back through ``after``, the same
way the control panel does.
"""

from __future__ import annotations

import os
import queue
import shlex
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from installer.context import Logger
from installer.gui import theme as theme_module

from . import __version__, core

COLUMNS = (
    ("name", "Package", 230),
    ("version", "Version", 130),
    ("origin", "Source", 90),
    ("description", "Description", 430),
)


class PackageManager:
    """The window.  Every system change is a command line from :mod:`pkgmgr.core`."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Kiln Package Manager")
        self.theme = theme_module.apply(self.root)
        self._size_window()

        self.logger = Logger()
        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.rows: list = []
        #: Tree item id -> Package, so a category heading selected on its own is
        #: never mistaken for the package at some absolute row number.
        self._by_item: dict = {}
        self.installed: list = []
        self.abilities = core.summary()

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain)
        self.root.after(150, self.reload_installed)
        self._log(
            "Kiln Package Manager %s.  pacman: %s, yay: %s, terminal: %s"
            % (
                __version__,
                self.abilities["pacman"] or "missing",
                self.abilities["yay"] or "not installed",
                self.abilities["terminal"] or "none",
            )
        )

    # -- construction ------------------------------------------------------
    def _size_window(self) -> None:
        width = min(1180, max(760, self.root.winfo_screenwidth() - 120))
        height = min(820, max(520, self.root.winfo_screenheight() - 160))
        self.root.geometry("%dx%d+%d+%d" % (width, height, 70, 60))
        self.root.minsize(min(860, width), min(600, height))

    def _build(self) -> None:
        theme = self.theme
        shell = tk.Frame(self.root, bg=theme.panel)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=theme.panel)
        header.pack(fill="x", padx=28, pady=(20, 0))
        tk.Label(
            header, text="SOFTWARE", bg=theme.panel, fg=theme.accent,
            font=theme.small_bold, anchor="w",
        ).pack(fill="x")
        tk.Label(
            header, text="Package manager", bg=theme.panel, fg=theme.ink,
            font=theme.title, anchor="w",
        ).pack(fill="x", pady=(6, 0))
        tk.Label(
            header,
            text="Installed packages from pacman, new ones from the official "
                 "repositories, and AUR recipes built with yay.",
            bg=theme.panel, fg=theme.muted, font=theme.body, anchor="w",
            justify="left", wraplength=820,
        ).pack(fill="x", pady=(6, 0))

        line = tk.Frame(shell, bg=theme.line, height=1)
        line.pack(fill="x", padx=28, pady=(14, 0))

        tools = tk.Frame(shell, bg=theme.panel)
        tools.pack(fill="x", padx=28, pady=(12, 0))
        self.tools_label = tk.Label(
            tools, text="", bg=theme.panel, fg=theme.muted, font=theme.small,
            anchor="w",
        )
        self.tools_label.pack(side="left", fill="x", expand=True)
        self.yay_button = ttk.Button(
            tools, text="Set up yay", style="Ghost.TButton", command=self.setup_yay
        )
        self.yay_button.pack(side="right")

        # -- search ---------------------------------------------------------
        search = tk.Frame(shell, bg=theme.panel)
        search.pack(fill="x", padx=28, pady=(12, 0))
        self.query = tk.StringVar()
        self.scope = tk.StringVar(value="Everything")
        entry = ttk.Entry(search, textvariable=self.query, font=theme.body)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda _event: self.search())
        self.scope_box = ttk.Combobox(
            search, textvariable=self.scope, values=list(core.SCOPES),
            state="readonly", width=14,
        )
        self.scope_box.pack(side="left", padx=(10, 0))
        self.search_button = ttk.Button(
            search, text="Search", style="Accent.TButton", command=self.search
        )
        self.search_button.pack(side="left", padx=(10, 0))

        # -- results --------------------------------------------------------
        table = tk.Frame(shell, bg=theme.panel)
        table.pack(fill="both", expand=True, padx=28, pady=(12, 0))
        self.tree = ttk.Treeview(
            table, columns=[key for key, _label, _width in COLUMNS],
            show="tree headings", selectmode="browse",
        )
        # The tree column carries the category heading, so the list reads as
        # shelves instead of one long run of packages.
        self.tree.heading("#0", text="Category / package")
        self.tree.column("#0", width=210, anchor="w", stretch=False)
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w",
                             stretch=key == "description")
        self.tree.tag_configure("aur", foreground=theme.accent)
        self.tree.tag_configure(
            "category", foreground=theme.ink, font=theme.small_bold
        )
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.install())
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._describe_selection())

        # -- actions --------------------------------------------------------
        actions = tk.Frame(shell, bg=theme.panel)
        actions.pack(fill="x", padx=28, pady=(10, 0))
        self.install_button = ttk.Button(
            actions, text="Install", style="Accent.TButton", command=self.install
        )
        self.install_button.pack(side="left")
        self.remove_button = ttk.Button(
            actions, text="Remove", style="Secondary.TButton", command=self.remove
        )
        self.remove_button.pack(side="left", padx=(8, 0))
        self.upgrade_button = ttk.Button(
            actions, text="Upgrade installed", style="Secondary.TButton",
            command=self.upgrade,
        )
        self.upgrade_button.pack(side="left", padx=(8, 0))
        self.reload_button = ttk.Button(
            actions, text="Reload", style="Ghost.TButton", command=self.reload_installed
        )
        self.reload_button.pack(side="right")

        # -- log ------------------------------------------------------------
        log_frame = tk.Frame(shell, bg=theme.panel)
        log_frame.pack(fill="x", padx=28, pady=(12, 0))
        log_background = getattr(theme, "mono_bg", "#0b0e12")
        log_foreground = getattr(theme, "mono_fg", "#cfd6de")
        self.log = tk.Text(
            log_frame, height=7, wrap="none", state="disabled",
            bg=log_background, fg=log_foreground,
            insertbackground=log_foreground,
            font=getattr(theme, "mono", ("TkFixedFont", 9)), relief="flat",
        )
        self.log.pack(fill="x")
        entry.configure(font=theme.body)

        footer = tk.Frame(shell, bg=theme.footer)
        footer.pack(fill="x", side="bottom", pady=(12, 0))
        self.status_label = tk.Label(
            footer, text="Ready", bg=theme.footer, fg=theme.faint,
            font=theme.small, anchor="w",
        )
        self.status_label.pack(side="left", padx=28, pady=10)
        ttk.Button(
            footer, text="Close", style="Secondary.TButton", command=self.close
        ).pack(side="right", padx=28, pady=8)

        self._refresh_tools()

    # -- log and status ----------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _refresh_tools(self) -> None:
        abilities = core.summary()
        self.abilities = abilities
        parts = []
        parts.append("pacman found" if abilities["pacman"] else "pacman missing")
        if abilities["yay"]:
            parts.append("yay %s" % os.path.basename(abilities["yay"]))
        else:
            parts.append("yay not installed: AUR entries cannot be built yet")
        if not abilities["elevator"]:
            parts.append("no pkexec or sudo: nothing can be installed")
        if not abilities["terminal"]:
            parts.append("no terminal emulator found: AUR builds cannot ask for a password")
        self.tools_label.configure(text=" · ".join(parts))
        self.yay_button.configure(
            text="yay is ready" if abilities["yay"] else "Set up yay",
            state="disabled" if abilities["yay"] else "normal",
        )

    # -- worker plumbing ---------------------------------------------------
    def _post(self, function) -> None:
        self.queue.put(function)

    def _drain(self) -> None:
        while True:
            try:
                function = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                function()
            except Exception as error:  # a broken callback must not stop the window
                self._log("error: %s" % error)
        self.root.after(120, self._drain)

    def _start(self, argv, description: str, done=None) -> None:
        if self.busy:
            self._log("one change at a time: wait for the running command")
            return
        self.busy = True
        self._status(description)
        self._log("$ " + " ".join(shlex.quote(str(part)) for part in argv))

        def work() -> None:
            try:
                code = core.stream(
                    argv, lambda line: self._post(lambda text=line: self._log(text))
                )
            except OSError as error:
                self._post(lambda: self._log("cannot run %s: %s" % (argv[0], error)))
                code = 127

            def finish() -> None:
                self.busy = False
                self._log("%s exited with status %d" % (argv[0], code))
                self._status("Ready" if code == 0 else "Finished with errors")
                self._refresh_tools()
                if done is not None:
                    done(code)

            self._post(finish)

        threading.Thread(target=work, daemon=True).start()

    def _run_in_terminal(self, argv, description: str, done=None) -> None:
        """Hand an interactive command to a terminal window and wait for it."""
        wrapped = core.terminal_command(argv)
        if not wrapped:
            messagebox.showerror(
                "No terminal",
                "An AUR package is built by makepkg as your user, and sudo asks "
                "for a password in a terminal.  None of these is installed:\n\n"
                + ", ".join(program for program, _flag in core.TERMINALS)
                + "\n\nInstall one of them, or run the command shown in the log "
                "by hand.",
            )
            self._log("$ " + " ".join(shlex.quote(str(part)) for part in argv))
            return
        self._start(wrapped, description, done)

    # -- reading the system ------------------------------------------------
    def reload_installed(self) -> None:
        def work() -> None:
            installed_text = core.capture([core.PACMAN, "-Q"])
            foreign_text = core.capture([core.PACMAN, "-Qm"])
            rows = core.parse_installed(installed_text, foreign_text)

            def show() -> None:
                self.installed = rows
                self._status("%d packages installed" % len(rows))
                self._log("read %d installed packages (%d from the AUR)"
                          % (len(rows), sum(1 for row in rows if row.aur)))
                if self.scope.get() in ("Installed", "Everything"):
                    self.search()

            self._post(show)

        threading.Thread(target=work, daemon=True).start()

    # -- searching ---------------------------------------------------------
    def search(self) -> None:
        query = self.query.get().strip()
        scope = self.scope.get()
        if scope in ("Installed", "Everything") and not self.installed:
            self.reload_installed()
            return

        def work() -> None:
            groups = []
            if scope in ("Installed", "Everything"):
                groups.append(core.filter_installed(self.installed, query))
            for argv in core.search_commands(query, scope):
                if argv[0] == core.YAY and not core.has_yay():
                    self._post(lambda: self._log("yay is not installed: AUR search skipped"))
                    continue
                groups.append(core.parse_search(core.capture(argv)))
            rows = core.merge(*groups)

            def show() -> None:
                self._show(rows, scope, query)

            self._post(show)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, rows, scope: str, query: str) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows = list(rows)
        self._by_item = {}
        groups = core.group_by_category(self.rows)
        for category, packages in groups:
            shelf = self.tree.insert(
                "", "end", text=category,
                values=("", "", "", "%d package(s)" % len(packages)),
                open=True, tags=("category",),
            )
            for package in packages:
                item = self.tree.insert(
                    shelf, "end", text="",
                    values=(package.name, package.version, package.origin,
                            package.description),
                    tags=("aur",) if package.aur else (),
                )
                self._by_item[item] = package
        what = query or "everything"
        self._status("%d result(s) for %s in %s, in %d categories"
                     % (len(self.rows), what, scope, len(groups)))
        self._log(
            "search: %d result(s) for %s in %s (%s)"
            % (
                len(self.rows), what, scope,
                ", ".join("%s: %d" % (name, len(items)) for name, items in groups)
                or "nothing found",
            )
        )

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            return None
        # A category heading is not a package, so it maps to nothing.
        return self._by_item.get(selection[0])

    def _describe_selection(self) -> None:
        package = self._selected()
        if package is None:
            return
        text = "%s %s from %s" % (package.name, package.version, package.origin)
        if package.installed:
            text += " (installed)"
        if package.aur:
            text += " — built from its AUR recipe in a terminal"
        self._status(text)

    # -- actions -----------------------------------------------------------
    def install(self) -> None:
        package = self._selected()
        if package is None:
            messagebox.showinfo("Install", "Select a package in the list first.")
            return
        if package.aur:
            if not core.has_yay():
                if messagebox.askyesno(
                    "yay is needed",
                    "%s is an AUR package: it has to be built by yay.\n\n"
                    "Set yay up now?  It needs base-devel and git, and makepkg "
                    "asks for your sudo password in a terminal." % package.name,
                ):
                    self.setup_yay()
                return
            self._run_in_terminal(
                core.install_command(package),
                "Building %s from the AUR" % package.name,
                lambda _code: self.reload_installed(),
            )
            return
        prefix = core.elevator()
        if not prefix:
            messagebox.showerror(
                "No way to authorise",
                "Installing needs pkexec (polkit) or sudo.  Neither was found.",
            )
            return
        self._start(
            prefix + core.install_command(package),
            "Installing %s" % package.name,
            lambda _code: self.reload_installed(),
        )

    def remove(self) -> None:
        package = self._selected()
        if package is None:
            messagebox.showinfo("Remove", "Select an installed package first.")
            return
        if not package.installed:
            messagebox.showinfo("Remove", "%s is not installed." % package.name)
            return
        if not messagebox.askyesno(
            "Remove %s" % package.name,
            "Remove %s and the dependencies nothing else needs?" % package.name,
        ):
            return
        prefix = core.elevator()
        if not prefix:
            messagebox.showerror(
                "No way to authorise",
                "Removing needs pkexec (polkit) or sudo.  Neither was found.",
            )
            return
        self._start(
            prefix + core.remove_command(package),
            "Removing %s" % package.name,
            lambda _code: self.reload_installed(),
        )

    def upgrade(self) -> None:
        if not messagebox.askyesno(
            "Upgrade installed",
            "Run a full system upgrade (pacman -Syu) now?\n\n"
            "A partial upgrade is how an Arch system breaks, so this "
            "refreshes the databases first.",
        ):
            return
        prefix = core.elevator()
        if not prefix:
            messagebox.showerror(
                "No way to authorise",
                "Upgrading needs pkexec (polkit) or sudo.  Neither was found.",
            )
            return
        self._start(
            prefix + core.upgrade_command(),
            "Upgrading the system",
            lambda _code: self.reload_installed(),
        )

    def setup_yay(self) -> None:
        if core.has_yay():
            messagebox.showinfo("yay", "yay is already installed.")
            self._refresh_tools()
            return
        if not messagebox.askyesno(
            "Set up yay",
            "yay builds AUR packages.  It is not in the official repositories, "
            "so this window does two things:\n\n"
            "1. installs base-devel and git with pacman\n"
            "2. clones yay's recipe and builds it with makepkg in a terminal, "
            "where sudo can ask for your password\n\nContinue?",
        ):
            return
        prefix = core.elevator()
        if not prefix:
            messagebox.showerror(
                "No way to authorise",
                "Installing the build tools needs pkexec (polkit) or sudo.",
            )
            return
        directory = tempfile.mkdtemp(prefix="kiln-yay-")

        def build(_code: int) -> None:
            self._run_in_terminal(
                core.yay_bootstrap_command(directory),
                "Building yay from the AUR",
                lambda _done: self._refresh_tools(),
            )

        self._start(
            prefix + core.build_tools_command(), "Installing the build tools", build
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self.busy and not messagebox.askyesno(
            "Still working", "A command is still running.  Close anyway?"
        ):
            return
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run() -> int:
    """Open the window; returns the exit status."""
    try:
        return PackageManager().run()
    except tk.TclError as error:
        print("kiln-packages: cannot open a window: %s" % error)
        return 1
