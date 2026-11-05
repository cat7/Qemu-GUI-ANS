"""Build the QEMU argv for a Machine and render it as run.command / run.bat.

No Tk; the only filesystem access is reading an image's first bytes and
probing for free local ports. Tests pass ``platform=`` explicitly.
"""

from __future__ import annotations

import os
import shlex
import socket
import zlib

from . import model, paths
from .model import Machine

HEADER_NOTE = "Written by Qemu-system-ppc ANS GUI. Do not edit."

LCD_CHARDEV = "lcd"


def qopt(value) -> str:
    """Escape a value for QEMU's key=value option parser (comma -> ,,)."""
    return str(value).replace(",", ",,")


def _path(p: str, base: str, platform: str) -> str:
    return paths.join_path(base, os.path.expanduser(p), platform)


def split_extra_args(text: str, platform: str = paths.HOST_PLATFORM) -> list:
    text = (text or "").strip()
    if not text:
        return []
    lex = shlex.shlex(text, posix=True)
    lex.whitespace_split = True
    lex.commentchars = ""
    if paths.is_windows(platform):
        lex.escape = ""
    return list(lex)


# ---------------------------------------------------------------- ports

# Two local control connections per machine, each machine its own pair, so
# the command shown is the one that runs: QMP (keyswitch, status, reset) and
# the front-panel LCD.
QMP_PORT_BASE = 47000
LCD_PORT_BASE = 48000
PORT_COUNT = 1000


def _own_port(m: Machine, base: str | int) -> int:
    return int(base) + zlib.crc32(m.name.encode("utf-8")) % PORT_COUNT


def qmp_port(m: Machine) -> int:
    return _own_port(m, QMP_PORT_BASE)


def lcd_port(m: Machine) -> int:
    return _own_port(m, LCD_PORT_BASE)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _free_from(base: int, first: int) -> int:
    for k in range(PORT_COUNT):
        port = base + (first - base + k) % PORT_COUNT
        if _port_free(port):
            return port
    return first


def free_ports(m: Machine) -> tuple:
    """(QMP port, LCD port): the machine's own, or the next free ones."""
    return (_free_from(QMP_PORT_BASE, qmp_port(m)), _free_from(LCD_PORT_BASE, lcd_port(m)))


# ---------------------------------------------------------------- pieces

def machine_option(m: Machine, machine_dir: str, platform: str) -> str:
    """-M ans700 with the keyswitch, the NVRAM file, the power supplies and
    the LCD chardev."""
    parts = [model.MACHINE_ID, f"keyswitch={m.keyswitch}"]
    if m.nvram.strip():
        parts.append(f"nvram={qopt(_path(m.nvram.strip(), machine_dir, platform))}")
    if m.power_supplies != 1:
        parts.append(f"power-supplies={m.power_supplies}")
    parts.append(f"lcd={LCD_CHARDEV}")
    return ",".join(parts)


def lcd_args(port: int) -> list:
    """The LCD lines go out on a local socket the GUI connects to."""
    return ["-chardev", f"socket,id={LCD_CHARDEV},host=127.0.0.1,port={port},"
                        "server=on,wait=off"]


def serial_args(m: Machine) -> list:
    """ESCC channel A, then B: a telnet server on a local port, or nothing."""
    args = []
    for i, sp in enumerate(m.serial[:2]):
        if sp.mode == "telnet":
            cid = f"serial{'ab'[i]}"
            args += ["-chardev", f"socket,id={cid},host=127.0.0.1,port={int(sp.port)},"
                                 "server=on,wait=off,telnet=on",
                     "-serial", f"chardev:{cid}"]
        else:
            args += ["-serial", "null"]
    return args


def display_option(m: Machine) -> str:
    if m.display == "cocoa" and m.swap_opt_cmd:
        return "cocoa,swap-opt-cmd=on"
    return m.display


def nic_option(net) -> str:
    """-nic for the built-in MACE Ethernet (the machine's only network card)."""
    if net.mode == "none":
        return "none"
    tail = f"mac={net.mac}"
    if net.mode == "user":
        fwd = "".join(f",hostfwd={r.proto}:127.0.0.1:{r.host_port}-:{r.guest_port}"
                      for r in net.hostfwd if not r.empty)
        return f"user,{tail}{fwd}"
    if net.mode in model.NETWORK_MODES_WITH_IFNAME:
        return f"{net.mode},ifname={qopt(net.ifname)},{tail}"
    if net.mode in ("vmnet-shared", "vmnet-host"):
        return f"{net.mode},{tail}"
    raise ValueError(f"unknown network mode {net.mode!r}")


def drive_format(stored: str, file: str, base: str) -> str:
    """A stored non-raw value stands; "raw" is checked against the file."""
    fmt = (stored or "").strip()
    if fmt and fmt != "raw":
        return fmt
    path = paths.join_path(base, os.path.expanduser(file), paths.HOST_PLATFORM)
    if not os.path.isfile(path):
        return "raw"
    return model.detect_format(path)


def scsi_args(s, machine_dir: str, platform: str) -> list:
    name = qopt(_path(s.file, machine_dir, platform))
    tag = f"{s.bus.replace('scsi.', 's')}id{s.id}"
    if s.kind == "cdrom":
        did = f"cd-{tag}"
        return ["-drive", f"if=none,id={did},file={name},format=raw,media=cdrom,readonly=on",
                "-device", f"scsi-cd,drive={did},bus={s.bus},scsi-id={s.id}"]
    did = f"hd-{tag}"
    fmt = drive_format(s.format, s.file, machine_dir)
    return ["-drive", f"if=none,id={did},file={name},format={fmt}",
            "-device", f"scsi-hd,drive={did},bus={s.bus},scsi-id={s.id}"]


def qmp_args(port: int) -> list:
    return ["-qmp", f"tcp:127.0.0.1:{port},server=on,wait=off"]


def needs_sudo(m: Machine, platform: str = paths.HOST_PLATFORM) -> bool:
    """vmnet-* and low slirp forward ports run under sudo (macOS only)."""
    return (m.network.needs_sudo or m.network.low_host_port) and not paths.is_windows(platform)


def build_argv(m: Machine, qemu_dir: str, machine_dir: str,
               platform: str = paths.HOST_PLATFORM,
               qmp: int | None = None, lcd: int | None = None) -> list:
    """The complete argv, first token = absolute path of the QEMU binary.
    *qmp* and *lcd* are the local ports (default: the machine's own)."""
    argv = [paths.join_path(qemu_dir, paths.qemu_binary_name(platform), platform)]
    argv += ["-M", machine_option(m, machine_dir, platform)]
    argv += ["-m", str(int(m.ram_mb))]
    if m.rom:
        argv += ["-bios", _path(m.rom, qemu_dir, platform)]
    argv += lcd_args(lcd if lcd is not None else lcd_port(m))
    argv += serial_args(m)
    argv += ["-display", display_option(m)]
    argv += ["-nic", nic_option(m.network)]
    order = {b: i for i, b in enumerate(model.SCSI_BUS_NAMES)}
    for s in sorted(m.scsi, key=lambda x: (order.get(x.bus, 9), x.id)):
        if s.file and s.bus in order:
            argv += scsi_args(s, machine_dir, platform)
    argv += qmp_args(qmp if qmp is not None else qmp_port(m))
    argv += split_extra_args(m.extra_args, platform)
    return argv


def ports_in(argv: list) -> tuple:
    """(QMP port, LCD port) a built argv uses."""
    qmp = lcd = None
    for i, tok in enumerate(argv[:-1]):
        nxt = argv[i + 1]
        if tok == "-qmp" and nxt.startswith("tcp:127.0.0.1:"):
            qmp = int(nxt.split(":")[2].split(",")[0])
        if tok == "-chardev" and nxt.startswith(f"socket,id={LCD_CHARDEV},"):
            for kv in nxt.split(","):
                if kv.startswith("port="):
                    lcd = int(kv[5:])
    return qmp, lcd


# ---------------------------------------------------------------- rendering

def group_options(argv: list) -> list:
    groups: list = []
    for tok in argv[1:]:
        if tok.startswith("-") or not groups:
            groups.append([tok])
        else:
            groups[-1].append(tok)
    return groups


# Ask for the password once; keep sudo's ticket alive for a long run.
SUDO_KEEPALIVE = ('sudo -v\n'
                  'while true; do sudo -n true; sleep 60; '
                  'kill -0 "$$" 2>/dev/null || exit; done &\n'
                  'SUDO_KEEPALIVE_PID=$!')
CHOWN_LINE = ('kill "$SUDO_KEEPALIVE_PID" 2>/dev/null\n'
              f'sudo -n chown "${{SUDO_USER:-$(id -un)}}" {model.NVRAM_NAME} 2>/dev/null')


def render_shell(argv: list, sudo: bool = False) -> str:
    lines = ["#!/bin/bash", f"# {HEADER_NOTE}", 'cd "$(dirname "$0")"', ""]
    if sudo:
        lines += [SUDO_KEEPALIVE, ""]
    lines.append(("sudo " if sudo else "") + shlex.quote(argv[0]) + " \\")
    groups = group_options(argv)
    for i, g in enumerate(groups):
        cont = " \\" if i < len(groups) - 1 else ""
        lines.append(" ".join(shlex.quote(t) for t in g) + cont)
    if sudo:
        lines += ["", CHOWN_LINE]
    return "\n".join(lines) + "\n"


def bat_quote(token: str) -> str:
    t = token.replace("%", "%%")
    if (" " in t or "," in t) and not (t.startswith('"') and t.endswith('"')):
        return f'"{t}"'
    return t


BAT_TITLE = "Network Server"
BAT_MODE = "mode con: cols=100 lines=30"
BAT_PAUSE = "if errorlevel 1 pause"


def bat_title(name: str) -> str:
    text = "".join(c for c in str(name or "") if c.isprintable() and c not in '&<>|^"%')
    return text.strip()[:40] or BAT_TITLE


def render_bat(argv: list, title: str = "") -> str:
    lines = ["@echo off", f"rem {HEADER_NOTE}", f"title {bat_title(title)}", BAT_MODE,
             'cd /d "%~dp0"', "", bat_quote(argv[0]) + " ^"]
    groups = group_options(argv)
    for i, g in enumerate(groups):
        cont = " ^" if i < len(groups) - 1 else ""
        lines.append(" ".join(bat_quote(t) for t in g) + cont)
    lines += ["", BAT_PAUSE]
    return "\r\n".join(lines) + "\r\n"


def render_launcher(argv: list, platform: str = paths.HOST_PLATFORM, sudo: bool = False,
                    title: str = "") -> str:
    return render_bat(argv, title) if paths.is_windows(platform) else render_shell(argv, sudo)


def one_line(argv: list, machine_dir: str, platform: str = paths.HOST_PLATFORM,
             sudo: bool = False) -> str:
    if paths.is_windows(platform):
        return f'cd /d {bat_quote(machine_dir)} && ' + " ".join(bat_quote(t) for t in argv)
    return f"cd {shlex.quote(machine_dir)} && " + ("sudo " if sudo else "") + shlex.join(argv)


def launcher_text(m: Machine, qemu_dir: str, machine_dir: str,
                  platform: str = paths.HOST_PLATFORM) -> str:
    return render_launcher(build_argv(m, qemu_dir, machine_dir, platform), platform,
                           needs_sudo(m, platform), m.name)


def write_launcher(m: Machine, qemu_dir: str, machine_dir: str,
                   platform: str = paths.HOST_PLATFORM, qmp: int | None = None,
                   lcd: int | None = None):
    """Write run.command / run.bat into the machine folder; returns (path, argv)."""
    from pathlib import Path
    import stat
    argv = build_argv(m, qemu_dir, machine_dir, platform, qmp, lcd)
    text = render_launcher(argv, platform, needs_sudo(m, platform), m.name)
    path = Path(machine_dir) / paths.launcher_name(platform)
    path.write_text(text, encoding="utf-8", newline="")
    if not paths.is_windows(platform):
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path, argv
