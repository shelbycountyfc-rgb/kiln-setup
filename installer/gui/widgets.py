"""Tk building blocks shared by the pages."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import Theme


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
class ScrollArea(tk.Frame):
    """Vertically scrolling container.  Put page content in :attr:`body`."""

    def __init__(self, master: tk.Misc, theme: Theme, background: str | None = None) -> None:
        bg = background or theme.panel
        super().__init__(master, bg=bg)
        self.theme = theme
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview, style="Vertical.TScrollbar"
        )
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        # The scrollbar stays in place.  Showing and hiding it from inside the
        # scroll callback changes the canvas width, which changes the wrapped
        # text height, which toggles the scrollbar again: a geometry loop that
        # never settles and leaves Tk spinning in its idle handler.
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._body_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        # Bind once for the whole application and scroll only when the pointer is
        # inside this area.  Binding Enter/Leave on the canvas instead stops the
        # wheel the moment the pointer is over a card or an entry -- which is
        # most of the page, so anything below the fold can never be reached.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._wheel, add="+")

    def _body_resized(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_resized(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)

    def _owns(self, widget: tk.Misc | None) -> bool:
        """True when *widget* is this area or sits inside it."""
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _wheel(self, event: tk.Event) -> None:
        # Every scroll area answers this binding; only the one under the pointer
        # should move.
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError, AttributeError):
            under = None
        if under is None or not self._owns(under):
            return
        number = getattr(event, "num", None)
        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        else:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)


class Card(tk.Frame):
    """A white sheet with a hairline border and an optional heading."""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        title: str = "",
        subtitle: str = "",
        padx: int = 18,
        pady: int = 16,
    ) -> None:
        super().__init__(
            master, bg=theme.panel, bd=0, highlightthickness=1,
            highlightbackground=theme.line, highlightcolor=theme.line,
        )
        self.theme = theme
        self.body = tk.Frame(self, bg=theme.panel)
        self.body.pack(fill="both", expand=True, padx=padx, pady=pady)
        if title:
            tk.Label(
                self.body, text=title, bg=theme.panel, fg=theme.ink,
                font=theme.heading, anchor="w",
            ).pack(fill="x")
        if subtitle:
            tk.Label(
                self.body, text=subtitle, bg=theme.panel, fg=theme.muted,
                font=theme.small, anchor="w", justify="left", wraplength=620,
            ).pack(fill="x", pady=(4, 0))
        if title or subtitle:
            tk.Frame(self.body, bg=theme.line, height=1).pack(fill="x", pady=(13, 13))


class Form(tk.Frame):
    """Label/control rows with optional hint text under the control."""

    def __init__(self, master: tk.Misc, theme: Theme) -> None:
        super().__init__(master, bg=theme.panel)
        self.theme = theme
        self.index = 0

    def field(self, label: str, hint: str = "") -> tk.Frame:
        row = tk.Frame(self, bg=self.theme.panel)
        row.grid(row=self.index, column=0, columnspan=2, sticky="ew", pady=(0, 13))
        row.columnconfigure(1, weight=1)
        tk.Label(
            row, text=label, bg=self.theme.panel, fg=self.theme.ink_soft,
            font=self.theme.body, anchor="w", width=18, justify="left",
        ).grid(row=0, column=0, sticky="nw", padx=(0, 16), pady=(7, 0))
        holder = tk.Frame(row, bg=self.theme.panel)
        holder.grid(row=0, column=1, sticky="ew")
        if hint:
            tk.Label(
                holder, text=hint, bg=self.theme.panel, fg=self.theme.muted,
                font=self.theme.small, anchor="w", justify="left", wraplength=430,
            ).pack(side="bottom", fill="x", pady=(5, 0))
        self.index += 1
        return holder

    def note(self, text: str) -> tk.Label:
        label = tk.Label(
            self, text=text, bg=self.theme.panel, fg=self.theme.muted,
            font=self.theme.small, anchor="w", justify="left", wraplength=560,
        )
        label.grid(row=self.index, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.index += 1
        return label


# --------------------------------------------------------------------------- #
# Navigation rail
# --------------------------------------------------------------------------- #
class StepRail(tk.Frame):
    """The step list on the left edge, styled like the Windows Settings nav."""

    def __init__(
        self, master: tk.Misc, theme: Theme, width: int = 236, on_select=None
    ) -> None:
        """A step list on the left edge.

        With ``on_select`` the rows are a menu: a click anywhere on a row calls
        ``on_select(key)``.  Without it the rail is the installer's progress
        indicator, which the Back and Next buttons move along.
        """
        super().__init__(master, bg=theme.rail, width=width)
        self.theme = theme
        self.on_select = on_select
        self.pack_propagate(False)
        self._rows: dict[str, tuple] = {}
        self._order: list = []

        from .. import brand as branding

        brand = tk.Frame(self, bg=theme.rail)
        brand.pack(fill="x", padx=20, pady=(22, 0))
        mark = tk.Canvas(brand, width=20, height=20, bg=theme.rail, highlightthickness=0, bd=0)
        mark.pack(side="left", pady=(2, 0))
        # A flat accent tile with the Arch chevron, drawn rather than shipped.
        mark.create_rectangle(0, 0, 20, 20, fill=theme.accent, outline="")
        mark.create_polygon(4, 15, 10, 4, 16, 15, 12.6, 15, 10, 9, 7.4, 15, fill="#ffffff", outline="")
        names = tk.Frame(brand, bg=theme.rail)
        names.pack(side="left", padx=(10, 0), fill="x", expand=True)
        tk.Label(
            names, text=branding.PRODUCT, bg=theme.rail, fg=theme.ink, font=theme.brand, anchor="w",
        ).pack(fill="x")
        tk.Label(
            names, text="Setup", bg=theme.rail, fg=theme.rail_faint,
            font=theme.small, anchor="w",
        ).pack(fill="x")
        tk.Frame(self, bg=theme.rail_line, height=1).pack(fill="x", padx=20, pady=(16, 12))

        self.list = tk.Frame(self, bg=theme.rail)
        self.list.pack(fill="both", expand=True, padx=(8, 8))
        self.footer = tk.Label(
            self, text="", bg=theme.rail, fg=theme.rail_faint, font=theme.small,
            anchor="w", justify="left", wraplength=190,
        )
        self.footer.pack(fill="x", padx=20, pady=(8, 16))

    def set_steps(self, steps) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        self._rows = {}
        self._order = [key for key, _label in steps]
        for index, (key, label) in enumerate(steps, start=1):
            row = tk.Frame(self.list, bg=self.theme.rail)
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, bg=self.theme.rail, width=3)
            bar.pack(side="left", fill="y")
            dot = tk.Canvas(row, width=22, height=22, bg=self.theme.rail, highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(10, 0), pady=6)
            text = tk.Label(
                row, text=label, bg=self.theme.rail, fg=self.theme.rail_text,
                font=self.theme.body, anchor="w",
            )
            text.pack(side="left", padx=(10, 8), fill="x", expand=True)
            self._rows[key] = (row, bar, dot, text, index)
            self.set_state(key, "pending")
            if self.on_select is not None:
                # The label and the dot are children of the row, so each of
                # them needs the binding too: Tk delivers a click to the
                # innermost widget and does not bubble it to the parent.
                for widget in (row, bar, dot, text):
                    widget.configure(cursor="hand2")
                    widget.bind(
                        "<Button-1>", lambda _event, step=key: self.choose(step)
                    )

    def choose(self, key: str) -> None:
        """Report a click on ``key`` to whoever asked for one."""
        if self.on_select is not None:
            self.on_select(key)

    def set_state(self, key: str, state: str) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        frame, bar, dot, text, index = row
        theme = self.theme
        # The selected step sits on a white surface with an accent edge, the way
        # a selected item does in Windows Settings.
        surface = theme.panel if state == "active" else theme.rail
        for widget in (frame, bar, dot, text):
            try:
                widget.configure(bg=surface)
            except tk.TclError:
                pass
        if state == "active":
            bar.configure(bg=theme.accent)
        dot.delete("all")
        if state == "done":
            dot.create_oval(2, 2, 20, 20, fill=theme.ok, outline=theme.ok)
            dot.create_text(11, 11, text="\u2713", fill="#ffffff", font=theme.small_bold)
            text.configure(fg=theme.muted, font=theme.body)
        elif state == "active":
            dot.create_oval(2, 2, 20, 20, fill=theme.accent, outline=theme.accent)
            dot.create_text(11, 11, text=str(index), fill="#ffffff", font=theme.small_bold)
            text.configure(fg=theme.ink, font=theme.body_bold)
        elif state == "failed":
            dot.create_oval(2, 2, 20, 20, fill=theme.fail, outline=theme.fail)
            dot.create_text(11, 11, text="!", fill="#ffffff", font=theme.small_bold)
            text.configure(fg=theme.fail, font=theme.body)
        else:
            dot.create_oval(2, 2, 20, 20, fill=surface, outline="#c7c7c7", width=1)
            dot.create_text(11, 11, text=str(index), fill=theme.faint, font=theme.small)
            text.configure(fg=theme.muted, font=theme.body)


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
class Banner(tk.Frame):
    """Inline message strip used for validation errors and failures."""

    def __init__(self, master: tk.Misc, theme: Theme) -> None:
        super().__init__(master, bg=theme.panel)
        self.theme = theme
        self.bar = tk.Frame(self, bg=theme.fail, width=3)
        self.bar.pack(side="left", fill="y")
        self.label = tk.Label(
            self, text="", bg="#fde7e9", fg="#a4262c", font=theme.body, anchor="w",
            justify="left", wraplength=660, padx=12, pady=10,
        )
        self.label.pack(side="left", fill="both", expand=True)

    def show(self, text: str, kind: str = "error") -> None:
        palette = {
            "error": (self.theme.fail, "#fde7e9", "#a4262c"),
            "warn": (self.theme.warn, "#fff4ce", "#9d5d00"),
            "ok": (self.theme.ok, "#dff6dd", self.theme.ok),
        }
        bar, background, foreground = palette.get(kind, palette["error"])
        self.bar.configure(bg=bar)
        self.label.configure(bg=background, fg=foreground, text=text)
        self.grid() if self.winfo_manager() == "" else None


class Notice(tk.Frame):
    """Static note printed on a page (not a validation message)."""

    def __init__(self, master: tk.Misc, theme: Theme, text: str, kind: str = "warn") -> None:
        colors = {"warn": ("#c98a00", "#fff4ce"), "info": (theme.accent, "#f0f6fc")}
        bar, background = colors.get(kind, colors["warn"])
        super().__init__(master, bg=background)
        tk.Frame(self, bg=bar, width=3).pack(side="left", fill="y")
        tk.Label(
            self, text=text, bg=background, fg=theme.ink_soft, font=theme.body,
            anchor="w", justify="left", wraplength=660, padx=12, pady=10,
        ).pack(side="left", fill="both", expand=True)


class Pill(tk.Label):
    """A small rounded badge, used for counts and states."""

    def __init__(self, master: tk.Misc, theme: Theme, text: str = "", kind: str = "info") -> None:
        backgrounds = {"info": theme.accent_soft, "ok": "#dff6dd", "warn": "#fff4ce"}
        foregrounds = {"info": theme.accent_press, "ok": theme.ok, "warn": theme.warn}
        super().__init__(
            master, text=text, bg=backgrounds.get(kind, theme.accent_soft),
            fg=foregrounds.get(kind, theme.accent_press), font=theme.small_bold,
            padx=9, pady=3,
        )


# --------------------------------------------------------------------------- #
# Selection card
# --------------------------------------------------------------------------- #
class OptionCard(tk.Frame):
    """A clickable card with a radio marker, title and one-line description."""

    def __init__(
        self,
        master: tk.Misc,
        theme: Theme,
        title: str,
        subtitle: str,
        value: str,
        variable: tk.StringVar,
        command=None,
        width: int = 300,
    ) -> None:
        super().__init__(
            master, bg=theme.panel, bd=0, highlightthickness=1,
            highlightbackground=theme.line, highlightcolor=theme.line,
        )
        self.theme = theme
        self.value = value
        self.variable = variable
        self.command = command
        inner = tk.Frame(self, bg=theme.panel, padx=13, pady=11)
        inner.pack(fill="both", expand=True)
        self.marker = tk.Canvas(inner, width=16, height=16, bg=theme.panel, highlightthickness=0, bd=0)
        self.marker.pack(side="left", anchor="n", pady=(2, 0))
        text = tk.Frame(inner, bg=theme.panel)
        text.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.title_label = tk.Label(
            text, text=title, bg=theme.panel, fg=theme.ink, font=theme.body_bold,
            anchor="w", justify="left",
        )
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(
            text, text=subtitle, bg=theme.panel, fg=theme.muted, font=theme.small,
            anchor="w", justify="left", wraplength=width - 60,
        )
        self.subtitle_label.pack(fill="x", pady=(2, 0))
        for widget in (self, inner, text, self.title_label, self.subtitle_label, self.marker):
            widget.bind("<Button-1>", self._select)
        self._paint()

    def _select(self, _event=None) -> None:
        self.variable.set(self.value)
        if self.command:
            self.command()

    def _paint(self) -> None:
        selected = self.variable.get() == self.value
        try:
            self.configure(
                highlightbackground=self.theme.accent if selected else self.theme.line,
                highlightcolor=self.theme.accent if selected else self.theme.line,
            )
        except tk.TclError:
            pass
        self.marker.delete("all")
        if selected:
            self.marker.create_oval(1, 1, 15, 15, outline=self.theme.accent, width=2, fill=self.theme.panel)
            self.marker.create_oval(5, 5, 11, 11, outline=self.theme.accent, fill=self.theme.accent)
        else:
            self.marker.create_oval(1, 1, 15, 15, outline="#8a8a8a", width=1, fill=self.theme.panel)

    def refresh(self) -> None:
        self._paint()


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def hint(master: tk.Misc, theme: Theme, text: str, wraplength: int = 620) -> tk.Label:
    return tk.Label(
        master, text=text, bg=theme.panel, fg=theme.muted, font=theme.small,
        anchor="w", justify="left", wraplength=wraplength,
    )


def _wrap_margin(label: tk.Label) -> int:
    try:
        pad = int(str(label.cget("padx")))
    except (TypeError, ValueError, tk.TclError):
        pad = 0
    return 2 * pad + 6


def _follow_width(label: tk.Label) -> None:
    state = [0]

    def resize(event: tk.Event) -> None:
        width = int(getattr(event, "width", 0))
        # Ignore sub-pixel churn: reacting to every pixel of a resize makes the
        # label's height oscillate against the container that sized it.
        if width < 60 or abs(width - state[0]) < 8:
            return
        state[0] = width
        try:
            label.configure(wraplength=max(60, width - _wrap_margin(label)))
        except tk.TclError:
            pass

    try:
        label.bind("<Configure>", resize, add="+")
    except tk.TclError:
        pass


def fit_labels(container: tk.Misc) -> None:
    """Make every wrapped label follow the width its container gives it."""
    stack = [container]
    while stack:
        widget = stack.pop()
        try:
            children = widget.winfo_children()
        except tk.TclError:
            continue
        for child in children:
            if isinstance(child, tk.Label):
                try:
                    wrapped = int(child.cget("wraplength"))
                except (TypeError, ValueError, tk.TclError):
                    wrapped = 0
                if wrapped > 0:
                    _follow_width(child)
            stack.append(child)


def bullets(master: tk.Misc, theme: Theme, items, wraplength: int = 600) -> tk.Frame:
    box = tk.Frame(master, bg=theme.panel)
    for text in items:
        row = tk.Frame(box, bg=theme.panel)
        row.pack(fill="x", pady=(0, 7))
        tk.Label(row, text="\u2713", bg=theme.panel, fg=theme.accent, font=theme.body_bold).pack(
            side="left", anchor="n"
        )
        tk.Label(
            row, text=text, bg=theme.panel, fg=theme.ink_soft, font=theme.body,
            anchor="w", justify="left", wraplength=wraplength,
        ).pack(side="left", fill="x", expand=True, padx=(10, 0))
    # Every caller wants the list laid out where it was created, and forgetting
    # to pack the returned frame leaves the bullets invisible.
    box.pack(fill="x")
    return box
