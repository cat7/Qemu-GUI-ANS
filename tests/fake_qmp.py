"""A stand-in for QEMU's QMP server: status, the keyswitch, reset."""

from __future__ import annotations

import json
import socket
import threading


class FakeQMP:
    def __init__(self, keyswitch: str = "normal", status: str = "running"):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.keyswitch = keyswitch
        self.status = status
        self.resets = 0
        self.commands: list = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self.sock.close()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                f = conn.makefile("rw", encoding="utf-8", newline="\n")
                f.write(json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n")
                f.flush()
                try:
                    for line in f:
                        msg = json.loads(line)
                        self.commands.append(msg)
                        f.write(json.dumps({"event": "RTC_CHANGE"}) + "\n")
                        f.write(json.dumps(self._answer(msg)) + "\n")
                        f.flush()
                except OSError:
                    pass

    def _answer(self, msg: dict) -> dict:
        cmd, args = msg["execute"], msg.get("arguments", {})
        if cmd == "qmp_capabilities":
            return {"return": {}}
        if cmd == "query-status":
            return {"return": {"status": self.status, "running": self.status == "running"}}
        if cmd in ("qom-get", "qom-set"):
            if args.get("path") != "/machine" or args.get("property") != "keyswitch":
                return {"error": {"class": "GenericError", "desc": "Property not found"}}
            if cmd == "qom-get":
                return {"return": self.keyswitch}
            if args["value"] not in ("normal", "locked", "service"):
                return {"error": {"class": "GenericError",
                                  "desc": "keyswitch must be normal, locked or service"}}
            self.keyswitch = args["value"]
            return {"return": {}}
        if cmd == "system_reset":
            self.resets += 1
            return {"return": {}}
        return {"error": {"class": "CommandNotFound", "desc": cmd}}
