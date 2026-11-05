"""The main window: the list of machines, the front panel of the selected
one (LCD, keyswitch, reset), the command line Start will run, the
emulator's output, and Start / Stop.

``start_machine()`` writes the launcher and runs it with the machine's own
folder as the working directory, its output going into ``last-run.log``.
``start_in_terminal()`` opens the same launcher in Terminal (macOS). Either
way the front panel follows the run through the machine's local LCD and QMP
ports.
"""

from __future__ import annotations

import signal
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

from . import command, model, paths
from .model import Machine, Library
from .paths import Settings
from .ui_dialogs import APP_TITLE, ask_name, confirm_delete, confirm_reset_nvram, open_folder
from .ui_machine import MachineEditor
from .ui_panel import FrontPanel

LOG_NAME = "last-run.log"
LOG_TAIL_BYTES = 64 * 1024

CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


def qemu_dir() -> str:
    """The folder this program is installed in, which holds the emulator."""
    return str(paths.install_dir())


class RunningMachine:
    def __init__(self, name: str, proc: subprocess.Popen, log_path: Path, argv: list, log_fh,
                 qmp_port: int | None = None, lcd_port: int | None = None):
        self.name = name
        self.proc = proc
        self.log_path = log_path
        self.argv = argv
        self.qmp_port = qmp_port
        self.lcd_port = lcd_port
        self.started = time.time()
        self.ended: float | None = None
        self._log_fh = log_fh
        self.exit_code: int | None = None

    @property
    def pid(self) -> int:
        return self.proc.pid

    def poll(self) -> int | None:
        rc = self.proc.poll()
        if rc is not None and self.exit_code is None:
            self.exit_code = rc
            self.ended = time.time()
            try:
                if self._log_fh:
                    self._log_fh.close()
            except OSError:
                pass
        return rc

    def stop(self) -> None:
        """SIGTERM: QEMU closes its disks cleanly; for AIX it is a power cut."""
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def kill(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def uptime(self) -> float:
        return (self.ended or time.time()) - self.started


TERMINAL_STATUS = "Started in Terminal"


def start_in_terminal(m: Machine, machine_dir: Path) -> tuple:
    """Run the launcher in a Terminal window (macOS); returns
    (launcher, QMP port, LCD port)."""
    machine_dir = Path(machine_dir)
    machine_dir.mkdir(parents=True, exist_ok=True)
    qmp, lcd = command.free_ports(m)
    launcher, _argv = command.write_launcher(m, qemu_dir(), str(machine_dir), qmp=qmp, lcd=lcd)
    subprocess.Popen(["open", "-a", "Terminal", str(launcher)], cwd=str(machine_dir))
    return launcher, qmp, lcd


def start_machine(m: Machine, machine_dir: Path) -> RunningMachine:
    machine_dir = Path(machine_dir)
    machine_dir.mkdir(parents=True, exist_ok=True)
    qmp, lcd = command.free_ports(m)
    launcher, argv = command.write_launcher(m, qemu_dir(), str(machine_dir), paths.HOST_PLATFORM,
                                            qmp=qmp, lcd=lcd)
    log_path = machine_dir / LOG_NAME
    log_path.write_text("# " + command.one_line(argv, str(machine_dir)) + "\n", encoding="utf-8")
    windows = paths.is_windows(paths.HOST_PLATFORM)
    log_fh = None if windows else open(log_path, "a", encoding="utf-8")
    try:
        if windows:
            proc = subprocess.Popen(["cmd.exe", "/c", str(launcher)], cwd=str(machine_dir),
                                    creationflags=CREATE_NEW_CONSOLE)
        else:
            proc = subprocess.Popen(argv, cwd=str(machine_dir), stdout=log_fh,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    except OSError:
        if log_fh:
            log_fh.close()
        raise
    return RunningMachine(m.name, proc, log_path, argv, log_fh, qmp, lcd)


def read_log_tail(path: Path, limit: int = LOG_TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", "replace")
    if size > limit:
        text = "…\n" + text.split("\n", 1)[-1]
    return text


class MainWindow(tk.Tk):
    def __init__(self, settings: Settings, settings_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.settings_path = settings_path or paths.settings_path()
        self.library = Library()
        self.library.ensure()
        self.running: dict = {}
        self.finished: dict = {}
        self.terminal_started: dict = {}
        self.ports: dict = {}           # name -> (QMP, LCD) of its last start
        self.title(APP_TITLE)
        self.geometry("1150x800")
        self.minsize(900, 620)
        self.notes_name: str | None = None
        self._log_shown: tuple | None = None
        self._build()
        self.refresh_list(select=settings.last_machine)
        self.after(1000, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._quit)
        # Tk's own handler exits from inside the signal and can abort.
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)):
            if sig is not None:
                signal.signal(sig, self._on_signal)

    # ---------------- layout
    def _build(self):
        menubar = tk.Menu(self)
        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="New machine…", command=self.new_machine)
        filem.add_command(label="Open the Machines folder", command=self.open_machines_folder)
        filem.add_separator()
        filem.add_command(label="Quit", command=self._quit)
        menubar.add_cascade(label="File", menu=filem)
        self.config(menu=menubar)

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        self.tree = ttk.Treeview(left, columns=("machine", "state"), show="headings",
                                 selectmode="browse", height=18)
        self.tree.heading("machine", text="Machine")
        self.tree.heading("state", text="")
        self.tree.column("machine", width=220, anchor="w")
        self.tree.column("state", width=80, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())
        self.tree.bind("<Double-1>", lambda _e: self.edit_machine())

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        spec = [("New machine…", self.new_machine, False),
                ("Duplicate", self.duplicate_machine, True),
                ("Edit…", self.edit_machine, True),
                ("Delete", self.delete_machine, True),
                ("Open machine folder", self.open_machine_folder, True),
                ("Reset NVRAM", self.reset_nvram, True)]
        self.machine_buttons: list = []
        for i, (label, cmd, needs_machine) in enumerate(spec):
            button = ttk.Button(btns, text=label, command=cmd)
            button.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            if needs_machine:
                self.machine_buttons.append(button)
        row = (len(spec) + 1) // 2
        self.start_button = ttk.Button(btns, text="Start", command=self.start_selected)
        self.start_button.grid(row=row, column=0, sticky="ew", padx=2, pady=(8, 2))
        self.stop_button = ttk.Button(btns, text="Stop", command=self.stop_selected)
        self.stop_button.grid(row=row, column=1, sticky="ew", padx=2, pady=(8, 2))
        self.terminal_button = ttk.Button(btns, text="Start in Terminal",
                                          command=self.start_selected_in_terminal)
        if paths.HOST_PLATFORM == "darwin":
            self.terminal_button.grid(row=row + 1, column=0, columnspan=2, sticky="ew",
                                      padx=2, pady=2)
        self.machine_buttons += [self.start_button, self.terminal_button]
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        right = ttk.Frame(pane)
        pane.add(right, weight=3)
        self.panel = FrontPanel(right, self._key_turned)
        self.panel.pack(fill="x", pady=(0, 6))
        self.run_status = ttk.Label(right, text="", justify="left")
        self.run_status.pack(anchor="w", pady=(0, 6))

        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Command line constructed:").pack(side="left")
        self.copy_button = ttk.Button(head, text="Copy command", command=self.copy_command)
        self.copy_button.pack(side="right")
        self.machine_buttons.append(self.copy_button)
        self.command_line = tk.Text(right, height=11, wrap="none", font=self._mono(11))
        self.command_line.pack(fill="both", expand=True)
        self.command_line.config(state="disabled")

        ttk.Label(right, text="Output (last-run.log)").pack(anchor="w", pady=(8, 0))
        self.log_text = tk.Text(right, height=6, wrap="char", font=self._mono(10))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        ttk.Label(right, text="Notes").pack(anchor="w", pady=(8, 0))
        self.notes = tk.Text(right, height=3, wrap="word")
        self.notes.pack(fill="x")
        self.notes.bind("<FocusOut>", lambda _e: self.save_notes())

    @staticmethod
    def _mono(size: int):
        return ("Menlo", size) if paths.HOST_PLATFORM == "darwin" else ("Consolas", size - 1)

    @staticmethod
    def _set_text(widget: tk.Text, text: str, end: bool = False):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.config(state="disabled")
        if end:
            widget.see("end")

    def _show_notes(self, name: str | None, text: str):
        self.notes_name = name
        self.notes.config(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        if name is None:
            self.notes.config(state="disabled")

    def save_notes(self):
        name = getattr(self, "notes_name", None)
        if not name or not self.library.has_record(name):
            return
        text = self.notes.get("1.0", "end").rstrip("\n")
        try:
            m = self.library.load(name)
        except (OSError, ValueError, KeyError, TypeError):
            return
        if m.notes == text:
            return
        m.notes = text
        try:
            self.library.save(m)
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"The notes for “{name}” could not be saved.\n\n{e}")

    # ---------------- list / selection
    def refresh_list(self, select: str | None = None):
        current = select or self.selected_name()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for m in self.library.load_all():
            state = "running" if m.name in self.running else ""
            self.tree.insert("", "end", iid=m.name, values=(m.name, state))
        names = self.library.names()
        if current in names:
            self.tree.selection_set(current)
            self.tree.see(current)
        elif names:
            self.tree.selection_set(names[0])
        self.on_select()

    def selected_name(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def selected_machine(self) -> Machine | None:
        name = self.selected_name()
        if not name:
            return None
        try:
            return self.library.load(name)
        except (OSError, ValueError, KeyError, TypeError) as e:
            messagebox.showerror(APP_TITLE, f"“{name}” could not be read.\n\n{e}")
            return None

    def on_select(self):
        self.save_notes()
        name = self.selected_name()
        if name:
            self.settings.last_machine = name
            self._save_settings()
        self.refresh_details()

    def command_argv(self, m: Machine) -> list:
        return command.build_argv(m, qemu_dir(), str(self.library.folder(m.name)))

    def machine_ports(self, m: Machine) -> tuple:
        """Where this machine's QMP and LCD listen: the ports of its last
        start, else its own (a run from an earlier session uses those)."""
        return self.ports.get(m.name) or (command.qmp_port(m), command.lcd_port(m))

    def refresh_details(self):
        m = self.selected_machine()
        for button in self.machine_buttons:
            button.state(["disabled"] if m is None else ["!disabled"])
        if not m:
            self.panel.set_machine(None)
            self._set_text(self.command_line, "")
            self._set_text(self.log_text, "")
            self._show_notes(None, "")
            self.run_status.config(text="")
            self.stop_button.state(["disabled"])
            return
        folder = self.library.folder(m.name)
        qmp, lcd = self.machine_ports(m)
        try:
            argv = command.build_argv(m, qemu_dir(), str(folder), qmp=qmp, lcd=lcd)
            text = command.render_launcher(argv, paths.HOST_PLATFORM, command.needs_sudo(m),
                                           m.name)
        except Exception as e:      # never let one bad record blank the window
            text = f"({e})"
        self._set_text(self.command_line, text)
        self.panel.set_machine(m.name, m.keyswitch, qmp, lcd)
        self._show_notes(m.name, m.notes)
        self._refresh_run_status(m)
        self._refresh_log(m.name, force=True)

    def _refresh_log(self, name: str, force: bool = False):
        path = self.library.folder(name) / LOG_NAME
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = (name, mtime)
        if not force and key == self._log_shown:
            return
        self._log_shown = key
        self._set_text(self.log_text, read_log_tail(path), end=True)

    def _serial_line(self, m: Machine) -> str:
        parts = []
        for i, sp in enumerate(m.serial):
            if sp.mode == "telnet":
                parts.append(f"port {'AB'[i]}: telnet 127.0.0.1 {sp.port}")
        fwd = [r for r in m.network.hostfwd if not r.empty] if m.network.mode == "user" else []
        for r in fwd:
            parts.append(f"{r.proto} 127.0.0.1:{r.host_port} -> guest {r.guest_port}")
        return "   ·   ".join(parts)

    def _refresh_run_status(self, m: Machine):
        name = m.name
        r = self.running.get(name) or self.finished.get(name)
        running = bool(r and r.exit_code is None)
        self.stop_button.state(["!disabled"] if running else ["disabled"])
        if not r:
            if name in self.terminal_started:
                when = time.strftime("%H:%M", time.localtime(self.terminal_started[name]))
                head = f"{TERMINAL_STATUS} ({when})"
            else:
                head = "Not started from here"
        elif r.exit_code is None:
            head = f"Started from here {self._duration(r.uptime())} ago"
        elif r.exit_code == 0:
            head = f"Stopped after {self._duration(r.uptime())}"
        else:
            head = f"Stopped after {self._duration(r.uptime())} — code {r.exit_code}"
        line = self._serial_line(m)
        self.run_status.config(text=head + (f"\n{line}" if line else ""))

    @staticmethod
    def _duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s} second{'' if s == 1 else 's'}"
        if s < 3600:
            return f"{s // 60} minute{'' if s // 60 == 1 else 's'}"
        return f"{s // 3600} hour{'' if s // 3600 == 1 else 's'} {(s % 3600) // 60} min"

    # ---------------- actions
    def _key_turned(self, position: str):
        """The front key stays where it was turned: the record follows it."""
        m = self.selected_machine()
        if not m or m.keyswitch == position:
            return
        m.keyswitch = position
        try:
            self.library.save(m)
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"“{m.name}” could not be saved.\n\n{e}")
            return
        self._write_launcher(m)
        self.refresh_details()

    def new_machine(self):
        m = model.new_machine("")
        MachineEditor(self, m, self.library, qemu_dir(), self._on_editor_save, is_new=True)

    def duplicate_machine(self):
        name = self.selected_name()
        if not name:
            return
        new_name = ask_name(self, "Duplicate", "Name:", f"{name} copy", self.library.names())
        if not new_name:
            return
        try:
            m = self.library.duplicate(name, new_name)
        except (OSError, ValueError) as e:
            messagebox.showerror("Duplicate", str(e))
            return
        self._write_launcher(m)
        self.refresh_list(select=new_name)

    def delete_machine(self):
        name = self.selected_name()
        if not name:
            return
        if name in self.running and self.running[name].poll() is None:
            messagebox.showwarning("Delete", f"“{name}” is running.")
            return
        will_go, will_stay = self.library.delete_preview(name)
        if not confirm_delete(self, name, will_go, will_stay, self.library.folder(name)):
            return
        self.library.delete(name)
        self.finished.pop(name, None)
        self.terminal_started.pop(name, None)
        self.ports.pop(name, None)
        self.refresh_list()

    def edit_machine(self):
        m = self.selected_machine()
        if m:
            MachineEditor(self, m, self.library, qemu_dir(), self._on_editor_save)

    def _on_editor_save(self, m: Machine, old_name: str):
        if old_name != m.name and (old_name in self.running or self.panel.running()):
            raise ValueError("A machine cannot be renamed while it is running.")
        self.library.save(m, old_name=old_name)
        if old_name != m.name:
            self.finished.pop(old_name, None)
            self.ports.pop(old_name, None)
        self._write_launcher(m)
        self.refresh_list(select=m.name)

    def _write_launcher(self, m: Machine):
        qmp, lcd = self.machine_ports(m)
        try:
            command.write_launcher(m, qemu_dir(), str(self.library.folder(m.name)), qmp=qmp, lcd=lcd)
        except OSError as e:
            messagebox.showerror(APP_TITLE, f"The start-up file could not be written.\n\n{e}")

    def copy_command(self):
        m = self.selected_machine()
        if not m:
            return
        qmp, lcd = self.machine_ports(m)
        argv = command.build_argv(m, qemu_dir(), str(self.library.folder(m.name)), qmp=qmp, lcd=lcd)
        self.clipboard_clear()
        self.clipboard_append(command.one_line(argv, str(self.library.folder(m.name)),
                                               sudo=command.needs_sudo(m)))
        self.run_status.config(text="Command copied")

    def reset_nvram(self):
        m = self.selected_machine()
        if not m:
            return
        if (m.name in self.running and self.running[m.name].poll() is None) or self.panel.running():
            messagebox.showwarning("Reset NVRAM", f"“{m.name}” is running.")
            return
        if self.library.own_nvram(m) is None:
            messagebox.showinfo("Reset NVRAM",
                                f"“{m.name}” keeps its NVRAM in {m.nvram or 'no file'}, "
                                "which this program does not change.", parent=self)
            return
        if not confirm_reset_nvram(self, m.name):
            return
        try:
            self.library.reset_nvram(m)
        except OSError as e:
            messagebox.showerror("Reset NVRAM", f"“{m.name}” could not be reset.\n\n{e}")
            return
        self.refresh_details()

    def open_machine_folder(self):
        name = self.selected_name()
        if name:
            open_folder(self.library.folder(name))

    def open_machines_folder(self):
        self.library.ensure()
        open_folder(self.library.root)

    def _ready_to_start(self, m: Machine) -> bool:
        r = self.running.get(m.name)
        if (r and r.poll() is None) or self.panel.running():
            messagebox.showwarning("Start", f"“{m.name}” is already running.")
            return False
        folder = self.library.folder(m.name)
        errors, _warnings = model.validate(m, qemu_dir(), machine_dir=str(folder))
        errors += model.start_blockers(m, qemu_dir())
        if errors:
            messagebox.showerror("Start", "\n".join(f"• {e}" for e in errors))
            return False
        nv = model.nvram_path(m, folder)
        if nv and not nv.exists():
            try:
                model.write_blank_nvram(nv)
            except OSError as e:
                messagebox.showerror("Start", f"The NVRAM file could not be made.\n\n{e}")
                return False
        return True

    def start_selected(self) -> RunningMachine | None:
        """Run the emulator from here, its output in the window. A vmnet or
        low-port machine needs sudo, so it goes to Terminal."""
        m = self.selected_machine()
        if not m or not self._ready_to_start(m):
            return None
        if command.needs_sudo(m):
            if paths.HOST_PLATFORM != "darwin":
                messagebox.showerror("Start", "This network setting only works on a Mac.")
                return None
            self._start_terminal(m)
            return None
        try:
            r = start_machine(m, self.library.folder(m.name))
        except OSError as e:
            messagebox.showerror("Start", f"It could not be started.\n\n{e}")
            return None
        self.running[m.name] = r
        self.ports[m.name] = (r.qmp_port, r.lcd_port)
        self.finished.pop(m.name, None)
        self.terminal_started.pop(m.name, None)
        self.refresh_list(select=m.name)
        return r

    def start_selected_in_terminal(self):
        m = self.selected_machine()
        if m and self._ready_to_start(m):
            self._start_terminal(m)

    def _start_terminal(self, m: Machine):
        try:
            _launcher, qmp, lcd = start_in_terminal(m, self.library.folder(m.name))
        except OSError as e:
            messagebox.showerror("Start", f"A Terminal window could not be opened.\n\n{e}")
            return
        self.terminal_started[m.name] = time.time()
        self.ports[m.name] = (qmp, lcd)
        self.finished.pop(m.name, None)
        self.refresh_list(select=m.name)

    def stop_selected(self):
        name = self.selected_name()
        r = self.running.get(name) if name else None
        if not r or r.poll() is not None:
            return
        if not messagebox.askyesno("Stop", f"Stop “{name}” now? It is like pulling the power "
                                           "cord: shut AIX down first (shutdown -F) if you can.",
                                   icon="warning", default="no", parent=self):
            return
        r.stop()
        self.after(5000, lambda: r.kill())

    def _poll(self):
        changed = False
        for name, r in list(self.running.items()):
            if r.poll() is not None:
                self.finished[name] = r
                del self.running[name]
                changed = True
        if changed:
            self.refresh_list()
        else:
            sel = self.selected_machine() if self.selected_name() else None
            if sel:
                self._refresh_run_status(sel)
                self._refresh_log(sel.name)
        self.after(1000, self._poll)

    # ---------------- housekeeping
    def _save_settings(self):
        try:
            self.settings.save(self.settings_path)
        except OSError:
            pass

    def _quit(self):
        self.save_notes()
        self._save_settings()
        if self.running:
            names = ", ".join(self.running)
            if not messagebox.askyesno("Quit", f"Still running: {names} — stop them and quit?"):
                return
        for r in self.running.values():
            r.stop()
        self.destroy()

    def _on_signal(self, signum, frame):
        """Quit from the event loop; running machines are left running."""
        self.after(0, self._quit_on_signal)

    def _quit_on_signal(self):
        self.save_notes()
        self._save_settings()
        self.destroy()
