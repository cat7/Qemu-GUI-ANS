"""The generated command line against the real emulator, with the GUI's own
LCD code reading the real front panel.

Off unless asked for; needs qemu-system-ppc with the ans700 machine and the
Network Server ROM. No disk is needed: POST and Open Firmware write the LCD.

    QEMUGUI_LIVE=1 QEMUGUI_QEMU_DIR=/path/to/qemu QEMUGUI_ROM=/path/to/ans700.rom \\
        python -m unittest tests.test_live -v

Optional: QEMUGUI_DISK=<disk image> (attached at internal bus 0 ID 2 with
-snapshot, so it is never changed), QEMUGUI_LIVE_LOG=<file> (the LCD lines
seen, with times), QEMUGUI_LIVE_SECONDS (how long to watch, default 40).

The argv is exactly what the GUI builds except: ``-display none``, serial
ports off, no port forwarding, and ``-snapshot``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import command, lcd, model, qmp  # noqa: E402

LIVE = os.environ.get("QEMUGUI_LIVE") == "1"
QEMU_DIR = os.environ.get("QEMUGUI_QEMU_DIR", "")
ROM = os.environ.get("QEMUGUI_ROM", "")
DISK = os.environ.get("QEMUGUI_DISK", "")
LOG = os.environ.get("QEMUGUI_LIVE_LOG", "")
SECONDS = float(os.environ.get("QEMUGUI_LIVE_SECONDS", "40"))


def wait_for(cond, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


@unittest.skipUnless(LIVE, "set QEMUGUI_LIVE=1 to run the emulator")
class LiveFrontPanel(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        lib = model.Library(Path(self.td.name) / "Machines")
        m = model.new_machine("live-ans")
        m.rom = ROM
        m.display = "none"
        m.serial = [model.SerialPort("none", 0), model.SerialPort("none", 0)]
        m.network.hostfwd = []
        if DISK:
            m.scsi = [model.ScsiDrive("scsi.1", 2, "disk", DISK, "raw")]
        self.folder = lib.save(m)
        self.qmp_port, self.lcd_port = command.free_ports(m)
        self.argv = command.build_argv(m, QEMU_DIR, str(self.folder), qmp=self.qmp_port,
                                       lcd=self.lcd_port) + ["-snapshot"]
        print("\n" + command.one_line(self.argv, str(self.folder)))
        self.out = open(Path(self.td.name) / "qemu.log", "w")
        self.proc = subprocess.Popen(self.argv, cwd=self.folder, stdout=self.out,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.seen: list = []

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.out.close()
        if LOG:
            Path(LOG).write_text("".join(self.seen), encoding="utf-8")
        self.td.cleanup()

    def watch(self, client: lcd.LcdClient, label: str, seconds: float):
        t0 = time.time()
        last = None
        deadline = t0 + seconds
        while time.time() < deadline and self.proc.poll() is None:
            v, on, lines = client.state.snapshot()
            if v != last:
                last = v
                self.seen.append(f"[{label} +{time.time() - t0:5.1f}s lit={on}]\n"
                                 + "".join(f"  |{t}|\n" for t in lines))
            time.sleep(0.05)

    def test_lcd_keyswitch_and_late_reader(self):
        first = lcd.LcdClient(self.lcd_port)
        first.RETRY_SECONDS = 0.05
        first.start()
        try:
            self.assertTrue(wait_for(lambda: first.state.connected, 10), "no LCD socket")
            self.watch(first, "from power-on", SECONDS)
            self.assertIsNone(self.proc.poll(), "QEMU exited")
            lines = first.state.lines()
        finally:
            first.stop()
        seen = "".join(self.seen)
        self.assertIn("|  ROM vers.1.1.22   |", seen)        # POST, line 1
        self.assertIn("MB Parity RAM", seen)                # POST, line 2
        if DISK:
            self.assertRegex(seen, r"\|\s+[0-9A-C]\d\d\s+\|")    # an AIX start-up code

        # a reader that connects late gets the current four lines at once
        late = lcd.LcdClient(self.lcd_port)
        late.start()
        try:
            self.assertTrue(wait_for(lambda: late.state.connected, 5))
            self.assertTrue(wait_for(lambda: late.state.lines() == lines, 5),
                            (late.state.lines(), lines))
            self.watch(late, "late reader", 1)
        finally:
            late.stop()

        st = qmp.status(self.qmp_port)
        self.assertEqual(st, {"status": "running", "keyswitch": "normal"})
        qmp.set_keyswitch(self.qmp_port, "locked")
        self.assertEqual(qmp.status(self.qmp_port)["keyswitch"], "locked")
        qmp.set_keyswitch(self.qmp_port, "normal")


if __name__ == "__main__":
    unittest.main()
