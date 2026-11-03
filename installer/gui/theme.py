"""Colours, fonts and ttk styles for the installer window.

The look follows a Windows Setup / OOBE screen: a white content sheet, a dark
navigation rail, one blue accent for the primary action.  Every page draws with
these names so the whole window can be retuned in one place.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

INK = "#14161a"
INK_SOFT = "#33373f"
MUTED = "#5f6672"
FAINT = "#8a919d"
LINE = "#e2e4e9"
PANEL = "#ffffff"
CANVAS = "#f4f5f7"
FOOTER = "#fafbfc"
ACCENT = "#1793d1"  # Arch blue
ACCENT_HOVER = "#1279ae"
ACCENT_PRESS = "#0d628c"
ACCENT_SOFT = "#e8f4fb"
RAIL = "#14171c"
RAIL_LINE = "#272c34"
RAIL_TEXT = "#c6ccd6"
RAIL_FAINT = "#79828f"
OK = "#127a41"
WARN = "#8a5a00"
FAIL = "#b3261e"
MONO_BG = "#101318"
MONO_FG = "#d7dce4"

SPACE_XS = 6
SPACE_S = 10
SPACE_M = 16
SPACE_L = 24
SPACE_XL = 32

_SANS = (
    "Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans",
    "Liberation Sans", "Ubuntu", "Cantarell", "Helvetica",
)
_MONO = (
    "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono",
    "Noto Sans Mono", "Courier New",
)


def _pick_family(root: tk.Misc, candidates) -> str:
    try:
        available = {name.lower(): name for name in tkfont.families(root)}
    except tk.TclError:
        available = {}
    for name in candidates:
        if name.lower() in available:
            return available[name.lower()]
    try:
        return tkfont.nametofont("TkDefaultFont").actual("family")
    except tk.TclError:
        return "TkDefaultFont"


class Theme:
    ink = INK
    ink_soft = INK_SOFT
    muted = MUTED
    faint = FAINT
    line = LINE
    panel = PANEL
    canvas = CANVAS
    footer = FOOTER
    accent = ACCENT
    accent_hover = ACCENT_HOVER
    accent_press = ACCENT_PRESS
    accent_soft = ACCENT_SOFT
    rail = RAIL
    rail_line = RAIL_LINE
    rail_text = RAIL_TEXT
    rail_faint = RAIL_FAINT
    ok = OK
    warn = WARN
    fail = FAIL
    mono_bg = MONO_BG
    mono_fg = MONO_FG

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        family = _pick_family(root, _SANS)
        mono = _pick_family(root, _MONO)
        self.family = family
        self.mono_family = mono
        self.brand = tkfont.Font(root=root, family=family, size=13, weight="bold")
        self.display = tkfont.Font(root=root, family=family, size=24, weight="bold")
        self.big = tkfont.Font(root=root, family=family, size=32, weight="bold")
        self.title = tkfont.Font(root=root, family=family, size=18, weight="bold")
        self.heading = tkfont.Font(root=root, family=family, size=11, weight="bold")
        self.body = tkfont.Font(root=root, family=family, size=10)
        self.body_bold = tkfont.Font(root=root, family=family, size=10, weight="bold")
        self.small = tkfont.Font(root=root, family=family, size=9)
        self.small_bold = tkfont.Font(root=root, family=family, size=9, weight="bold")
        self.mono = tkfont.Font(root=root, family=mono, size=9)


def apply(root: tk.Misc) -> Theme:
    theme = Theme(root)
    try:
        root.configure(bg=PANEL)  # type: ignore[attr-defined]
    except tk.TclError:
        pass

    style = ttk.Style(root)
    for name in ("clam", "alt", "default"):
        if name in style.theme_names():
            style.theme_use(name)
            break

    style.configure(".", background=PANEL, foreground=INK, font=theme.body)
    style.configure("TFrame", background=PANEL)
    style.configure("TLabel", background=PANEL, foreground=INK)

    # -- buttons -----------------------------------------------------------
    style.configure(
        "Accent.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0,
        focusthickness=0, padding=(24, 11), font=theme.body_bold, relief="flat",
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", "#cfe4f1"), ("pressed", ACCENT_PRESS), ("active", ACCENT_HOVER)],
        foreground=[("disabled", "#f2f8fc")],
    )
    style.configure(
        "Secondary.TButton", background=PANEL, foreground=INK, borderwidth=1,
        bordercolor=LINE, lightcolor=PANEL, darkcolor=PANEL, focusthickness=0,
        padding=(20, 10), font=theme.body, relief="flat",
    )
    style.map(
        "Secondary.TButton",
        background=[("disabled", PANEL), ("pressed", "#eef0f4"), ("active", "#f5f6f9")],
        bordercolor=[("disabled", LINE), ("active", "#c9ced8")],
        foreground=[("disabled", FAINT), ("active", INK)],
    )
    style.configure(
        "Ghost.TButton", background=PANEL, foreground=INK_SOFT, borderwidth=0,
        focusthickness=0, padding=(18, 10), font=theme.body, relief="flat",
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", PANEL), ("pressed", "#e6e9ee"), ("active", "#eceff3")],
        foreground=[("disabled", FAINT), ("active", INK)],
    )

    # -- inputs ------------------------------------------------------------
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name, fieldbackground=PANEL, background=PANEL, foreground=INK,
            bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, insertcolor=INK,
            padding=(10, 8), arrowsize=13,
        )
        style.map(
            name,
            bordercolor=[("focus", ACCENT), ("disabled", LINE)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
            fieldbackground=[("disabled", "#f2f3f5"), ("readonly", PANEL)],
            foreground=[("disabled", FAINT)],
        )

    style.configure(
        "TCombobox", fieldbackground=PANEL, background=PANEL, foreground=INK,
        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, arrowcolor=INK_SOFT,
        arrowsize=14, padding=(8, 6),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL), ("disabled", "#f2f3f5")],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
        foreground=[("disabled", FAINT)],
        arrowcolor=[("active", ACCENT)],
    )

    for name in ("Panel.TCheckbutton", "Panel.TRadiobutton"):
        style.configure(
            name, background=PANEL, foreground=INK, focuscolor=PANEL,
            padding=(0, 4), font=theme.body,
        )
        style.map(
            name, background=[("active", PANEL), ("pressed", PANEL)],
            foreground=[("disabled", FAINT)],
        )

    # -- data views --------------------------------------------------------
    style.configure(
        "Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
        borderwidth=0, rowheight=28, font=theme.body,
    )
    style.configure(
        "Treeview.Heading", background=CANVAS, foreground=MUTED, relief="flat",
        borderwidth=0, padding=(10, 8), font=theme.small_bold,
    )
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    style.map("Treeview.Heading", background=[("active", "#e9ebef")])

    style.configure(
        "Accent.Horizontal.TProgressbar", troughcolor="#e6e9ee", background=ACCENT,
        bordercolor="#e6e9ee", lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=0,
        thickness=6,
    )
    style.configure(
        "Vertical.TScrollbar", background="#c7ccd4", troughcolor=PANEL,
        bordercolor=PANEL, arrowcolor=MUTED, borderwidth=0, arrowsize=12, width=10,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#adb4bf")])

    for pattern, value in (
        ("*TCombobox*Listbox.background", PANEL),
        ("*TCombobox*Listbox.foreground", INK),
        ("*TCombobox*Listbox.selectBackground", ACCENT_SOFT),
        ("*TCombobox*Listbox.selectForeground", INK),
        ("*TCombobox*Listbox.borderWidth", 0),
    ):
        try:
            root.option_add(pattern, value)  # type: ignore[attr-defined]
        except tk.TclError:
            pass
    return theme
