"""The small windows: the name for a duplicate, the delete and NVRAM reset
confirmations, create a disk, and the hover tips.
"""

from __future__ import annotations

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

from . import model, paths

APP_TITLE = "Qemu-system-ppc ANS GUI"

# (label, qemu-img size). The Network Server shipped with 2 GB and 4 GB drives.
DISK_SIZES = (("1 GB", "1G"), ("2 GB", "2G"), ("4 GB", "4G"), ("9 GB", "9G"))
DEFAULT_DISK_SIZE = "2 GB"

class ToolTip:
    """A short explanation that appears while the pointer rests on a widget."""

    DELAY_MS = 500

    def __init__(self, widget, text: str, wraplength: int = 360):
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self.tip: tk.Toplevel | None = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background="#ffffe0", foreground="black",
                 relief="solid", borderwidth=1, wraplength=self.wraplength,
                 padx=6, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


def show_validation(parent, errors: list[str], warnings: list[str]) -> bool:
    """Show what is wrong, or worth knowing. True if saving may go ahead."""
    if errors:
        text = "\n".join(f"• {e}" for e in errors)
        if warnings:
            text += "\n" + "\n".join(f"• {w}" for w in warnings)
        messagebox.showerror("Save", text, parent=parent)
        return False
    if warnings:
        text = "\n".join(f"• {w}" for w in warnings) + "\n\nSave anyway?"
        return messagebox.askyesno("Save", text, parent=parent)
    return True


def _bullets(files: list[str], limit: int = 20) -> str:
    shown = "\n".join(f"    {f}" for f in files[:limit])
    if len(files) > limit:
        shown += f"\n    … and {len(files) - limit} more"
    return shown


def confirm_delete(parent, name: str, will_go: list[str], will_stay: list[str],
                   folder: Path) -> bool:
    """Delete removes the record, the launcher, the log and the NVRAM. Disk
    images are never deleted; the window lists both sides before anything
    happens."""
    text = f"Remove “{name}”?"
    if will_go:
        text += "\n\nDeleted:\n" + _bullets(will_go)
    if will_stay:
        text += "\n\nKept:\n" + _bullets(will_stay)
    return messagebox.askyesno("Remove", text, icon="warning", parent=parent)


def confirm_reset_nvram(parent, name: str) -> bool:
    """A blank NVRAM is a machine never set up: Open Firmware forgets its
    boot device and console settings."""
    text = (f"Clear the NVRAM of “{name}”? Open Firmware forgets its settings (boot device, "
            "console) and comes up on the screen and the ADB keyboard.")
    return messagebox.askyesno("Reset NVRAM", text, icon="warning", default="no", parent=parent)


def ask_name(parent, title: str, prompt: str, initial: str, existing: list[str]) -> str | None:
    while True:
        name = simpledialog.askstring(title, prompt, initialvalue=initial, parent=parent)
        if name is None:
            return None
        name = name.strip()
        if not model.NAME_RE.match(name):
            messagebox.showerror(title, "That name will not work.", parent=parent)
            continue
        if name in existing:
            messagebox.showerror(title, f"You already have a machine called “{name}”.",
                                 parent=parent)
            continue
        return name


class CreateDiskDialog(simpledialog.Dialog):
    """Make a new, empty hard disk for this machine and offer it a bay.

    Never writes over an existing file: ``model.check_new_image_path`` is the
    guard, and it refuses rather than overwriting.
    """

    def __init__(self, parent, machine: model.Machine, machine_dir: Path):
        self.machine = machine
        self.machine_dir = Path(machine_dir)
        self.qemu_img = paths.qemu_img_binary()
        self.target: Path | None = None
        self.result = None  # (path, format, (bus, id) | None)
        super().__init__(parent, "New hard disk")

    def body(self, master):
        r = 0
        ttk.Label(master, text="Name:").grid(row=r, column=0, sticky="w", padx=4, pady=3)
        self.name_var = tk.StringVar(value="hard disk")
        ttk.Entry(master, textvariable=self.name_var, width=30).grid(
            row=r, column=1, sticky="ew", padx=4)
        r += 1
        ttk.Label(master, text="Size:").grid(row=r, column=0, sticky="w", padx=4, pady=3)
        self.size_var = tk.StringVar(value=DEFAULT_DISK_SIZE)
        ttk.Combobox(master, textvariable=self.size_var, values=[s[0] for s in DISK_SIZES],
                     state="readonly", width=10).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        ttk.Label(master, text="Format:").grid(row=r, column=0, sticky="w", padx=4, pady=3)
        self.fmt_var = tk.StringVar(value="raw")
        ttk.Combobox(master, textvariable=self.fmt_var, values=model.FORMATS, state="readonly",
                     width=10).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        ttk.Label(master, text="Put it in:").grid(row=r, column=0, sticky="w", padx=4, pady=3)
        self.choices: list = [("Nowhere", None)]
        for b in model.SCSI_BUSES:
            for sid in model.SCSI_IDS:
                if self.machine.scsi_at(b.bus, sid) is None:
                    alias = model.of_alias(b.bus, sid)
                    self.choices.append((f"{b.label.split(' (')[0]}, {model.scsi_name(sid)}"
                                         + (f" ({alias})" if alias else ""), (b.bus, sid)))
        first = next((label for label, place in self.choices if place), "Nowhere")
        self.place_var = tk.StringVar(value=first)
        ttk.Combobox(master, textvariable=self.place_var, values=[c[0] for c in self.choices],
                     state="readonly", width=34).grid(row=r, column=1, sticky="w", padx=4)
        r += 1
        if not self.qemu_img.is_file():
            ttk.Label(master, text=f"{self.qemu_img.name} is missing.", foreground="#a00").grid(
                row=r, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 2))
        return None

    def validate(self):
        target, why = model.check_new_image_path(self.machine_dir, self.name_var.get(),
                                                 self.fmt_var.get())
        if why:
            messagebox.showerror("New hard disk", why, parent=self)
            return False
        if not self.qemu_img.is_file():
            messagebox.showerror("New hard disk", f"{self.qemu_img.name} is missing.", parent=self)
            return False
        self.target = target
        return True

    def apply(self):
        fmt = self.fmt_var.get()
        size_arg = dict(DISK_SIZES).get(self.size_var.get(), "2G")
        self.machine_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.qemu_img), "create", "-f", fmt, str(self.target), size_arg]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            messagebox.showerror("New hard disk", f"The disk could not be made.\n\n{e}", parent=self)
            return
        if out.returncode != 0:
            messagebox.showerror("New hard disk",
                                 f"The disk could not be made.\n\n{out.stderr or out.stdout}",
                                 parent=self)
            return
        chosen = self.place_var.get()
        place = next((p for label, p in self.choices if label == chosen), None)
        self.result = (str(self.target), fmt, place)


def open_folder(path: Path) -> None:
    path = Path(path)
    try:
        if paths.HOST_PLATFORM == "darwin":
            subprocess.Popen(["open", str(path)])
        elif paths.is_windows():
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        messagebox.showerror(APP_TITLE, f"That folder could not be opened.\n\n{e}")
