"""Where the application thinks it is and what it refuses to do; and, when a
display is there, the windows themselves."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import qemu_gui                                    # noqa: E402
from qemugui import command, model, paths          # noqa: E402
from fake_lcd import FakeLcd                       # noqa: E402
from fake_qmp import FakeQMP                       # noqa: E402

APP = "Qemu-system-ppc ANS GUI"


class InstallDir(unittest.TestCase):
    def test_running_from_source(self):
        got = paths.resolve_install_dir(frozen=False, executable="/usr/bin/python3",
                                        source_root="/src/Qemu-GUI-ANS")
        self.assertEqual(got, Path("/src/Qemu-GUI-ANS"))

    def test_frozen_inside_a_macos_application_bundle(self):
        got = paths.resolve_install_dir(
            frozen=True, executable=f"/Applications/ans/{APP}.app/Contents/MacOS/{APP}",
            source_root="/nowhere")
        self.assertEqual(got, Path("/Applications/ans"))

    def test_frozen_windows_executable(self):
        got = paths.resolve_install_dir(frozen=True, executable=f"/c/qemu-ans/{APP}.exe",
                                        source_root="/nowhere")
        self.assertEqual(got, Path("/c/qemu-ans"))

    def test_source_root_points_at_the_entry_script(self):
        self.assertTrue((paths.SOURCE_ROOT / "qemu_gui.py").is_file())

    def test_everything_hangs_off_install_dir(self):
        with tempfile.TemporaryDirectory() as td:
            paths.use_install_dir(td)
            try:
                here = Path(td).resolve()
                self.assertEqual(paths.machines_dir(), here / "Machines")
                self.assertEqual(paths.qemu_binary("darwin"), here / "qemu-system-ppc")
                self.assertEqual(paths.qemu_binary("win32"), here / "qemu-system-ppc.exe")
                self.assertEqual(model.Library().root, here / "Machines")
            finally:
                paths.use_install_dir(None)


class FakeMainWindow:
    built = 0

    def __init__(self, *a, **k):
        FakeMainWindow.built += 1

    def mainloop(self):
        pass


class RefusesToStartWithoutTheEmulator(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name).resolve()
        paths.use_install_dir(self.dir)
        FakeMainWindow.built = 0
        fake = types.ModuleType("qemugui.ui_main")
        fake.MainWindow = FakeMainWindow
        self._saved = sys.modules.get("qemugui.ui_main")
        sys.modules["qemugui.ui_main"] = fake

    def tearDown(self):
        paths.use_install_dir(None)
        if self._saved is None:
            sys.modules.pop("qemugui.ui_main", None)
        else:
            sys.modules["qemugui.ui_main"] = self._saved
        self.td.cleanup()

    def test_missing_binary_stops_the_program(self):
        said = []
        self.assertEqual(qemu_gui.main([], report_problem=said.append), qemu_gui.EXIT_CANNOT_RUN)
        self.assertEqual(len(said), 1)
        self.assertIn("qemu-system-ppc", said[0])
        self.assertEqual(FakeMainWindow.built, 0)
        self.assertFalse((self.dir / "Machines").exists())

    def test_positive_control_the_check_passes_when_the_binary_is_there(self):
        p = self.dir / paths.qemu_binary_name()
        p.write_text("#!/bin/sh\n")
        os.chmod(p, 0o755)
        self.assertEqual(qemu_gui.main([], report_problem=lambda m: self.fail(m)), 0)
        self.assertEqual(FakeMainWindow.built, 1)
        self.assertTrue((self.dir / "Machines").is_dir())


class NoPrivatePaths(unittest.TestCase):
    """The product names no folder of the computer it was written on."""

    def test_tracked_sources(self):
        for p in [ROOT / "qemu_gui.py", ROOT / "QemuGUI.spec", ROOT / "README.md",
                  *sorted((ROOT / "qemugui").glob("*.py")), *sorted(HERE.glob("*.py"))]:
            text = p.read_text(encoding="utf-8")
            for bad in ("/" + "Users/", "/" + "Volumes/"):
                self.assertNotIn(bad, text, p.name)


def _tk_available() -> bool:
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
        return True
    except Exception:
        return False


TK = _tk_available()


def pump(w, cond, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        w.update()
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@unittest.skipUnless(TK, "no display")
class Windows(unittest.TestCase):
    def setUp(self):
        from qemugui import ui_main, ui_panel
        self.ui = ui_main
        self.panel_mod = ui_panel
        ui_panel.STATUS_SECONDS = 0.1
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name).resolve()
        paths.use_install_dir(self.dir)
        (self.dir / "ans700.rom").write_bytes(b"\0" * model.ROM_SIZE)
        self.saved = (ui_main.start_in_terminal, ui_main.start_machine)
        self.calls: list = []
        self.lcd = FakeLcd(lines=["  ROM vers.1.1.22   ", "0512 MB Parity RAM  ",
                                  "195MHz 604, 50MHzBus", "1024KB Level 2 Cache"])
        self.qmp = FakeQMP(keyswitch="normal")
        ui_main.start_in_terminal = lambda m, d: (self.calls.append("terminal"),
                                                  (Path(d) / "run.command", self.qmp.port,
                                                   self.lcd.port))[1]
        lib = model.Library()
        m = model.new_machine("AIX")
        m.rom = "ans700.rom"
        lib.save(m)
        self.w = ui_main.MainWindow(paths.Settings(), settings_path=self.dir / "settings.json")
        self.w.withdraw()

    def tearDown(self):
        self.ui.start_in_terminal, self.ui.start_machine = self.saved
        self.w.destroy()
        self.lcd.close()
        self.qmp.close()
        paths.use_install_dir(None)
        self.td.cleanup()

    def test_the_window_shows_the_machine_and_its_command(self):
        self.assertEqual(self.w.selected_name(), "AIX")
        text = self.w.command_line.get("1.0", "end")
        self.assertIn("ans700,keyswitch=normal", text)
        self.assertIn("socket,id=lcd,host=127.0.0.1", text)
        self.assertEqual(self.w.panel.state_label.cget("text"), "Off")
        self.assertEqual(self.w.panel.view.text(), [" " * 20] * 4)
        self.assertFalse(self.w.panel.view.lit)

    def test_front_panel_follows_a_terminal_run(self):
        self.w._start_terminal(self.w.selected_machine())
        self.assertEqual(self.calls, ["terminal"])
        panel = self.w.panel
        self.assertTrue(pump(self.w, lambda: panel.view.text()[1] == "0512 MB Parity RAM  "))
        self.assertEqual(panel.view.text(), self.lcd.lines)
        self.assertTrue(panel.view.lit)
        self.assertTrue(pump(self.w, lambda: panel.state_label.cget("text") == "Running"))
        self.lcd.set_line(2, "538")
        self.assertTrue(pump(self.w, lambda: panel.view.text()[2] == "538".ljust(20)))
        # the canvas really carries the characters
        item = panel.view.chars[2][0]
        self.assertEqual(panel.view.itemcget(item, "text"), "5")

    def test_turning_the_key_while_running(self):
        self.w._start_terminal(self.w.selected_machine())
        panel = self.w.panel
        self.assertTrue(pump(self.w, lambda: panel.running()))
        panel.key_var.set("service")
        panel._key_turned()
        self.assertEqual(self.qmp.keyswitch, "service")
        self.assertEqual(model.Library().load("AIX").keyswitch, "service")
        self.assertIn("keyswitch=service", self.w.command_line.get("1.0", "end"))
        launcher = (model.Library().folder("AIX") / "run.command").read_text()
        self.assertIn("keyswitch=service", launcher)

    def test_key_turned_in_the_guest_shows(self):
        self.w._start_terminal(self.w.selected_machine())
        panel = self.w.panel
        self.qmp.keyswitch = "locked"
        self.assertTrue(pump(self.w, lambda: panel.key_var.get() == "locked"))

    def test_turning_the_key_while_off_only_changes_the_record(self):
        panel = self.w.panel
        panel.key_var.set("locked")
        panel._key_turned()
        self.assertEqual(model.Library().load("AIX").keyswitch, "locked")
        self.assertEqual(self.qmp.keyswitch, "normal")

    def test_machine_gone_goes_dark(self):
        self.w._start_terminal(self.w.selected_machine())
        panel = self.w.panel
        self.assertTrue(pump(self.w, lambda: panel.view.lit))
        self.lcd.close()
        self.qmp.close()
        self.assertTrue(pump(self.w, lambda: not panel.view.lit
                             and panel.state_label.cget("text") == "Off"))
        self.assertEqual(panel.view.text(), [" " * 20] * 4)

    def test_editor_opens_and_saves(self):
        from qemugui import ui_machine
        ed = ui_machine.MachineEditor(self.w, self.w.selected_machine(), self.w.library,
                                      str(self.dir), self.w._on_editor_save)
        ed.ram_var.set("256")
        ed._row("scsi.1", 2).file.set(str(self.dir / "aix.img"))
        ed.serial_rows[0].on.set(False)
        ed.save = ed.save                      # no dialogs: there is nothing to warn about
        m = ed.collect()
        self.assertEqual(m.ram_mb, 256)
        self.assertEqual([(s.bus, s.id, s.kind) for s in m.scsi], [("scsi.1", 2, "disk")])
        self.assertEqual(m.serial[0].mode, "none")
        self.w._on_editor_save(m, "AIX")
        ed.destroy()
        self.assertEqual(model.Library().load("AIX").ram_mb, 256)


if __name__ == "__main__":
    unittest.main()
