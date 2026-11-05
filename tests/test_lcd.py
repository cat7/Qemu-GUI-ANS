"""The LCD line parser, the four-line state, and the reconnecting reader."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from qemugui import lcd  # noqa: E402
from fake_lcd import FakeLcd  # noqa: E402

POST = ["  ROM vers.1.1.22   ", "0064 MB Parity RAM  ", "195MHz 604, 50MHzBus",
        "1024KB Level 2 Cache"]


def wait_for(cond, seconds: float = 5.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class ParseLine(unittest.TestCase):
    def test_qemu_lines(self):
        self.assertEqual(lcd.parse_line("lcd2: 195MHz 604, 50MHzBus\n"),
                         (2, "195MHz 604, 50MHzBus"))
        self.assertEqual(lcd.parse_line("lcd0:   ROM vers.1.1.22   "), (0, POST[0]))

    def test_short_is_padded_and_long_is_cut(self):
        self.assertEqual(lcd.parse_line("lcd3: 538"), (3, "538".ljust(20)))
        self.assertEqual(lcd.parse_line("lcd1: " + "x" * 30), (1, "x" * 20))

    def test_blank_line(self):
        self.assertEqual(lcd.parse_line("lcd1: " + " " * 20), (1, lcd.BLANK))
        self.assertEqual(lcd.parse_line("lcd1:"), (1, lcd.BLANK))

    def test_crlf_and_control_characters(self):
        self.assertEqual(lcd.parse_line("lcd0: AB\x01C\r\n"), (0, "AB C".ljust(20)))

    def test_not_lcd_lines(self):
        for text in ("", "qemu-system-ppc: warning", "lcd4: nope", "lcd: x", "xlcd0: y"):
            self.assertIsNone(lcd.parse_line(text), text)


class State(unittest.TestCase):
    def test_lines_in_any_chunking(self):
        data = "".join(f"lcd{i}: {t}\n" for i, t in enumerate(POST)).encode()
        for step in (1, 3, 7, len(data)):
            s = lcd.LcdState()
            s.set_connected(True)
            for k in range(0, len(data), step):
                s.feed(data[k:k + step])
            self.assertEqual(s.lines(), POST, step)

    def test_partial_line_waits_for_its_end(self):
        s = lcd.LcdState()
        s.feed(b"lcd0: half")
        self.assertEqual(s.lines()[0], lcd.BLANK)
        s.feed(b" a line\n")
        self.assertEqual(s.lines()[0], "half a line".ljust(20))

    def test_disconnect_blanks_and_version_bumps(self):
        s = lcd.LcdState()
        v0 = s.version
        s.set_connected(True)
        s.feed(b"lcd1: 0512 MB Parity RAM  \n")
        self.assertGreater(s.version, v0)
        self.assertEqual(s.lines()[1], "0512 MB Parity RAM  ")
        v1 = s.version
        s.set_connected(False)
        self.assertGreater(s.version, v1)
        self.assertEqual(s.lines(), [lcd.BLANK] * 4)
        self.assertFalse(s.connected)

    def test_unchanged_line_keeps_the_version(self):
        s = lcd.LcdState()
        s.feed(b"lcd2: 538\n")
        v = s.version
        s.feed(b"lcd2: 538\n")
        self.assertEqual(s.version, v)


class Client(unittest.TestCase):
    def setUp(self):
        self.client = lcd.LcdClient()
        self.client.RETRY_SECONDS = 0.05
        self.client.start()
        self.servers = []

    def tearDown(self):
        self.client.stop()
        for s in self.servers:
            s.close()

    def server(self, port=0, lines=None):
        s = FakeLcd(port, lines)
        self.servers.append(s)
        return s

    def test_late_connect_gets_the_current_text(self):
        srv = self.server(lines=POST)
        self.client.set_port(srv.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))
        self.assertTrue(self.client.state.connected)

    def test_changes_follow(self):
        srv = self.server(lines=POST)
        self.client.set_port(srv.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))
        srv.set_line(2, "Testing L2Cache SIMM")
        self.assertTrue(wait_for(lambda: self.client.state.lines()[2] == "Testing L2Cache SIMM"))

    def test_nobody_listening_is_dark_then_lights_up(self):
        srv = self.server(lines=POST)
        port = srv.port
        srv.close()
        self.servers.remove(srv)
        self.client.set_port(port)
        time.sleep(0.3)
        self.assertFalse(self.client.state.connected)
        self.assertEqual(self.client.state.lines(), [lcd.BLANK] * 4)
        srv2 = self.server(port, ["538".ljust(20)] + POST[1:])
        self.assertTrue(wait_for(lambda: self.client.state.lines()[0] == "538".ljust(20)))
        self.assertEqual(srv2.connects, 1)

    def test_emulator_gone_goes_dark(self):
        srv = self.server(lines=POST)
        self.client.set_port(srv.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))
        srv.close()
        self.servers.remove(srv)
        self.assertTrue(wait_for(lambda: not self.client.state.connected))
        self.assertEqual(self.client.state.lines(), [lcd.BLANK] * 4)

    def test_dropped_reader_reconnects_and_gets_the_text_again(self):
        srv = self.server(lines=POST)
        self.client.set_port(srv.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))
        srv.drop_reader()
        self.assertTrue(wait_for(lambda: srv.connects == 2))
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))

    def test_switching_machines(self):
        a = self.server(lines=POST)
        b = self.server(lines=["Machine B".ljust(20)] * 4)
        self.client.set_port(a.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines() == POST))
        self.client.set_port(b.port)
        self.assertTrue(wait_for(lambda: self.client.state.lines()[0] == "Machine B".ljust(20)))
        self.client.set_port(None)
        self.assertTrue(wait_for(lambda: not self.client.state.connected))


if __name__ == "__main__":
    unittest.main()
