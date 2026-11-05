"""The control connection (qemugui.qmp) against a stand-in QMP server."""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from qemugui import qmp  # noqa: E402
from fake_qmp import FakeQMP  # noqa: E402


class Control(unittest.TestCase):
    def setUp(self):
        self.q = FakeQMP()

    def tearDown(self):
        self.q.close()

    def test_status_and_key(self):
        self.assertEqual(qmp.status(self.q.port), {"status": "running", "keyswitch": "normal"})

    def test_turn_the_key(self):
        qmp.set_keyswitch(self.q.port, "service")
        self.assertEqual(self.q.keyswitch, "service")
        sent = [c for c in self.q.commands if c["execute"] == "qom-set"][-1]
        self.assertEqual(sent["arguments"], {"path": "/machine", "property": "keyswitch",
                                             "value": "service"})
        self.assertEqual(qmp.status(self.q.port)["keyswitch"], "service")

    def test_a_refusal_is_an_error(self):
        with self.assertRaises(qmp.QMPError) as cm:
            qmp.set_keyswitch(self.q.port, "sideways")
        self.assertIn("keyswitch must be", str(cm.exception))

    def test_reset(self):
        qmp.reset(self.q.port)
        self.assertEqual(self.q.resets, 1)

    def test_paused(self):
        self.q.status = "paused"
        self.assertEqual(qmp.status_label(qmp.status(self.q.port)["status"]), "Paused")

    def test_nobody_listening(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with self.assertRaises(qmp.QMPError):
            qmp.status(port)

    def test_something_else_on_the_port(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        import threading

        def answer():
            c, _ = srv.accept()
            c.sendall(b'{"hello": 1}\n')
            c.close()
        threading.Thread(target=answer, daemon=True).start()
        try:
            with self.assertRaises(qmp.QMPError):
                qmp.status(port)
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
