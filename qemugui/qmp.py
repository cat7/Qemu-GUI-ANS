"""Talk to a running emulator over its control connection (QMP): whether it
runs, where the front keyswitch is, turn the key, press the reset button.

Each machine is started with ``-qmp tcp:127.0.0.1:PORT`` (command.py), so
this works whatever window QEMU shows and also for a run started in
Terminal.
"""

from __future__ import annotations

import json
import socket

TIMEOUT = 2.0


class QMPError(Exception):
    """The emulator could not be reached, or it refused the request."""


def _command(f, msg: dict):
    f.write(json.dumps(msg) + "\n")
    f.flush()
    while True:
        line = f.readline()
        if not line:
            raise QMPError("The emulator closed the connection.")
        reply = json.loads(line)
        if "event" in reply:
            continue
        if "error" in reply:
            raise QMPError(reply["error"].get("desc", "The emulator refused it."))
        return reply.get("return")


def execute_many(port: int, commands: list, timeout: float = TIMEOUT) -> list:
    """Run QMP commands ``[(name, arguments or None), ...]`` on one
    connection; returns their results in order."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            f = s.makefile("rw", encoding="utf-8", newline="\n")
            greeting = json.loads(f.readline() or "{}")
            if "QMP" not in greeting:
                raise QMPError("Something else answers on that port.")
            _command(f, {"execute": "qmp_capabilities"})
            out = []
            for name, arguments in commands:
                msg = {"execute": name}
                if arguments:
                    msg["arguments"] = arguments
                out.append(_command(f, msg))
            return out
    except (OSError, ValueError) as e:
        raise QMPError(f"The emulator cannot be reached ({e}).") from e


def execute(port: int, command: str, arguments: dict | None = None, timeout: float = TIMEOUT):
    return execute_many(port, [(command, arguments)], timeout)[0]


MACHINE_PATH = "/machine"


def status(port: int, timeout: float = TIMEOUT) -> dict:
    """``{"status": "running" | "paused" | ..., "keyswitch": "normal" | ...}``."""
    st, key = execute_many(port, [("query-status", None),
                                  ("qom-get", {"path": MACHINE_PATH, "property": "keyswitch"})],
                           timeout)
    return {"status": (st or {}).get("status", ""), "keyswitch": key}


def set_keyswitch(port: int, position: str) -> None:
    """Turn the front key while the machine runs."""
    execute(port, "qom-set", {"path": MACHINE_PATH, "property": "keyswitch", "value": position})


def reset(port: int) -> None:
    """The front-panel reset button."""
    execute(port, "system_reset")


STATUS_LABELS = {"running": "Running", "paused": "Paused", "shutdown": "Shut down",
                 "prelaunch": "Starting", "inmigrate": "Starting", "internal-error": "Stopped (error)",
                 "guest-panicked": "Stopped (panic)"}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.capitalize() if status else "")
