"""Colours, fonts and ttk styles for the installer window.

The window follows Windows Setup: a light grey chrome, a white content sheet,
Segoe UI, flat controls and one blue accent (#0067c0) that carries the primary
action.  Every page draws with these names, so the whole window can be retuned
in one place.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

INK = "#1a1a1a"
INK_SOFT = "#333333"
MUTED = "#5c5c5c"
FAINT = "#767676"
LINE = "#e0e0e0"
PANEL = "#ffffff"
CANVAS = "#f3f3f3"
FOOTER = "#f3f3f3"
ACCENT = "#0067c0"  # Windows 11 system accent
ACCENT_HOVER = "#1975c5"
ACCENT_PRESS = "#005a9e"
ACCENT_SOFT = "#e5f1fb"
RAIL = "#f3f3f3"
RAIL_LINE = "#e5e5e5"
RAIL_TEXT = "#1a1a1a"
RAIL_FAINT = "#6b6b6b"
OK = "#0f7b0f"
WARN = "#9d5d00"
FAIL = "#c42b1c"
MONO_BG = "#0c0c0c"
MONO_FG = "#cccccc"

#: Every colour the window draws with.  A palette entry that is not a
#: #rrggbb value reaches Tk as a colour name, and Tk answers with
#: "unknown color name ..." from somewhere inside a widget rather than from
#: the file that holds the value, so the names are checked explicitly.
COLOUR_NAMES = (
    "ink", "ink_soft", "muted", "faint", "line", "panel", "canvas", "footer",
    "accent", "accent_hover", "accent_press", "accent_soft", "rail", "rail_line",
    "rail_text", "rail_faint", "ok", "warn", "fail", "mono_bg", "mono_fg",
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

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
        self.display = tkfont.Font(root=root, family=family, size=26, weight="bold")
        self.big = tkfont.Font(root=root, family=family, size=26, weight="bold")
        # Windows Setup headlines are semibold; Tk only offers bold, so the
        # sizes are kept a step smaller than the weight would suggest.
        self.title = tkfont.Font(root=root, family=family, size=19, weight="bold")
        self.heading = tkfont.Font(root=root, family=family, size=11, weight="bold")
        self.body = tkfont.Font(root=root, family=family, size=10)
        self.body_bold = tkfont.Font(root=root, family=family, size=10, weight="bold")
        self.small = tkfont.Font(root=root, family=family, size=9)
        self.small_bold = tkfont.Font(root=root, family=family, size=9, weight="bold")
        self.mono = tkfont.Font(root=root, family=mono, size=9)


def colour_problems(source=None) -> list:
    """``[(name, value)]`` for every palette entry Tk would refuse to draw."""
    source = Theme if source is None else source
    bad = []
    for name in COLOUR_NAMES:
        value = getattr(source, name, None)
        if not isinstance(value, str) or not _HEX.match(value):
            bad.append((name, value))
    return bad


def _image(rows) -> tk.PhotoImage:
    """Build a PhotoImage from a list of rows of colour names."""
    image = tk.PhotoImage(width=len(rows[0]), height=len(rows))
    # Tk wants a list of rows, each row a list of colours: {c c c} {c c c} ...
    image.put(" ".join("{" + " ".join(row) + "}" for row in rows))
    return image


def _windows_indicators(style: ttk.Style, theme: Theme) -> None:
    """Draw Windows-style check boxes and radio buttons as ttk images.

    The ttk themes draw their own indicators (clam shows a cross when a box is
    checked), which reads as a Linux application.  These small images replace
    them with the filled accent box and the round dot Windows uses.
    """
    box = 16
    size = box + 7  # trailing surface keeps the box off its label
    surface = PANEL
    check_off = [[surface] * size for _ in range(box)]
    for i in range(box):
        for j in range(box):
            if i in (0, box - 1) or j in (0, box - 1):
                check_off[i][j] = "#8a8a8a"
    check_on = [[ACCENT if j < box else surface for j in range(size)] for _ in range(box)]
    for x, y in ((3, 8), (4, 9), (5, 10), (6, 11), (7, 10), (8, 9), (9, 8), (10, 7), (11, 6)):
        for dy in (0, -1):
            if 0 <= y + dy < box:
                check_on[y + dy][x] = "#ffffff"

    centre = (box - 1) / 2
    radio_off = [[surface] * size for _ in range(box)]
    radio_on = [[surface] * size for _ in range(box)]
    for i in range(box):
        for j in range(box):
            distance = ((i - centre) ** 2 + (j - centre) ** 2) ** 0.5
            if distance <= 7.3:
                radio_off[i][j] = "#8a8a8a"
            if distance <= 6.3:
                radio_off[i][j] = surface
                radio_on[i][j] = "#ffffff"
            if 6.3 < distance <= 7.3 or distance <= 3.1:
                radio_on[i][j] = ACCENT

    theme.img_check_off = _image(check_off)
    theme.img_check_on = _image(check_on)
    theme.img_radio_off = _image(radio_off)
    theme.img_radio_on = _image(radio_on)
    try:
        style.element_create(
            "Win.Check", "image", theme.img_check_off,
            ("selected", theme.img_check_on), ("disabled", theme.img_check_off),
        )
        style.element_create(
            "Win.Radio", "image", theme.img_radio_off,
            ("selected", theme.img_radio_on), ("disabled", theme.img_radio_off),
        )
    except tk.TclError:
        return
    def layout(indicator: str) -> list:
        return [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                (indicator, {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                    ("Checkbutton.label", {"sticky": "nswe"})]}),
            ]}),
        ]
    try:
        style.layout("Panel.TCheckbutton", layout("Win.Check"))
        style.layout("Panel.TRadiobutton", layout("Win.Radio"))
    except tk.TclError:
        pass


def apply(root: tk.Misc) -> Theme:
    theme = Theme(root)
    try:
        root.configure(bg=CANVAS)  # type: ignore[attr-defined]
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
    # Windows buttons are flat rectangles: a filled accent for the default
    # action and a hairline white button for everything else.
    style.configure(
        "Accent.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0,
        focusthickness=0, padding=(24, 9), font=theme.body, relief="flat",
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", "#f0f0f0"), ("pressed", ACCENT_PRESS), ("active", ACCENT_HOVER)],
        foreground=[("disabled", "#a0a0a0")],
    )
    style.configure(
        "Secondary.TButton", background="#fbfbfb", foreground=INK, borderwidth=1,
        bordercolor="#d1d1d1", lightcolor="#fbfbfb", darkcolor="#fbfbfb",
        focusthickness=0, padding=(22, 8), font=theme.body, relief="flat",
    )
    style.map(
        "Secondary.TButton",
        background=[("disabled", "#f5f5f5"), ("pressed", "#e8e8e8"), ("active", "#f0f0f0")],
        bordercolor=[("disabled", "#e5e5e5"), ("active", "#b8b8b8")],
        foreground=[("disabled", "#a0a0a0"), ("active", INK)],
    )
    # Ghost buttons read as Windows text links.
    style.configure(
        "Ghost.TButton", background=PANEL, foreground=ACCENT, borderwidth=0,
        focusthickness=0, padding=(16, 8), font=theme.body, relief="flat",
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", PANEL), ("pressed", "#eaf1f8"), ("active", "#f2f7fc")],
        foreground=[("disabled", FAINT), ("active", ACCENT_PRESS)],
    )

    # -- inputs ------------------------------------------------------------
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name, fieldbackground=PANEL, background=PANEL, foreground=INK,
            bordercolor="#d1d1d1", lightcolor="#d1d1d1", darkcolor="#d1d1d1",
            insertcolor=INK, padding=(9, 6), arrowsize=13,
        )
        style.map(
            name,
            bordercolor=[("focus", ACCENT), ("disabled", LINE)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
            fieldbackground=[("disabled", "#f5f5f5"), ("readonly", PANEL)],
            foreground=[("disabled", "#a0a0a0")],
        )

    style.configure(
        "TCombobox", fieldbackground=PANEL, background=PANEL, foreground=INK,
        bordercolor="#d1d1d1", lightcolor="#d1d1d1", darkcolor="#d1d1d1",
        arrowcolor=INK_SOFT, arrowsize=14, padding=(8, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL), ("disabled", "#f5f5f5")],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
        foreground=[("disabled", "#a0a0a0")],
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
    _windows_indicators(style, theme)

    # -- data views --------------------------------------------------------
    style.configure(
        "Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
        borderwidth=0, rowheight=30, font=theme.body,
    )
    style.configure(
        "Treeview.Heading", background="#fbfbfb", foreground=MUTED, relief="flat",
        borderwidth=0, padding=(10, 8), font=theme.small_bold,
    )
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    style.map("Treeview.Heading", background=[("active", "#f0f0f0")])

    style.configure(
        "Accent.Horizontal.TProgressbar", troughcolor="#e5e5e5", background=ACCENT,
        bordercolor="#e5e5e5", lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=0,
        thickness=4,
    )
    style.configure(
        "Vertical.TScrollbar", background="#c9c9c9", troughcolor=PANEL,
        bordercolor=PANEL, arrowcolor=MUTED, borderwidth=0, arrowsize=12, width=10,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#a6a6a6")])

    for pattern, value in (
        ("*TCombobox*Listbox.background", PANEL),
        ("*TCombobox*Listbox.foreground", INK),
        ("*TCombobox*Listbox.selectBackground", ACCENT_SOFT),
        ("*TCombobox*Listbox.selectForeground", INK),
        ("*TCombobox*Listbox.borderWidth", 0),
        ("*TButton.focusColor", PANEL),
        ("*TCheckbutton.focusColor", PANEL),
        ("*TRadiobutton.focusColor", PANEL),
    ):
        try:
            root.option_add(pattern, value)  # type: ignore[attr-defined]
        except tk.TclError:
            pass
    return theme
