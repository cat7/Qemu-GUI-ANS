"""The front panel: the four-line LCD, the keyswitch and the reset button,
with the machine's state beside them.

The LCD text comes from the emulator's LCD socket (lcd.LcdClient); the
state and the key position from its QMP connection, asked every second on a
thread of its own so a slow emulator never stalls the window.
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
from typing import Callable

from . import lcd, model, paths, qmp

GREY = "gray"

# A yellow-green backlit character LCD; dark when the machine is off.
LCD_BEZEL = "#1d1f1c"
LCD_GLASS_ON = "#a9c34a"
LCD_CELL_ON = "#9db73f"
LCD_INK = "#1b2610"
LCD_GLASS_OFF = "#3b4426"
LCD_CELL_OFF = "#363e23"

POLL_MS = 150
STATUS_SECONDS = 1.0


class LcdView(tk.Canvas):
    """4 x 20 character cells, drawn from a list of four strings."""

    PAD = 10
    GAP = 2

    def __init__(self, master, size: int = 15):
        family = "Menlo" if paths.HOST_PLATFORM == "darwin" else (
            "Consolas" if paths.is_windows() else "DejaVu Sans Mono")
        self.font = tkfont.Font(family=family, size=size, weight="bold")
        self.cw = self.font.measure("M") + 2
        self.ch = self.font.metrics("linespace") + 2
        w = 2 * self.PAD + lcd.COLS * (self.cw + self.GAP) - self.GAP
        h = 2 * self.PAD + lcd.LINES * (self.ch + self.GAP) - self.GAP
        super().__init__(master, width=w + 12, height=h + 12, background=LCD_BEZEL,
                         highlightthickness=0, borderwidth=0)
        self.glass = self.create_rectangle(6, 6, 6 + w, 6 + h, outline="", fill=LCD_GLASS_OFF)
        self.cells = []
        self.chars = []
        for r in range(lcd.LINES):
            row_cells, row_chars = [], []
            for c in range(lcd.COLS):
                x = 6 + self.PAD + c * (self.cw + self.GAP)
                y = 6 + self.PAD + r * (self.ch + self.GAP)
                row_cells.append(self.create_rectangle(x, y, x + self.cw, y + self.ch,
                                                       outline="", fill=LCD_CELL_OFF))
                row_chars.append(self.create_text(x + self.cw / 2, y + self.ch / 2, text="",
                                                  font=self.font, fill=LCD_INK))
            self.cells.append(row_cells)
            self.chars.append(row_chars)
        self.lit: bool | None = None
        self.shown = [""] * lcd.LINES
        self.show(False, [])

    def show(self, lit: bool, lines: list) -> None:
        if lit != self.lit:
            self.lit = lit
            self.itemconfigure(self.glass, fill=LCD_GLASS_ON if lit else LCD_GLASS_OFF)
            for row in self.cells:
                for cell in row:
                    self.itemconfigure(cell, fill=LCD_CELL_ON if lit else LCD_CELL_OFF)
        for r in range(lcd.LINES):
            text = (lines[r] if r < len(lines) else "").ljust(lcd.COLS)[:lcd.COLS]
            if not lit:
                text = lcd.BLANK
            if text == self.shown[r]:
                continue
            self.shown[r] = text
            for c, item in enumerate(self.chars[r]):
                self.itemconfigure(item, text=text[c])

    def text(self) -> list:
        """What the panel shows now (for tests)."""
        return list(self.shown)


class StatusPoller(threading.Thread):
    """Asks one machine's QMP port for its state and key position."""

    def __init__(self):
        super().__init__(daemon=True, name="qmp-status")
        self._lock = threading.Lock()
        self._port: int | None = None
        self._result: dict | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    def set_port(self, port: int | None) -> None:
        with self._lock:
            if port != self._port:
                self._port = port
                self._result = None
        self._wake.set()

    def result(self) -> dict | None:
        with self._lock:
            return self._result

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                port = self._port
            res = None
            if port is not None:
                try:
                    res = qmp.status(port, timeout=1.0)
                except qmp.QMPError:
                    res = None
            with self._lock:
                if port == self._port:
                    self._result = res
            self._wake.wait(STATUS_SECONDS)
            self._wake.clear()


class FrontPanel(ttk.LabelFrame):
    """``on_key(position)`` runs when the key is turned (the window saves it
    in the record; while the machine runs, the panel also turns it live)."""

    def __init__(self, master, on_key: Callable[[str], None]):
        super().__init__(master, text="Front panel", padding=8)
        self.on_key = on_key
        self.view = LcdView(self)
        self.view.grid(row=0, column=0, rowspan=4, sticky="nw")
        side = ttk.Frame(self)
        side.grid(row=0, column=1, sticky="nw", padx=(14, 0))
        self.state_label = ttk.Label(side, text="Off", font=("", 0, "bold"))
        self.state_label.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(side, text="Key:").grid(row=1, column=0, sticky="w", pady=(10, 2))
        self.key_var = tk.StringVar(value=model.KEYSWITCH_DEFAULT)
        self.key_buttons = []
        for i, pos in enumerate(model.KEYSWITCH):
            rb = ttk.Radiobutton(side, text=model.KEYSWITCH_LABELS[pos], value=pos,
                                 variable=self.key_var, command=self._key_turned)
            rb.grid(row=2 + i, column=0, columnspan=3, sticky="w")
            self.key_buttons.append(rb)
        self.reset_button = ttk.Button(side, text="Reset", command=self._reset)
        self.reset_button.grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.note = ttk.Label(self, text="", foreground=GREY)
        self.note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.name: str | None = None
        self.record_key = model.KEYSWITCH_DEFAULT
        self.qmp_port: int | None = None
        self.lcd_client = lcd.LcdClient()
        self.lcd_client.start()
        self.poller = StatusPoller()
        self.poller.start()
        self._version = -1
        self._after = self.after(POLL_MS, self._tick)
        self.bind("<Destroy>", self._destroyed, add="+")

    # ---- which machine
    def set_machine(self, name: str | None, keyswitch: str = model.KEYSWITCH_DEFAULT,
                    qmp_port: int | None = None, lcd_port: int | None = None) -> None:
        if name != self.name:
            self._version = -1
        self.name = name
        self.record_key = keyswitch
        self.qmp_port = qmp_port
        self.lcd_client.set_port(lcd_port)
        self.poller.set_port(qmp_port)
        self.key_var.set(keyswitch)
        self._show_state()

    def running(self) -> bool:
        return self.poller.result() is not None

    # ---- actions
    def _key_turned(self):
        pos = self.key_var.get()
        if self.name is None:
            return
        if self.running() and self.qmp_port is not None:
            try:
                qmp.set_keyswitch(self.qmp_port, pos)
            except qmp.QMPError as e:
                messagebox.showerror("Keyswitch", f"The key could not be turned.\n\n{e}",
                                     parent=self)
        self.record_key = pos
        self.on_key(pos)

    def _reset(self):
        if self.qmp_port is None or not self.running():
            return
        if not messagebox.askyesno("Reset", f"Press the reset button of “{self.name}”? "
                                            "AIX loses whatever it has not written to disk.",
                                   icon="warning", default="no", parent=self):
            return
        try:
            qmp.reset(self.qmp_port)
        except qmp.QMPError as e:
            messagebox.showerror("Reset", str(e), parent=self)

    # ---- drawing
    def _show_state(self):
        res = self.poller.result()
        if self.name is None:
            text, note = "", ""
        elif res is None:
            text = "Off"
            note = "The LCD lights up when the machine starts."
        else:
            text = qmp.status_label(res.get("status", "")) or "Running"
            note = ""
            live = res.get("keyswitch")
            if live in model.KEYSWITCH and live != self.key_var.get():
                self.key_var.set(live)
        if res is not None and not self.lcd_client.state.connected:
            note = "Waiting for the LCD…"
        self.state_label.config(text=text)
        self.note.config(text=note)
        self.reset_button.state(["!disabled"] if res is not None else ["disabled"])
        for rb in self.key_buttons:
            rb.state(["!disabled"] if self.name else ["disabled"])

    def _tick(self):
        version, connected, lines = self.lcd_client.state.snapshot()
        if version != self._version:
            self._version = version
            self.view.show(connected, lines)
        self._show_state()
        self._after = self.after(POLL_MS, self._tick)

    def _destroyed(self, event):
        if event.widget is not self:
            return
        try:
            self.after_cancel(self._after)
        except tk.TclError:
            pass
        self.lcd_client.stop()
        self.poller.stop()
