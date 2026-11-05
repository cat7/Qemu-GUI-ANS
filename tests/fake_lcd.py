"""A stand-in for QEMU's LCD socket: a server that sends lcdN: lines."""

from __future__ import annotations

import socket
import threading


class FakeLcd:
    """Accepts one reader at a time and sends it all four lines on connect,
    as QEMU does; ``set_line`` sends a changed line to the reader."""

    def __init__(self, port: int = 0, lines=None):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.lines = list(lines or [" " * 20] * 4)
        self.conn: socket.socket | None = None
        self.connects = 0
        self.connected = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _send(self, i: int):
        if self.conn is not None:
            try:
                self.conn.sendall(f"lcd{i}: {self.lines[i]}\n".encode("latin-1"))
            except OSError:
                pass

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conn = conn
            self.connects += 1
            for i in range(4):
                self._send(i)
            self.connected.set()

    def set_line(self, i: int, text: str):
        self.lines[i] = text.ljust(20)[:20]
        self._send(i)

    def drop_reader(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.connected.clear()

    def close(self):
        self.drop_reader()
        self.sock.close()
