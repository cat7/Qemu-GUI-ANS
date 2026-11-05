"""The front-panel LCD as the emulator reports it: four lines of twenty
characters.

QEMU's ans700 machine writes every line that changes to its ``lcd=``
chardev as ``lcdN: <20 characters>`` and, when a reader connects, all four
lines at once. The command line (command.py) makes that chardev a local
socket server; :class:`LcdClient` connects to it, reconnects whenever the
connection drops (QEMU not started yet, restarted, started in Terminal), and
keeps an :class:`LcdState` the window draws from.

No Tk in here.
"""

from __future__ import annotations

import re
import socket
import threading

LINES = 4
COLS = 20
BLANK = " " * COLS

LINE_RE = re.compile(r"^lcd([0-3]): ?(.*)$")


def parse_line(text: str) -> tuple | None:
    """``"lcd2: 195MHz 604, 50MHzBus"`` -> ``(2, "195MHz 604, 50MHzBus")``;
    anything else -> None. The text is padded or cut to 20 characters."""
    m = LINE_RE.match(text.rstrip("\r\n"))
    if not m:
        return None
    body = "".join(c if " " <= c <= "~" else " " for c in m.group(2))
    return int(m.group(1)), body[:COLS].ljust(COLS)


class LcdState:
    """The four lines and whether the emulator is connected. Fed bytes from
    the socket in any chunking; read from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._lines = [BLANK] * LINES
        self._pending = b""
        self._connected = False
        self.version = 0          # bumps on every change, for cheap redraws

    def feed(self, data: bytes) -> None:
        with self._lock:
            buf = self._pending + data
            *complete, self._pending = buf.split(b"\n")
            changed = False
            for raw in complete:
                got = parse_line(raw.decode("latin-1"))
                if got and self._lines[got[0]] != got[1]:
                    self._lines[got[0]] = got[1]
                    changed = True
            if changed:
                self.version += 1

    def set_connected(self, on: bool) -> None:
        with self._lock:
            if on == self._connected:
                return
            self._connected = on
            self._pending = b""
            if not on:
                self._lines = [BLANK] * LINES
            self.version += 1

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def lines(self) -> list:
        with self._lock:
            return list(self._lines)

    def snapshot(self) -> tuple:
        with self._lock:
            return self.version, self._connected, list(self._lines)


class LcdClient(threading.Thread):
    """Keeps a connection to one machine's LCD socket while the GUI runs."""

    RETRY_SECONDS = 0.5

    def __init__(self, port: int | None = None, host: str = "127.0.0.1",
                 state: LcdState | None = None):
        super().__init__(daemon=True, name="lcd-client")
        self.host = host
        self.state = state or LcdState()
        self._port = port
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._sock: socket.socket | None = None

    @property
    def port(self) -> int | None:
        with self._lock:
            return self._port

    def set_port(self, port: int | None) -> None:
        """Follow another machine (or none): drop the current connection."""
        with self._lock:
            if port == self._port:
                return
            self._port = port
            sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.state.set_connected(False)
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self.set_port(None)
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            port = self.port
            if port is None:
                self._wake.wait(self.RETRY_SECONDS)
                self._wake.clear()
                continue
            try:
                sock = socket.create_connection((self.host, port), timeout=1.0)
            except OSError:
                self._wake.wait(self.RETRY_SECONDS)
                self._wake.clear()
                continue
            with self._lock:
                if port != self._port:
                    sock.close()
                    continue
                self._sock = sock
            self.state.set_connected(True)
            sock.settimeout(None)
            try:
                while not self._stop.is_set():
                    data = sock.recv(4096)
                    if not data:
                        break
                    self.state.feed(data)
            except OSError:
                pass
            finally:
                with self._lock:
                    self._sock = None
                sock.close()
                if self.port == port:
                    self.state.set_connected(False)
