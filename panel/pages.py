"""The pages of the control panel.

Same shape as the installer's pages: each page builds widgets once, refreshes
its facts in :meth:`enter` and runs privileged work through ``app.job`` so the
window never blocks on pacman.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from installer import catalog, packages as pkg_tools
from installer.context import human_bytes
from installer.gui.widgets import (
    Banner,
    Card,
    Notice,
    Pill,
    ScrollArea,
    bullets,
    fit_labels,
    hint,
)
from . import core

GIB = 1024 ** 3


class PanelPage(tk.Frame):
    name = ""
    title = ""
    subtitle = ""
    nav_key = ""

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, bg=app.theme.panel)
        self.app = app
        self.theme = app.theme
        self._banner = Banner(self, self.theme)
        self._banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self._banner.grid_remove()
        self.area = ScrollArea(self, self.theme)
        self.area.grid(row=1, column=0, sticky="nsew")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.build(self.area.body)
        fit_labels(self)

    def build(self, body: tk.Misc) -> None:
        raise NotImplementedError

    def enter(self) -> None:
        pass

    def say(self, text: str, kind: str = "ok") -> None:
        self._banner.show(text, kind)

    def quiet(self) -> None:
        self._banner.grid_remove()


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
class DashboardPage(PanelPage):
    name = "dashboard"
    title = "Overview"
    subtitle = "The state of this computer at a glance."
    nav_key = "dashboard"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        release = core.os_release()
        tk.Label(
            body,
            text=release.get("PRETTY_NAME", "Arch Linux"),
            bg=theme.panel, fg=theme.ink, font=theme.big, anchor="w",
        ).pack(fill="x")
        tk.Label(
            body,
            text="Kernel %s  \u00b7  %s" % (core.kernel(), core.hostname()),
            bg=theme.panel, fg=theme.muted, font=theme.body, anchor="w",
        ).pack(fill="x", pady=(2, 18))

        self.stats = Card(body, theme, "System")
        self.stats.pack(fill="x", pady=(0, 16))
        self._rows = tk.Frame(self.stats.body, bg=theme.panel)
        self._rows.pack(fill="x")

        update_card = Card(
            body, theme, "Updates",
            "Arch is a rolling release: the system stays current with a full upgrade.",
        )
        update_card.pack(fill="x", pady=(0, 16))
        self.update_label = tk.Label(
            update_card.body, text="Not checked yet.", bg=theme.panel, fg=theme.ink_soft,
            font=theme.body, anchor="w", justify="left", wraplength=620,
        )
        self.update_label.pack(fill="x", pady=(0, 8))
        buttons = tk.Frame(update_card.body, bg=theme.panel)
        buttons.pack(fill="x")
        ttk.Button(
            buttons, text="Check for updates", style="Secondary.TButton",
            command=self.check_updates,
        ).pack(side="left")
        ttk.Button(
            buttons, text="Upgrade now", style="Accent.TButton", command=self.upgrade,
        ).pack(side="left", padx=(10, 0))
        self.update_log = tk.Text(
            update_card.body, height=6, bg=theme.mono_bg, fg=theme.mono_fg, bd=0,
            font=theme.mono, padx=10, pady=8, wrap="none",
        )
        self.update_log.pack(fill="both", expand=True, pady=(10, 0))
        self.update_log.configure(state="disabled")

        quick = Card(body, theme, "Everyday tasks", "The other pages do the rest.")
        quick.pack(fill="x")
        bullets(
            quick.body, theme,
            (
                "Software: install or remove packages by category, and switch 32-bit support on or off.",
                "Services: start, stop and enable the background programs of the system.",
                "Users: change passwords and add an account to the wheel group.",
                "Network: see the addresses and connect to a Wi-Fi network.",
            ),
        )

    def enter(self) -> None:
        theme = self.theme
        for child in self._rows.winfo_children():
            child.destroy()
        total, available = core.memory()
        disk_total, disk_used, _free = core.disk_usage("/")
        rows = [
            ("Processor", core.cpu_model()),
            ("Memory", "%s of %s in use" % (human_bytes(total - available), human_bytes(total)) if total else "unknown"),
            ("Storage", "%s of %s in use" % (human_bytes(disk_used), human_bytes(disk_total)) if disk_total else "unknown"),
            ("Uptime", core.uptime_text()),
            ("Packages", "%d installed" % core.installed_count(self.app.ctx)),
            ("32-bit support", "enabled" if core.multilib_enabled() else "disabled"),
        ]
        for label, value in rows:
            row = tk.Frame(self._rows, bg=theme.panel)
            row.pack(fill="x", pady=(0, 6))
            tk.Label(
                row, text=label, bg=theme.panel, fg=theme.muted, font=theme.small,
                width=16, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=value, bg=theme.panel, fg=theme.ink_soft, font=theme.body,
                anchor="w", justify="left", wraplength=520,
            ).pack(side="left", fill="x", expand=True)

    # -- updates -----------------------------------------------------------
    def check_updates(self) -> None:
        self.update_label.configure(text="Checking the repositories\u2026")
        self._write_log("")
        self.app.job(
            lambda: self.app.ctx.captured(["pacman", "-Sy"], quiet=True) and core.updates(self.app.ctx),
            self._showed_updates,
        )

    def _showed_updates(self, result) -> None:
        if isinstance(result, Exception):
            self.update_label.configure(text="Could not check: %s" % result)
            return
        rows = result or []
        if not rows:
            self.update_label.configure(text="The system is up to date.")
            return
        self.update_label.configure(text="%d update(s) available." % len(rows))
        self._write_log("\n".join("%s %s \u2192 %s" % row for row in rows[:40]))

    def upgrade(self) -> None:
        self.update_label.configure(text="Upgrading\u2026")
        self._write_log("")

        def work():
            ctx = self.app.ctx
            ctx.elevated(["pacman", "-Syu", "--noconfirm"], capture=True)
            return core.updates(ctx)

        self.app.job(work, self._showed_updates)

    def _write_log(self, text: str) -> None:
        self.update_log.configure(state="normal")
        self.update_log.delete("1.0", "end")
        self.update_log.insert("end", text)
        self.update_log.configure(state="disabled")


# --------------------------------------------------------------------------- #
# Software
# --------------------------------------------------------------------------- #
class SoftwarePage(PanelPage):
    name = "software"
    title = "Software"
    subtitle = "Categories from the official repositories; install and remove with pacman."
    nav_key = "software"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        installed_card = Card(body, theme, "Installed state")
        installed_card.pack(fill="x", pady=(0, 16))
        top = tk.Frame(installed_card.body, bg=theme.panel)
        top.pack(fill="x")
        self.multilib_pill = Pill(top, theme, "", "info")
        self.multilib_pill.pack(side="left")
        self.multilib_button = ttk.Button(
            top, text="Enable 32-bit support", style="Secondary.TButton",
            command=self.toggle_multilib,
        )
        self.multilib_button.pack(side="left", padx=(10, 0))
        self.status_label = tk.Label(
            installed_card.body, text="", bg=theme.panel, fg=theme.muted,
            font=theme.small, anchor="w", justify="left", wraplength=620,
        )
        self.status_label.pack(fill="x", pady=(8, 0))

        picker = Card(
            body, theme, "Packages",
            "Click a package to mark it for install (or removal when it is installed), "
            "then press the button that matches.",
        )
        picker.pack(fill="x")
        self._installed: set = set()
        self._pending_install: set = set()
        self._pending_remove: set = set()

        search_row = tk.Frame(picker.body, bg=theme.panel)
        search_row.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=self.search_var, width=34).pack(side="left")
        ttk.Button(
            search_row, text="Search repositories", style="Secondary.TButton",
            command=self.search,
        ).pack(side="left", padx=(8, 0))
        self.pending_pill = Pill(search_row, theme, "", "info")
        self.pending_pill.pack(side="right")

        panes = tk.Frame(picker.body, bg=theme.panel)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, minsize=190)
        panes.columnconfigure(1, weight=1)
        self.category_list = tk.Listbox(
            panes, height=10, width=26, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.canvas, fg=theme.ink,
            selectbackground=theme.accent_soft, selectforeground=theme.ink,
            font=theme.body, exportselection=False,
        )
        self.category_list.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.category_list.bind("<<ListboxSelect>>", self._category_changed)
        self.package_list = tk.Listbox(
            panes, height=10, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.panel, fg=theme.ink_soft,
            selectbackground=theme.accent_soft, selectforeground=theme.ink,
            font=theme.body, exportselection=False,
        )
        self.package_list.grid(row=0, column=1, sticky="nsew")
        self.package_list.bind("<ButtonRelease-1>", self._package_clicked)

        actions = tk.Frame(picker.body, bg=theme.panel)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            actions, text="Install marked", style="Accent.TButton", command=self.install_marked,
        ).pack(side="left")
        ttk.Button(
            actions, text="Remove marked", style="Secondary.TButton", command=self.remove_marked,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Clear", style="Ghost.TButton", command=self.clear_marks).pack(
            side="left", padx=(10, 0)
        )

        self._categories = list(catalog.categories())
        self._view: list = []
        self._load_categories()

    # -- catalogue ---------------------------------------------------------
    def _load_categories(self) -> None:
        self.category_list.delete(0, "end")
        for title, _note, multilib, _items in self._categories:
            self.category_list.insert("end", title + ("  (32-bit)" if multilib else ""))
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
        _title, _note, _multilib, items = self._categories[index]
        self._view = [name for name, _label, _detail in items]
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.package_list.delete(0, "end")
        for name in self._view:
            label, detail = catalog.details(name)
            if name in self._pending_install:
                mark = "[+]"
            elif name in self._pending_remove:
                mark = "[-]"
            else:
                mark = "[x]" if name in self._installed else "[ ]"
            self.package_list.insert("end", "  %s  %-24s %s" % (mark, name, label or detail))
        self.pending_pill.configure(
            text="+%d  \u2212%d" % (len(self._pending_install), len(self._pending_remove))
        )

    def _package_clicked(self, event: tk.Event) -> None:
        index = self.package_list.nearest(event.y)
        if index < 0 or index >= len(self._view):
            return
        name = self._view[index]
        if name in self._pending_install:
            self._pending_install.discard(name)
        elif name in self._pending_remove:
            self._pending_remove.discard(name)
        elif name in self._installed:
            self._pending_remove.add(name)
        elif catalog.is_multilib_package(name) and not core.multilib_enabled():
            self.say("%s needs 32-bit support. Enable multilib first." % name, "warn")
            return
        else:
            self._pending_install.add(name)
        self._refresh_list()

    def clear_marks(self) -> None:
        self._pending_install.clear()
        self._pending_remove.clear()
        self._refresh_list()

    # -- repository search -------------------------------------------------
    def search(self) -> None:
        term = self.search_var.get().strip()
        if not term:
            return
        self.status_label.configure(text="Searching for %r\u2026" % term)
        self.app.job(lambda: pkg_tools.search(self.app.ctx, term), lambda result: self._results(term, result))

    def _results(self, term: str, result) -> None:
        if isinstance(result, Exception) or not result:
            self.status_label.configure(text="No package matched %r." % term)
            return
        items = tuple((row["name"], "%s %s" % (row["repo"], row["version"]), row["description"]) for row in result)
        self._categories.append(("Search: %s" % term, "%d matches" % len(items), False, items))
        self.category_list.insert("end", "Search: %s" % term)
        self.category_list.selection_clear(0, "end")
        self.category_list.selection_set("end")
        self._category_changed()
        self.status_label.configure(text="%d matches for %r." % (len(items), term))

    # -- actions -----------------------------------------------------------
    def toggle_multilib(self) -> None:
        enabled = not core.multilib_enabled()
        self.status_label.configure(
            text="Turning 32-bit support %s\u2026" % ("on" if enabled else "off")
        )

        def work():
            ctx = self.app.ctx
            try:
                with open(core.PACMAN_CONF, "r", encoding="utf-8", errors="replace") as handle:
                    current = handle.read()
            except OSError as error:
                return "Cannot read %s: %s" % (core.PACMAN_CONF, error)
            updated = pkg_tools.set_multilib(current, enabled)
            if updated == current:
                return "32-bit support was already %s." % ("enabled" if enabled else "disabled")
            ctx.elevated_write(core.PACMAN_CONF, updated)
            ctx.captured(["pacman", "-Sy"], quiet=True)
            return "32-bit support is now %s." % ("enabled" if enabled else "disabled")

        self.app.job(work, self._after_toggle, privileged=False)

    def _after_toggle(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self.say(str(result), "ok")
        self.refresh_system_state()

    def install_marked(self) -> None:
        names = sorted(self._pending_install)
        if not names:
            self.say("Nothing is marked for installation.", "warn")
            return
        self.status_label.configure(text="Installing %s\u2026" % ", ".join(names))

        def work():
            self.app.ctx.elevated(["pacman", "-S", "--needed", "--noconfirm"] + names, capture=True)
            return names

        self.app.job(work, self._after_install)

    def _after_install(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self._pending_install.clear()
        self.say("Installed: %s" % ", ".join(result), "ok")
        self.refresh_system_state()

    def remove_marked(self) -> None:
        names = sorted(self._pending_remove)
        if not names:
            self.say("Nothing is marked for removal.", "warn")
            return
        answer = messagebox.askyesno(
            "Remove packages",
            "Remove %s and their unused dependencies?" % ", ".join(names),
            parent=self.winfo_toplevel(),
        )
        if not answer:
            return
        self.status_label.configure(text="Removing %s\u2026" % ", ".join(names))

        def work():
            self.app.ctx.elevated(["pacman", "-Rns", "--noconfirm"] + names, capture=True)
            return names

        self.app.job(work, self._after_remove)

    def _after_remove(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self._pending_remove.clear()
        self.say("Removed: %s" % ", ".join(result), "ok")
        self.refresh_system_state()

    # -- state -------------------------------------------------------------
    def enter(self) -> None:
        self.refresh_system_state()

    def refresh_system_state(self) -> None:
        def work():
            return core.installed_packages(self.app.ctx), core.multilib_enabled()

        self.app.job(work, self._state_loaded)

    def _state_loaded(self, result) -> None:
        if isinstance(result, Exception):
            self.status_label.configure(text="Could not read the package list: %s" % result)
            return
        installed, multilib = result
        self._installed = installed
        self.multilib_pill.configure(
            text="32-bit support: %s" % ("on" if multilib else "off"),
            kind="ok" if multilib else "info",
        )
        self.multilib_button.configure(
            text="Disable 32-bit support" if multilib else "Enable 32-bit support"
        )
        self.status_label.configure(text="%d packages installed." % len(installed))
        self._refresh_list()


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #
class ServicesPage(PanelPage):
    name = "services"
    title = "Services"
    subtitle = "Background programs managed by systemd."
    nav_key = "services"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        card = Card(body, theme, "System services", "Select a unit, then act on it.")
        card.pack(fill="x", pady=(0, 16))
        self.tree = ttk.Treeview(
            card.body, columns=("active", "enabled"), show="tree headings", height=14,
            selectmode="browse",
        )
        self.tree.heading("#0", text="Unit")
        self.tree.heading("active", text="Active")
        self.tree.heading("enabled", text="At boot")
        self.tree.column("#0", width=360)
        self.tree.column("active", width=110)
        self.tree.column("enabled", width=130)
        self.tree.pack(fill="x")
        actions = tk.Frame(card.body, bg=theme.panel)
        actions.pack(fill="x", pady=(10, 0))
        for label, action in (
            ("Start", "start"), ("Stop", "stop"), ("Enable", "enable"), ("Disable", "disable"),
        ):
            ttk.Button(
                actions, text=label, style="Secondary.TButton",
                command=lambda value=action: self.act(value),
            ).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Refresh", style="Ghost.TButton", command=self.enter).pack(side="left")

        self.detail = tk.Label(
            body, text="", bg=theme.panel, fg=theme.muted, font=theme.small,
            anchor="w", justify="left", wraplength=620,
        )
        self.detail.pack(fill="x")
        Notice(
            body, theme,
            "Changing a service asks for authorisation through polkit (pkexec). "
            "Stopping a service affects the running system immediately.",
            kind="info",
        ).pack(fill="x", pady=(12, 0))

    def enter(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.app.job(lambda: core.services(self.app.ctx), self._loaded)

    def _loaded(self, result) -> None:
        if isinstance(result, Exception):
            self.detail.configure(text="Could not list services: %s" % result)
            return
        for unit, active, enabled in result:
            self.tree.insert("", "end", iid=unit, text=unit, values=(active, enabled))
        self.detail.configure(text="%d services listed." % len(result))

    def act(self, action: str) -> None:
        selection = self.tree.selection()
        if not selection:
            self.detail.configure(text="Select a service first.")
            return
        unit = selection[0]
        self.detail.configure(text="Running systemctl %s %s\u2026" % (action, unit))
        self.app.job(
            lambda: (self.app.ctx.elevated(["systemctl", action, unit], capture=True), unit)[1],
            self._acted,
        )

    def _acted(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self.enter()


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
class UsersPage(PanelPage):
    name = "users"
    title = "Users"
    subtitle = "The accounts on this computer."
    nav_key = "users"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        card = Card(body, theme, "Accounts", "Members of wheel may use sudo.")
        card.pack(fill="x", pady=(0, 16))
        self.listbox = tk.Listbox(
            card.body, height=6, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.panel, fg=theme.ink_soft,
            font=theme.mono, exportselection=False,
        )
        self.listbox.pack(fill="x")
        actions = tk.Frame(card.body, bg=theme.panel)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            actions, text="Change password", style="Secondary.TButton", command=self.change_password,
        ).pack(side="left")
        ttk.Button(
            actions, text="Add to wheel", style="Secondary.TButton", command=self.add_to_wheel,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Refresh", style="Ghost.TButton", command=self.enter).pack(
            side="left", padx=(10, 0)
        )

        form = Card(body, theme, "Create an account", "The home directory is created with the usual groups.")
        form.pack(fill="x", pady=(0, 16))
        row = tk.Frame(form.body, bg=theme.panel)
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="Name", bg=theme.panel, fg=theme.muted, font=theme.small, width=10, anchor="w").pack(side="left")
        self.new_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.new_name, width=24).pack(side="left")
        row = tk.Frame(form.body, bg=theme.panel)
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="Password", bg=theme.panel, fg=theme.muted, font=theme.small, width=10, anchor="w").pack(side="left")
        self.new_password = tk.StringVar()
        ttk.Entry(row, textvariable=self.new_password, show="\u2022", width=24).pack(side="left")
        ttk.Button(
            form.body, text="Create user", style="Accent.TButton", command=self.create_user,
        ).pack(anchor="w", pady=(6, 0))

    def enter(self) -> None:
        self.listbox.delete(0, "end")
        for name, uid, groups, home, shell in core.users():
            self.listbox.insert("end", "%-16s uid %-6d %s" % (name, uid, ",".join(groups)))

    def _selected(self) -> str:
        selection = self.listbox.curselection()
        if not selection:
            return ""
        line = self.listbox.get(int(selection[0]))
        return line.split()[0] if line else ""

    def change_password(self) -> None:
        name = self._selected()
        if not name:
            self.say("Select an account first.", "warn")
            return
        password = simpledialog.askstring(
            "Change password", "New password for %s:" % name, show="\u2022", parent=self.winfo_toplevel()
        )
        if not password:
            return
        if len(password) < 8:
            self.say("The password must be at least 8 characters.", "warn")
            return
        self.app.job(
            lambda: self.app.ctx.elevated(
                ["chpasswd"], input_text="%s:%s\n" % (name, password), capture=True
            ) and "Password changed for %s." % name,
            self._report,
        )

    def add_to_wheel(self) -> None:
        name = self._selected()
        if not name:
            self.say("Select an account first.", "warn")
            return
        self.app.job(
            lambda: self.app.ctx.elevated(["gpasswd", "-a", name, "wheel"], capture=True)
            and "%s can now use sudo." % name,
            self._report,
        )

    def create_user(self) -> None:
        name = self.new_name.get().strip()
        password = self.new_password.get()
        if not name or not password:
            self.say("Enter a name and a password.", "warn")
            return
        if len(password) < 8:
            self.say("The password must be at least 8 characters.", "warn")
            return

        def work():
            ctx = self.app.ctx
            ctx.elevated(
                ["useradd", "--create-home", "--shell", "/bin/bash", "--groups", "wheel,audio,video,storage", name],
                capture=True,
            )
            ctx.elevated(["chpasswd"], input_text="%s:%s\n" % (name, password), capture=True)
            return "Account %s created." % name

        self.app.job(work, self._report)

    def _report(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self.say(str(result), "ok")
        self.enter()


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #
class NetworkPage(PanelPage):
    name = "network"
    title = "Network"
    subtitle = "Interfaces and connections managed by NetworkManager."
    nav_key = "network"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        card = Card(body, theme, "Interfaces and addresses")
        card.pack(fill="x", pady=(0, 16))
        self.addresses = tk.Label(
            card.body, text="Reading\u2026", bg=theme.mono_bg, fg=theme.mono_fg, font=theme.mono,
            anchor="w", justify="left", padx=10, pady=8, wraplength=620,
        )
        self.addresses.pack(fill="x")
        self.active_label = tk.Label(
            card.body, text="", bg=theme.panel, fg=theme.ink_soft, font=theme.body,
            anchor="w", justify="left", wraplength=620,
        )
        self.active_label.pack(fill="x", pady=(8, 0))

        wifi = Card(body, theme, "Join a Wi-Fi network", "Uses NetworkManager (nmcli).")
        wifi.pack(fill="x", pady=(0, 16))
        row = tk.Frame(wifi.body, bg=theme.panel)
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="Network", bg=theme.panel, fg=theme.muted, font=theme.small, width=12, anchor="w").pack(side="left")
        self.ssid = tk.StringVar()
        ttk.Entry(row, textvariable=self.ssid, width=28).pack(side="left")
        row = tk.Frame(wifi.body, bg=theme.panel)
        row.pack(fill="x")
        tk.Label(row, text="Password", bg=theme.panel, fg=theme.muted, font=theme.small, width=12, anchor="w").pack(side="left")
        self.psk = tk.StringVar()
        ttk.Entry(row, textvariable=self.psk, show="\u2022", width=28).pack(side="left")
        ttk.Button(
            wifi.body, text="Connect", style="Accent.TButton", command=self.connect,
        ).pack(anchor="w", pady=(10, 0))

        scan = Card(body, theme, "Nearby networks", "Click Scan to list what the adapter can see.")
        scan.pack(fill="x")
        self.scan_list = tk.Listbox(
            scan.body, height=7, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=theme.line, bg=theme.panel, fg=theme.ink_soft,
            font=theme.mono, exportselection=False,
        )
        self.scan_list.pack(fill="x")
        ttk.Button(scan.body, text="Scan", style="Secondary.TButton", command=self.scan).pack(
            anchor="w", pady=(8, 0)
        )

    def enter(self) -> None:
        self.app.job(
            lambda: (core.interfaces(self.app.ctx), core.active_connections(self.app.ctx)),
            self._loaded,
        )

    def _loaded(self, result) -> None:
        if isinstance(result, Exception):
            self.addresses.configure(text="Could not read the interfaces: %s" % result)
            return
        interfaces, active = result
        self.addresses.configure(text="\n".join(interfaces) or "No interfaces reported.")
        self.active_label.configure(
            text="Active: " + (", ".join("%s (%s)" % (name, device) for name, device, _type in active) or "none")
        )

    def connect(self) -> None:
        ssid = self.ssid.get().strip()
        password = self.psk.get()
        if not ssid:
            self.say("Enter the network name.", "warn")
            return
        command = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            command += ["password", password]
        self.app.job(
            lambda: self.app.ctx.elevated(command, capture=True) and "Connected to %s." % ssid,
            lambda result: self._report(result, True),
        )

    def scan(self) -> None:
        self.scan_list.delete(0, "end")
        self.app.job(
            lambda: self.app.ctx.captured(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list"], quiet=True),
            self._scanned,
        )

    def _scanned(self, result) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        seen = set()
        for line in (result or "").splitlines():
            parts = line.split(":")
            if parts and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                signal = parts[1] if len(parts) > 1 else ""
                self.scan_list.insert("end", "%-28s %s%%" % (parts[0], signal))

    def _report(self, result, refresh: bool = False) -> None:
        if isinstance(result, Exception):
            self.say(str(result), "error")
            return
        self.say(str(result), "ok")
        if refresh:
            self.enter()


# --------------------------------------------------------------------------- #
# About
# --------------------------------------------------------------------------- #
class AboutPage(PanelPage):
    name = "about"
    title = "About"
    subtitle = "What this panel can and cannot do."
    nav_key = "about"

    def build(self, body: tk.Misc) -> None:
        theme = self.theme
        from . import __version__

        card = Card(body, theme, "Arch Control Panel", "Version %s" % __version__)
        card.pack(fill="x", pady=(0, 16))
        bullets(
            card.body, theme,
            (
                "Software by category from the official repositories, with repository search.",
                "32-bit support: turn the [multilib] repository on or off and install the matching libraries.",
                "Services, users, passwords, Wi-Fi and full system upgrades.",
                "Everything is executed locally; the panel opens no network port.",
            ),
        )

        Notice(
            body, theme,
            "Package changes ask for authorisation through polkit. The panel is a front end "
            "for pacman and systemctl: it never edits files behind their back, and the AUR is "
            "not supported.",
            kind="info",
        ).pack(fill="x")

        tk.Label(
            body,
            text="Installed from the Arch GUI installer  \u00b7  launcher: arch-control-panel",
            bg=theme.panel, fg=theme.faint, font=theme.small, anchor="w",
        ).pack(fill="x", pady=(14, 0))
