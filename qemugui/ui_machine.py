"""The machine editor: Machine, Display, Drives, Serial ports, Network,
Advanced.

* **No disk is ever filled in for you.** Drives start empty and stay empty
  until they are chosen; the ROM too.
* A file field is one control: type or paste a path into it, or
  double-click it to open the chooser.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from . import model, paths
from .model import Machine, ScsiDrive, Network, SerialPort
from .ui_dialogs import show_validation, CreateDiskDialog, ToolTip

KIND_LABELS = {"": "Empty", "disk": "Hard disk", "cdrom": "CD-ROM"}
KIND_BY_LABEL = {v: k for k, v in KIND_LABELS.items()}
IMAGE_TYPES = [("Disks and CDs", "*.img *.dsk *.qcow2 *.iso *.cdr *.toast *.raw"),
               ("Every file", "*")]
ROM_TYPES = [("ROM files", "*.rom *.ROM *.bin"), ("Every file", "*")]
NVRAM_TYPES = [("NVRAM files", "*.bin *.img"), ("Every file", "*")]

GREY = "gray"
EDITOR_WIDTH = 780

KEY_TIP = ("Where the front key is when the machine is switched on. Unlocked is the normal "
           "running position. Service, with a blank NVRAM, makes Open Firmware look for an "
           "AIX Install CD or a diagnostics floppy. The key can also be turned while the "
           "machine runs, in the main window.")
NVRAM_TIP = ("Open Firmware's settings (boot device, console, its environment). Kept in this "
             "file between runs; empty = blank at every start and nothing saved.")
PSU_TIP = "The 700 has a second power-supply bay. Firmware and AIX read how many are fitted."
SWAP_TIP = "Swap the Option and Command keys, for a PC keyboard."
SERIAL_TIP = ("A telnet server on this computer (127.0.0.1) for the port: connect with "
              "telnet 127.0.0.1 PORT. AIX's serial console is on port B; Open Firmware uses "
              "port A once its console is set to ttya.")


def browse_file(parent, var: tk.StringVar, filetypes, fallback=None) -> str:
    start = paths.browse_start_dir(var.get(), fallback)
    f = filedialog.askopenfilename(parent=parent, initialdir=str(start), filetypes=filetypes)
    if f:
        var.set(f)
        return f
    return ""


class FilePicker:
    def __init__(self, master, var: tk.StringVar, filetypes, width: int = 40, fallback=None,
                 on_pick=None):
        self.var = var
        self.filetypes = filetypes
        self.fallback = fallback
        self.on_pick = on_pick
        self.entry = ttk.Entry(master, textvariable=var, width=width)
        self.entry.bind("<Double-Button-1>", self._browse)
        var.trace_add("write", lambda *_a: self._refresh())
        self._refresh()

    def grid(self, **kw) -> "FilePicker":
        self.entry.grid(**kw)
        return self

    def _refresh(self) -> None:
        if self.var.get():
            self.entry.xview_moveto(1.0)

    def _browse(self, _e=None):
        fb = self.fallback() if callable(self.fallback) else self.fallback
        chosen = browse_file(self.entry.winfo_toplevel(), self.var, self.filetypes, fb)
        if chosen and self.on_pick:
            self.on_pick(chosen)
        return "break"


class DriveRow:
    """One SCSI ID on one bus."""

    def __init__(self, master, row: int, bus: str, sid: int, fallback=None):
        self.bus = bus
        self.sid = sid
        self.kind = tk.StringVar(value=KIND_LABELS[""])
        self.file = tk.StringVar()
        self.format = tk.StringVar(value="raw")
        alias = model.of_alias(bus, sid)
        ttk.Label(master, text=model.scsi_name(sid)).grid(row=row, column=0, sticky="w",
                                                         padx=(0, 4), pady=1)
        ttk.Label(master, text=alias, foreground=GREY, width=6).grid(row=row, column=1, sticky="w")
        cb = ttk.Combobox(master, textvariable=self.kind, values=list(KIND_LABELS.values()),
                          state="readonly", width=9)
        cb.grid(row=row, column=2, padx=2, pady=1)
        cb.bind("<<ComboboxSelected>>", self._kind_changed)
        self.picker = FilePicker(master, self.file, IMAGE_TYPES, width=40, fallback=fallback,
                                 on_pick=self._file_picked)
        self.picker.grid(row=row, column=3, sticky="ew", padx=2, pady=1)
        self.file.trace_add("write", lambda *_a: self._infer_kind())
        self.format_cb = ttk.Combobox(master, textvariable=self.format, values=model.FORMATS,
                                      state="readonly", width=6)
        self.format_cb.grid(row=row, column=4, padx=2)

    def _file_picked(self, path: str):
        self.format.set(model.detect_format(path))

    def _infer_kind(self):
        if KIND_BY_LABEL[self.kind.get()] or not self.file.get().strip():
            return
        ext = Path(self.file.get().strip()).suffix.lower()
        self.kind.set(KIND_LABELS["cdrom" if ext in model.CDROM_EXTS else "disk"])
        self._kind_changed()

    def _kind_changed(self, _e=None):
        k = KIND_BY_LABEL[self.kind.get()]
        if not k:
            self.file.set("")
            self.format.set("raw")
        if k == "cdrom":
            self.format.set("raw")
        self.format_cb.config(state="readonly" if k == "disk" else "disabled")

    def set(self, d: ScsiDrive | None):
        self.kind.set(KIND_LABELS[d.kind if d and d.kind in KIND_LABELS else ""])
        self.file.set(d.file if d else "")
        self.format.set((d.format if d else "raw") or "raw")
        self.format_cb.config(state="readonly" if d and d.kind == "disk" else "disabled")

    def get(self) -> ScsiDrive | None:
        self._infer_kind()
        k = KIND_BY_LABEL[self.kind.get()]
        if not k or not self.file.get().strip():
            return None
        return ScsiDrive(self.bus, self.sid, k, self.file.get().strip(),
                         (self.format.get() or "raw") if k == "disk" else "raw")


class SerialRow:
    def __init__(self, master, row: int, index: int):
        self.on = tk.BooleanVar(value=True)
        self.port = tk.StringVar()
        ttk.Label(master, text=model.SERIAL_NAMES[index] + ":").grid(
            row=row, column=0, sticky="w", pady=3)
        cb = ttk.Checkbutton(master, text="Telnet server on port", variable=self.on,
                             command=self._changed)
        cb.grid(row=row, column=1, sticky="w", padx=(8, 4))
        ToolTip(cb, SERIAL_TIP)
        self.entry = ttk.Entry(master, textvariable=self.port, width=8)
        self.entry.grid(row=row, column=2, sticky="w")

    def _changed(self):
        self.entry.config(state="normal" if self.on.get() else "disabled")

    def set(self, sp: SerialPort):
        self.on.set(sp.mode == "telnet")
        self.port.set(str(sp.port))
        self._changed()

    def get(self, default_port: int) -> SerialPort:
        try:
            port = int(self.port.get().strip())
        except ValueError:
            port = -1 if self.on.get() else default_port
        return SerialPort("telnet" if self.on.get() else "none", port)


class MachineEditor(tk.Toplevel):
    """One machine's settings. ``on_save(machine, old_name)`` runs after the
    checks; with ``is_new`` nothing exists on disk until Save."""

    def __init__(self, parent, machine: Machine, library: model.Library, qemu_dir: str, on_save,
                 is_new: bool = False):
        super().__init__(parent)
        self.machine = machine.copy()
        self.old_name = machine.name
        self.is_new = is_new
        self.library = library
        self.qemu_dir = qemu_dir
        self.on_save = on_save
        self.title("New machine" if is_new else f"{machine.name} — settings")
        self.resizable(True, True)
        self.transient(parent)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._build_machine()
        self._build_display()
        self._build_drives()
        self._build_serial()
        self._build_network()
        self._build_advanced()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        self.msg = ttk.Label(bar, text="", foreground=GREY, wraplength=EDITOR_WIDTH - 200)
        self.msg.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(bar, text="Save", command=self.save).pack(side="right", padx=2)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.load(self.machine)
        self._size_window()
        self._centre_on(parent)
        self.nb.select(0)
        self.name_entry.focus_set()

    def _size_window(self):
        self.update_idletasks()
        height = min(self.winfo_reqheight(), max(400, self.winfo_screenheight() - 160))
        self.geometry(f"{max(EDITOR_WIDTH, self.winfo_reqwidth())}x{height}")
        self.minsize(640, 400)

    def _centre_on(self, parent):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            w, h = EDITOR_WIDTH, self.winfo_reqheight()
        try:
            top = parent.winfo_toplevel()
            top.update_idletasks()
            x = top.winfo_rootx() + (top.winfo_width() - w) // 2
            y = top.winfo_rooty() + (top.winfo_height() - h) // 2
        except tk.TclError:
            return
        x = max(0, min(x, self.winfo_screenwidth() - w))
        y = max(0, min(y, self.winfo_screenheight() - h - 40))
        self.geometry(f"{w}x{h}+{x}+{y}")

    def machine_folder(self) -> Path:
        name = self.name_var.get().strip() or self.old_name
        return self.library.folder(name) if name else self.library.root

    def _tab(self, title: str) -> ttk.Frame:
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text=title)
        return f

    @staticmethod
    def _heading(f, row: int, text: str, pady=(0, 4)):
        label = ttk.Label(f, text=text, font=("", 0, "bold"))
        label.grid(row=row, column=0, columnspan=5, sticky="w", pady=pady)
        return label

    # ---------------- tabs
    def _build_machine(self):
        f = self._tab("Machine")
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Name:").grid(row=r, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(f, textvariable=self.name_var, width=40)
        self.name_entry.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="Machine:").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Label(f, text=f"{model.MACHINE_LABEL} (200 MHz PowerPC 604e, 1 MB L2)").grid(
            row=r, column=1, columnspan=2, sticky="w", pady=4)
        r += 1
        ttk.Label(f, text="ROM:").grid(row=r, column=0, sticky="w", pady=4)
        self.rom_var = tk.StringVar()
        FilePicker(f, self.rom_var, ROM_TYPES, width=40,
                   fallback=lambda: self.qemu_dir).grid(row=r, column=1, columnspan=2,
                                                        sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="The Network Server ROM (4 MB): 1.1.22 for AIX, 2.0 for Mac OS. "
                          "Double-click to choose it.", foreground=GREY).grid(row=r, column=1, columnspan=2,
                                                                 sticky="w")
        r += 1
        ttk.Label(f, text="Memory (MB):").grid(row=r, column=0, sticky="w", pady=4)
        self.ram_var = tk.StringVar()
        ttk.Combobox(f, textvariable=self.ram_var, width=10,
                     values=[str(x) for x in model.RAM_CHOICES]).grid(row=r, column=1,
                                                                    sticky="w", pady=4)
        r += 1
        ttk.Label(f, text=f"Up to {model.RAM_MAX} MB, in pairs of DIMMs", foreground=GREY).grid(
            row=r, column=1, columnspan=2, sticky="w")
        r += 1
        kl = ttk.Label(f, text="Key at power-on:")
        kl.grid(row=r, column=0, sticky="w", pady=4)
        ToolTip(kl, KEY_TIP)
        self.key_var = tk.StringVar()
        kcb = ttk.Combobox(f, textvariable=self.key_var, state="readonly", width=12,
                           values=[model.KEYSWITCH_LABELS[k] for k in model.KEYSWITCH])
        kcb.grid(row=r, column=1, sticky="w", pady=4)
        ToolTip(kcb, KEY_TIP)
        r += 1
        pl = ttk.Label(f, text="Power supplies:")
        pl.grid(row=r, column=0, sticky="w", pady=4)
        ToolTip(pl, PSU_TIP)
        self.psu_var = tk.StringVar()
        ttk.Combobox(f, textvariable=self.psu_var, state="readonly", width=4,
                     values=[str(x) for x in model.POWER_SUPPLIES]).grid(row=r, column=1,
                                                                       sticky="w", pady=4)
        r += 1
        nl = ttk.Label(f, text="NVRAM file:")
        nl.grid(row=r, column=0, sticky="w", pady=4)
        ToolTip(nl, NVRAM_TIP)
        self.nvram_var = tk.StringVar()
        FilePicker(f, self.nvram_var, NVRAM_TYPES, width=30,
                   fallback=self.machine_folder).grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="New blank NVRAM…", command=self._new_nvram).grid(
            row=r, column=2, sticky="w", padx=(6, 0))
        r += 1
        ttk.Label(f, text="Kept in the machine folder. Blank: Open Firmware's console is the "
                          "screen and the ADB keyboard.", foreground=GREY,
                  wraplength=EDITOR_WIDTH - 200, justify="left").grid(
            row=r, column=1, columnspan=2, sticky="w")

    def _new_nvram(self):
        if not self.name_var.get().strip():
            messagebox.showinfo("New blank NVRAM", "Give this machine a name first.", parent=self)
            return
        target = self.machine_folder() / model.NVRAM_NAME
        try:
            if target.exists():
                if not messagebox.askyesno(
                        "New blank NVRAM",
                        f"{target} is already there. Clear it, so Open Firmware forgets its "
                        "settings?", icon="warning", default="no", parent=self):
                    return
                target.write_bytes(b"\0" * model.NVRAM_SIZE)
            else:
                model.write_blank_nvram(target)
        except OSError as e:
            messagebox.showerror("New blank NVRAM", str(e), parent=self)
            return
        self.nvram_var.set(model.NVRAM_NAME)
        self.msg.config(text=f"Blank NVRAM: {target}")

    def _build_display(self):
        f = self._tab("Display")
        f.columnconfigure(1, weight=1)
        self._heading(f, 0, "Screen")
        ttk.Label(f, text="Video card:").grid(row=1, column=0, sticky="w", pady=4)
        self.video_var = tk.StringVar()
        vcb = ttk.Combobox(f, textvariable=self.video_var, state="readonly", width=30,
                           values=[model.VIDEO_LABELS[k] for k in model.VIDEO_CARDS])
        vcb.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)
        vcb.bind("<<ComboboxSelected>>", self._video_changed)
        ttk.Label(f, text="Card ROM:").grid(row=5, column=0, sticky="w", pady=4)
        self.video_rom_var = tk.StringVar()
        self.video_rom_picker = FilePicker(f, self.video_rom_var, ROM_TYPES, width=40,
                                           fallback=lambda: self.qemu_dir)
        self.video_rom_picker.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)
        self.video_note = ttk.Label(f, foreground=GREY, wraplength=EDITOR_WIDTH - 60,
                                    justify="left")
        self.video_note.grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Display:").grid(row=2, column=0, sticky="w", pady=(10, 4))
        self.display_var = tk.StringVar()
        dcb = ttk.Combobox(f, textvariable=self.display_var,
                           values=list(model.displays_for_host()), state="readonly", width=10)
        dcb.grid(row=2, column=1, sticky="w", pady=(10, 4))
        dcb.bind("<<ComboboxSelected>>", self._display_changed)
        ttk.Label(f, text="none: no window; the front panel and the serial ports still work",
                  foreground=GREY).grid(row=3, column=1, sticky="w")
        self.swap_var = tk.BooleanVar(value=False)
        self.swap_cb = ttk.Checkbutton(f, text="Swap Option and Command (cocoa)",
                                       variable=self.swap_var)
        self.swap_cb.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ToolTip(self.swap_cb, SWAP_TIP)

    VIDEO_NOTES = {
        "54m30": "The built-in Cirrus 54M30 (1 MB, up to 1024 x 768 at 256 colours). Its mode "
                 "is set by Open Firmware and AIX (CDE at 1024 x 768 after chdev -l gda0).",
        "rage128pro": "A Rage 128 Pro in PCI slot D, in place of the built-in video. Needs the "
                      "card's Mac ROM (for example ati_rage128pro_136_agp.rom) and the "
                      "Network Server ROM 2.0. Double-click to choose the card ROM.",
    }

    def _video_changed(self, _e=None):
        v = model.video_by_label(self.video_var.get())
        self.video_note.config(text=self.VIDEO_NOTES[v])
        self.video_rom_picker.entry.config(state="normal" if v == "rage128pro" else "disabled")

    def _display_changed(self, _e=None):
        self.swap_cb.config(state="normal" if self.display_var.get() == "cocoa" else "disabled")

    def _build_drives(self):
        f = self._tab("Drives")
        f.columnconfigure(0, weight=1)
        self.drive_rows: list = []
        r = 0
        for b in model.SCSI_BUSES:
            self._heading(f, r, b.label, pady=(8 if r else 0, 2))
            r += 1
            ttk.Label(f, text=b.note, foreground=GREY).grid(row=r, column=0, sticky="w", padx=4)
            r += 1
            table = ttk.Frame(f)
            table.grid(row=r, column=0, sticky="ew")
            table.columnconfigure(3, weight=1)
            for sid in model.SCSI_IDS:
                self.drive_rows.append(DriveRow(table, sid, b.bus, sid,
                                                fallback=self.machine_folder))
            ttk.Label(table, text=model.scsi_name(model.SCSI_SELF_ID)).grid(
                row=len(model.SCSI_IDS), column=0, sticky="w", pady=1)
            ttk.Label(table, text=model.SCSI_SELF_LABEL, foreground=GREY).grid(
                row=len(model.SCSI_IDS), column=2, columnspan=3, sticky="w", padx=2, pady=1)
            r += 1
        ttk.Label(f, text="CDs are read-only. AIX installs from the Install CD with the key at "
                          "Service and a blank NVRAM (CD on internal bus 0, ID 0; the disk "
                          "on ID 2). Mac OS starts from a Mac OS CD on internal bus 0 or 1.", foreground=GREY, wraplength=EDITOR_WIDTH - 60,
                  justify="left").grid(row=r, column=0, sticky="w", padx=4, pady=(8, 0))
        r += 1
        bar = ttk.Frame(f)
        bar.grid(row=r, column=0, sticky="ew", pady=(8, 2))
        have_img = paths.qemu_img_binary().is_file()
        ttk.Button(bar, text="Create new disk image…", command=self._create_disk,
                   state=("normal" if have_img else "disabled")).pack(side="left", padx=4)
        if not have_img:
            ttk.Label(bar, text=f"{paths.qemu_img_name()} not found",
                      foreground=GREY).pack(side="left", padx=6)

    def _row(self, bus: str, sid: int) -> DriveRow:
        return next(r for r in self.drive_rows if r.bus == bus and r.sid == sid)

    def _build_serial(self):
        f = self._tab("Serial ports")
        self._heading(f, 0, "Serial ports")
        self.serial_rows = [SerialRow(f, 1 + i, i) for i in range(2)]
        ttk.Label(f, text="Connect with: telnet 127.0.0.1 PORT. AIX on a serial console "
                          "talks on port B; Open Firmware on port A after "
                          "setenv output-device ttya / setenv input-device ttya.",
                  foreground=GREY, wraplength=EDITOR_WIDTH - 60, justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _build_network(self):
        f = self._tab("Network")
        self._heading(f, 0, "Ethernet (built-in MACE)")
        ttk.Label(f, text="Connection:").grid(row=1, column=0, sticky="w")
        self.net_mode = tk.StringVar(value=model.network_mode_label("user"))
        self.net_mode_cb = ttk.Combobox(f, textvariable=self.net_mode, state="readonly", width=18,
                                        values=model.network_labels_for_host(paths.HOST_PLATFORM))
        self.net_mode_cb.grid(row=1, column=1, sticky="w")
        self.net_mode_cb.bind("<<ComboboxSelected>>", self._net_mode_changed)
        if paths.HOST_PLATFORM == "darwin":
            ttk.Label(f, text="vmnet needs the administrator password",
                      foreground=GREY).grid(row=1, column=2, sticky="w", padx=6)
        self.ifname_label = ttk.Label(f, text=model.ifname_label(paths.HOST_PLATFORM))
        self.ifname_label.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ifname_var = tk.StringVar()
        self.ifname_entry = ttk.Entry(f, textvariable=self.ifname_var, width=28)
        self.ifname_entry.grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Ethernet address:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.mac_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.mac_var, width=22).grid(row=3, column=1, sticky="w",
                                                              pady=(6, 0))
        ff = ttk.Frame(f)
        ff.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(ff, text="Port forwarding", font=("", 0, "bold")).grid(
            row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(ff, text="Slirp only, from 127.0.0.1 on this computer. 2323 -> 23 is telnet "
                           "into AIX. Host ports below 1024 need the administrator password.",
                  foreground=GREY, wraplength=EDITOR_WIDTH - 60, justify="left").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(0, 4))
        for c, t in enumerate(("Protocol", "Host port", "Guest port")):
            ttk.Label(ff, text=t, foreground=GREY).grid(row=2, column=c, sticky="w", padx=(0, 6))
        self.fwd_rows = []
        for i in range(model.HOSTFWD_ROWS):
            proto = tk.StringVar(value="tcp")
            hp, gp = tk.StringVar(), tk.StringVar()
            w = [ttk.Combobox(ff, textvariable=proto, state="readonly", width=5,
                              values=list(model.HOSTFWD_PROTOS)),
                 ttk.Entry(ff, textvariable=hp, width=8),
                 ttk.Entry(ff, textvariable=gp, width=8)]
            for c, x in enumerate(w):
                x.grid(row=3 + i, column=c, sticky="w", padx=(0, 6), pady=1)
            self.fwd_rows.append((proto, hp, gp, w))

    def _net_mode_changed(self, _e=None):
        mode = model.network_mode_by_label(self.net_mode.get())
        if mode in model.NETWORK_MODES_WITH_IFNAME:
            self.ifname_entry.config(state="normal")
            if not self.ifname_var.get():
                self.ifname_var.set(model.default_ifname(mode, paths.HOST_PLATFORM))
        else:
            self.ifname_entry.config(state="disabled")
        for *_v, widgets in self.fwd_rows:
            for i, w in enumerate(widgets):
                w.config(state=("readonly" if i == 0 else "normal") if mode == "user"
                         else "disabled")

    def _build_advanced(self):
        f = self._tab("Advanced")
        f.columnconfigure(0, weight=1)
        self._heading(f, 0, "Additional command line arguments")
        self.extra_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.extra_var, width=70).grid(row=1, column=0, sticky="ew")
        ttk.Label(f, text="Added at the end, e.g. -monitor stdio",
                  foreground=GREY).grid(row=2, column=0, sticky="w", pady=(4, 0))

    # ---------------- load / collect
    def load(self, m: Machine):
        self.name_var.set(m.name)
        self.rom_var.set(m.rom)
        self.ram_var.set(str(m.ram_mb))
        self.key_var.set(model.KEYSWITCH_LABELS.get(m.keyswitch,
                                                    model.KEYSWITCH_LABELS[model.KEYSWITCH_DEFAULT]))
        self.psu_var.set(str(m.power_supplies))
        self.nvram_var.set(m.nvram)
        self.display_var.set(m.display)
        self.swap_var.set(m.swap_opt_cmd)
        self._display_changed()
        self.video_var.set(model.VIDEO_LABELS.get(m.video, model.VIDEO_LABELS[model.VIDEO_DEFAULT]))
        self.video_rom_var.set(m.video_rom)
        self._video_changed()
        for row in self.drive_rows:
            row.set(m.scsi_at(row.bus, row.sid))
        for i, row in enumerate(self.serial_rows):
            row.set(m.serial[i] if i < len(m.serial) else model.default_serials()[i])
        self.net_mode_cb.config(
            values=model.network_labels_for_host(paths.HOST_PLATFORM, m.network.mode))
        self.net_mode.set(model.network_mode_label(m.network.mode))
        self.mac_var.set(m.network.mac)
        self.ifname_var.set(m.network.ifname)
        for i, (proto, hp, gp, _w) in enumerate(self.fwd_rows):
            r = m.network.hostfwd[i] if i < len(m.network.hostfwd) else model.HostFwd()
            proto.set(r.proto if r.proto in model.HOSTFWD_PROTOS else "tcp")
            hp.set(r.host_port)
            gp.set(r.guest_port)
        self._net_mode_changed()
        self.extra_var.set(m.extra_args)

    def collect(self) -> Machine:
        m = self.machine.copy()
        m.name = self.name_var.get().strip()
        try:
            m.ram_mb = int(self.ram_var.get().strip())
        except ValueError:
            m.ram_mb = -1
        m.rom = self.rom_var.get().strip()
        m.keyswitch = model.keyswitch_by_label(self.key_var.get())
        try:
            m.power_supplies = int(self.psu_var.get())
        except ValueError:
            m.power_supplies = 1
        m.nvram = self.nvram_var.get().strip()
        m.display = self.display_var.get()
        m.swap_opt_cmd = self.swap_var.get()
        m.video = model.video_by_label(self.video_var.get())
        m.video_rom = self.video_rom_var.get().strip()
        m.scsi = [d for d in (row.get() for row in self.drive_rows) if d]
        m.serial = [row.get(model.SERIAL_DEFAULT_PORTS[i]) for i, row in enumerate(self.serial_rows)]
        mode = model.network_mode_by_label(self.net_mode.get())
        ifname = self.ifname_var.get().strip() if mode in model.NETWORK_MODES_WITH_IFNAME else ""
        fwd = [model.HostFwd(p.get(), h.get().strip(), g.get().strip())
               for p, h, g, _w in self.fwd_rows]
        m.network = Network(mode, self.mac_var.get().strip(), ifname,
                            [r for r in fwd if not r.empty])
        m.extra_args = self.extra_var.get().strip()
        return m

    # ---------------- actions
    def _create_disk(self):
        if not self.name_var.get().strip():
            messagebox.showinfo("New hard disk", "Give this machine a name first.", parent=self)
            return
        dlg = CreateDiskDialog(self, self.collect(), self.machine_folder())
        if not dlg.result:
            return
        path, fmt, place = dlg.result
        if place is not None:
            bus, sid = place
            self._row(bus, sid).set(ScsiDrive(bus, sid, "disk", path, fmt))
        self.msg.config(text=f"Made {Path(path).name}")

    def save(self):
        m = self.collect()
        errors, warnings = model.validate(m, self.qemu_dir, machine_dir=str(self.machine_folder()))
        if m.name != self.old_name and self.library.has_record(m.name):
            errors.append(f"You already have a machine called “{m.name}”.")
        self.msg.config(text="  ".join(errors + warnings)[:300])
        if not show_validation(self, errors, warnings):
            return
        try:
            self.on_save(m, self.old_name)
        except (OSError, ValueError) as e:
            messagebox.showerror("Save", str(e), parent=self)
            return
        self.destroy()
