"""The machine record: dataclasses, JSON load/save, checks, machine folders.

No Tk in here. ``machine.json`` schema version 1.

Three rules this file exists to enforce:

* Machines live in the ``Machines`` folder next to the program.
* **Nothing here ever deletes a disk image.** Deleting a machine removes the
  handful of files the program itself wrote (``OWNED_FILES``) and leaves
  every other file exactly where it is.
* **Nothing here chooses a disk or a ROM.** Drives start empty; the only
  file a new machine names is its own NVRAM file inside its own folder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

SCHEMA = 1
NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# ---------------------------------------------------------------- the machine

MACHINE_ID = "ans700"                    # the -M value
MACHINE_LABEL = "Apple Network Server 700/200"
ROM_SIZE = 4 * 1024 * 1024               # the Open Firmware 1.1.22 image

# Memory: matched DIMM pairs, four pairs, DIMMs of up to 64 MB. QEMU's
# Hammerhead fills each pair with the largest power of two (<= 64 MB) that
# fits half of what is left; a size that leaves something over is refused.
RAM_MAX = 512
RAM_MIN = 16
RAM_DEFAULT = 512
RAM_CHOICES = (16, 32, 48, 64, 96, 128, 192, 256, 384, 512)
DIMM_MAX_MB = 64
DIMM_PAIRS = 4


def ram_fits(mb: int) -> bool:
    """*mb* splits into the DIMM pairs the board takes."""
    if not isinstance(mb, int) or not RAM_MIN <= mb <= RAM_MAX:
        return False
    rest = mb
    for _pair in range(DIMM_PAIRS):
        if rest < 2:
            break
        dimm = DIMM_MAX_MB
        while dimm > rest / 2:
            dimm //= 2
        rest -= 2 * dimm
    return rest == 0


# The front keyswitch: QEMU's values, in the order of the real key.
KEYSWITCH = ("service", "normal", "locked")
KEYSWITCH_LABELS = {"service": "Service", "normal": "Unlocked", "locked": "Locked"}
KEYSWITCH_DEFAULT = "normal"


def keyswitch_by_label(label: str) -> str:
    for k, v in KEYSWITCH_LABELS.items():
        if v == label:
            return k
    return KEYSWITCH_DEFAULT


POWER_SUPPLIES = (1, 2)

# ---------------------------------------------------------------- SCSI


@dataclass(frozen=True)
class ScsiBus:
    bus: str                  # QEMU's bus name
    label: str
    note: str


# The two Fast/Wide 53C825A controllers carry the drive bays; the 53C94 is
# the narrow external bus. Open Firmware's aliases: disk0-disk3 are IDs 0-3
# on the first 53C825A, disk4-disk6 IDs 4-6 on the second.
SCSI_BUSES = (
    ScsiBus("scsi.1", "Internal bus 0 (53C825A, PCI @11)",
            "Front bays: IDs 0-3 are disk0-disk3 in Open Firmware."),
    ScsiBus("scsi.2", "Internal bus 1 (53C825A, PCI @12)",
            "Front bays: IDs 4-6 are disk4-disk6 in Open Firmware."),
    ScsiBus("scsi.0", "External bus (53C94)",
            "The narrow SCSI-1 connector on the back."),
)
SCSI_BUS_NAMES = tuple(b.bus for b in SCSI_BUSES)
SCSI_IDS = list(range(7))         # 7 is the controller
SCSI_SELF_ID = 7
SCSI_SELF_LABEL = "Controller"


def scsi_bus(bus: str) -> ScsiBus | None:
    for b in SCSI_BUSES:
        if b.bus == bus:
            return b
    return None


def of_alias(bus: str, sid: int) -> str:
    """Open Firmware's devalias for a drive, or ''."""
    if bus == "scsi.1" and 0 <= sid <= 3:
        return f"disk{sid}"
    if bus == "scsi.2" and 4 <= sid <= 6:
        return f"disk{sid}"
    return ""


def scsi_name(sid: int) -> str:
    return f"ID {sid}"


DRIVE_KINDS = ("disk", "cdrom")
FORMATS = ("raw", "qcow2")
CDROM_EXTS = {".iso", ".cdr", ".toast"}

FORMAT_MAGIC = ((b"QFI\xfb", "qcow2"),)


def detect_format(path: str) -> str:
    """"qcow2" or "raw" by the file's magic number. Never raises."""
    try:
        with open(str(path or ""), "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "qcow2" if str(path).lower().endswith((".qcow2", ".qcow")) else "raw"
    for magic, fmt in FORMAT_MAGIC:
        if head.startswith(magic):
            return fmt
    return "raw"


@dataclass
class ScsiDrive:
    bus: str = "scsi.1"
    id: int = 0
    kind: str = "disk"           # disk | cdrom
    file: str = ""
    format: str = "raw"

    def to_dict(self) -> dict:
        return {"bus": self.bus, "id": self.id, "kind": self.kind, "file": self.file,
                "format": self.format}

    @classmethod
    def from_dict(cls, d: Any) -> "ScsiDrive | None":
        if not isinstance(d, dict):
            return None
        try:
            sid = int(d.get("id", 0))
        except (TypeError, ValueError):
            return None
        return cls(str(d.get("bus", "scsi.1")), sid, str(d.get("kind", "disk")),
                   str(d.get("file", "")), str(d.get("format", "raw")))


# ---------------------------------------------------------------- serial

SERIAL_MODES = ("none", "telnet")
SERIAL_NAMES = ("Serial port A (modem)", "Serial port B (printer)")
SERIAL_DEFAULT_PORTS = (4555, 4556)


@dataclass
class SerialPort:
    mode: str = "telnet"         # none | telnet
    port: int = 0

    def to_dict(self) -> dict:
        return {"mode": self.mode, "port": self.port}

    @classmethod
    def from_dict(cls, d: Any, default_port: int) -> "SerialPort":
        if not isinstance(d, dict):
            return cls("telnet", default_port)
        try:
            port = int(d.get("port", default_port))
        except (TypeError, ValueError):
            port = default_port
        return cls(str(d.get("mode", "telnet")), port)


def default_serials() -> list:
    return [SerialPort("telnet", p) for p in SERIAL_DEFAULT_PORTS]


# ---------------------------------------------------------------- network

DEFAULT_MAC = "08:00:07:4e:53:01"   # Apple's prefix
NETWORK_MODES = ("user", "none", "vmnet-bridged", "vmnet-shared", "vmnet-host", "tap")
NETWORK_MODE_PLATFORM = {"user": None, "none": None, "vmnet-bridged": "darwin",
                         "vmnet-shared": "darwin", "vmnet-host": "darwin", "tap": "win32"}
NETWORK_MODES_WITH_IFNAME = ("vmnet-bridged", "tap")
NETWORK_MODE_LABELS = {"user": "default (slirp)"}
HOSTFWD_PROTOS = ("tcp", "udp")
HOSTFWD_ROWS = 4


@dataclass
class HostFwd:
    proto: str = "tcp"
    host_port: str = ""
    guest_port: str = ""

    def to_dict(self) -> dict:
        return {"proto": self.proto, "host_port": self.host_port, "guest_port": self.guest_port}

    @classmethod
    def from_dict(cls, d: Any) -> "HostFwd":
        if not isinstance(d, dict):
            return cls()
        return cls(str(d.get("proto") or "tcp"), str(d.get("host_port") or "").strip(),
                   str(d.get("guest_port") or "").strip())

    @property
    def empty(self) -> bool:
        return not (self.host_port or self.guest_port)


def _port_ok(text) -> bool:
    text = str(text)
    return text.isascii() and text.isdigit() and 1 <= int(text) <= 65535


def default_hostfwd() -> list:
    """telnet into AIX from the host: 127.0.0.1:2323 -> the guest's port 23."""
    return [HostFwd("tcp", "2323", "23")]


@dataclass
class Network:
    mode: str = "user"
    mac: str = DEFAULT_MAC
    ifname: str = ""
    hostfwd: list = field(default_factory=default_hostfwd)

    def to_dict(self) -> dict:
        d = {"mode": self.mode, "mac": self.mac}
        if self.mode in NETWORK_MODES_WITH_IFNAME or self.ifname:
            d["ifname"] = self.ifname
        d["hostfwd"] = [r.to_dict() for r in self.hostfwd if not r.empty]
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "Network":
        if not isinstance(d, dict):
            return cls()
        rules = d.get("hostfwd")
        return cls(str(d.get("mode", "user")), str(d.get("mac") or DEFAULT_MAC),
                   str(d.get("ifname", "") or ""),
                   [HostFwd.from_dict(r) for r in rules] if isinstance(rules, list) else [])

    @property
    def needs_sudo(self) -> bool:
        return self.mode.startswith("vmnet-")

    @property
    def low_host_port(self) -> bool:
        return self.mode == "user" and any(
            not r.empty and str(r.host_port).strip().isdigit() and 0 < int(r.host_port) < 1024
            for r in self.hostfwd)

    @property
    def platform(self) -> str | None:
        return NETWORK_MODE_PLATFORM.get(self.mode)


def network_mode_label(mode: str) -> str:
    return NETWORK_MODE_LABELS.get(mode, mode)


def network_mode_by_label(label: str) -> str:
    for mode, shown in NETWORK_MODE_LABELS.items():
        if shown == label:
            return mode
    return label


def network_modes_for_host(platform: str = paths.HOST_PLATFORM, current: str | None = None) -> list:
    host = "win32" if paths.is_windows(platform) else platform
    out = [m for m in NETWORK_MODES if NETWORK_MODE_PLATFORM[m] in (None, host)]
    if current and current not in out:
        out.append(current)
    return out


def network_labels_for_host(platform: str = paths.HOST_PLATFORM, current: str | None = None) -> list:
    return [network_mode_label(m) for m in network_modes_for_host(platform, current)]


def default_ifname(mode: str, platform: str = paths.HOST_PLATFORM) -> str:
    return "en0" if mode == "vmnet-bridged" and platform == "darwin" else ""


def ifname_label(platform: str = paths.HOST_PLATFORM) -> str:
    return "Vmnet host interface:" if platform == "darwin" else "Tap device name:"


# ---------------------------------------------------------------- display

# "none": no window; the 54M30 is still there, the LCD and serial ports work.
DISPLAYS = {"darwin": ("cocoa", "sdl", "none"), "win32": ("sdl", "gtk", "none"),
            "linux": ("sdl", "gtk", "none")}


def displays_for_host(platform: str = paths.HOST_PLATFORM) -> tuple:
    return DISPLAYS.get("win32" if paths.is_windows(platform) else platform, ("sdl", "gtk", "none"))


def default_display(platform: str = paths.HOST_PLATFORM) -> str:
    return displays_for_host(platform)[0]


# ---------------------------------------------------------------- video card

# "54m30": the built-in Cirrus only (AIX). "rage128pro": an ATI Rage 128 Pro
# in PCI slot D of the second bridge (the only slots ROM 2.0 probes there)
# and no built-in video, so the window shows the card Mac OS uses.
VIDEO_CARDS = ("54m30", "rage128pro")
VIDEO_LABELS = {"54m30": "Built-in Cirrus 54M30 (AIX)",
                "rage128pro": "ATI Rage 128 Pro (Mac OS)"}
VIDEO_DEFAULT = "54m30"
RAGE_BUS = "bandit@f4000000"
RAGE_ADDR = 0x0D
ATI_VENDOR = 0x1002


def video_by_label(label: str) -> str:
    for k, v in VIDEO_LABELS.items():
        if v == label:
            return k
    return VIDEO_DEFAULT


def rom_pci_id(path: str) -> tuple | None:
    """(vendor, device) from a PCI option ROM's PCIR block, or None."""
    try:
        with open(str(path or ""), "rb") as fh:
            head = fh.read(0x10000)
    except OSError:
        return None
    if len(head) < 0x1c or head[:2] != b"\x55\xaa":
        return None
    off = int.from_bytes(head[0x18:0x1a], "little")
    if off + 8 > len(head) or head[off:off + 4] != b"PCIR":
        return None
    return (int.from_bytes(head[off + 4:off + 6], "little"),
            int.from_bytes(head[off + 6:off + 8], "little"))


# ---------------------------------------------------------------- NVRAM

# Grand Central's NVRAM, in a file of the machine's own. Blank (all zero) =
# a machine that has never been set up: Open Firmware talks on the screen and
# the ADB keyboard, and with the key at Service it looks for an install CD.
NVRAM_NAME = "nvram.bin"
NVRAM_SIZE = 8192

OWNED_FILES = ("machine.json", "run.command", "run.bat", "last-run.log", NVRAM_NAME, ".DS_Store")

IMAGE_SUFFIXES = {".img", ".dsk", ".qcow2", ".iso", ".cdr", ".toast", ".raw", ".image"}


def looks_like_disk_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


# ---------------------------------------------------------------- record

@dataclass
class Machine:
    name: str = "New machine"
    ram_mb: int = RAM_DEFAULT
    rom: str = ""                    # the person's choice
    nvram: str = NVRAM_NAME          # relative = in the machine's folder; "" = not kept
    keyswitch: str = KEYSWITCH_DEFAULT
    power_supplies: int = 1
    display: str = "cocoa"
    swap_opt_cmd: bool = False       # cocoa only
    video: str = VIDEO_DEFAULT
    video_rom: str = ""              # the card's ROM, for rage128pro
    network: Network = field(default_factory=Network)
    scsi: list = field(default_factory=list)
    serial: list = field(default_factory=default_serials)
    extra_args: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        order = {b: i for i, b in enumerate(SCSI_BUS_NAMES)}
        return {
            "schema": SCHEMA,
            "name": self.name,
            "machine": MACHINE_ID,
            "ram_mb": self.ram_mb,
            "rom": self.rom,
            "nvram": self.nvram,
            "keyswitch": self.keyswitch,
            "power_supplies": self.power_supplies,
            "display": self.display,
            "swap_opt_cmd": self.swap_opt_cmd,
            "video": self.video,
            "video_rom": self.video_rom,
            "network": self.network.to_dict(),
            "scsi": [d.to_dict() for d in sorted(self.scsi, key=lambda s: (order.get(s.bus, 9), s.id))],
            "serial": [s.to_dict() for s in self.serial],
            "extra_args": self.extra_args,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Machine":
        serial = d.get("serial")
        serial = serial if isinstance(serial, list) else []
        ports = [SerialPort.from_dict(serial[i] if i < len(serial) else None,
                                      SERIAL_DEFAULT_PORTS[i]) for i in range(2)]
        try:
            ram = int(d.get("ram_mb", RAM_DEFAULT))
        except (TypeError, ValueError):
            ram = RAM_DEFAULT
        try:
            psu = int(d.get("power_supplies", 1))
        except (TypeError, ValueError):
            psu = 1
        return cls(
            name=str(d.get("name", "New machine")),
            ram_mb=ram,
            rom=str(d.get("rom", "") or ""),
            nvram=str(d.get("nvram", NVRAM_NAME) or ""),
            keyswitch=str(d.get("keyswitch", KEYSWITCH_DEFAULT)),
            power_supplies=psu,
            display=str(d.get("display") or default_display()),
            swap_opt_cmd=bool(d.get("swap_opt_cmd", False)),
            video=str(d.get("video") or VIDEO_DEFAULT),
            video_rom=str(d.get("video_rom", "") or ""),
            network=Network.from_dict(d.get("network")),
            scsi=[s for s in (ScsiDrive.from_dict(x) for x in d.get("scsi") or []) if s],
            serial=ports,
            extra_args=str(d.get("extra_args", "")),
            notes=str(d.get("notes", "")),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Machine":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path) -> "Machine":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def copy(self) -> "Machine":
        return Machine.from_dict(self.to_dict())

    def scsi_at(self, bus: str, sid: int) -> ScsiDrive | None:
        for s in self.scsi:
            if s.bus == bus and s.id == sid:
                return s
        return None

    def first_free_scsi(self, bus: str = "scsi.1", prefer: int = 0) -> int | None:
        for sid in [prefer] + [i for i in SCSI_IDS if i != prefer]:
            if self.scsi_at(bus, sid) is None:
                return sid
        return None

    def image_paths(self) -> list:
        return [s.file for s in self.scsi if s.file]


def new_machine(name: str) -> Machine:
    """No disk, CD or ROM is filled in."""
    return Machine(name=name, display=default_display())


def nvram_path(m: Machine, machine_dir: str | Path) -> Path | None:
    if not m.nvram.strip():
        return None
    return Path(paths.join_path(str(machine_dir), str(Path(m.nvram.strip()).expanduser()),
                                paths.HOST_PLATFORM))


def write_blank_nvram(path: Path | str) -> Path:
    """8 KB of zeros. Refuses to write over an existing file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "xb") as fh:
        fh.write(b"\0" * NVRAM_SIZE)
    return p


def start_blockers(m: Machine, qemu_dir: str | None = None) -> list:
    """Reasons this machine cannot be started yet (it can still be saved)."""
    out = []
    if not (m.rom or "").strip():
        out.append("No ROM chosen.")
    elif qemu_dir and not Path(paths.join_path(qemu_dir, m.rom, paths.HOST_PLATFORM)).is_file():
        out.append(f"The ROM is missing: {paths.join_path(qemu_dir, m.rom, paths.HOST_PLATFORM)}")
    if m.video == "rage128pro" and not (m.video_rom or "").strip():
        out.append("No ROM chosen for the Rage 128 Pro.")
    return out


# ---------------------------------------------------------------- checking

def validate(m: Machine, qemu_dir: str | None, platform: str = paths.HOST_PLATFORM,
             check_files: bool = True, machine_dir: str | None = None) -> tuple:
    """(things that must be fixed, things worth knowing)."""
    errors: list = []
    warnings: list = []

    if not m.name or not m.name.strip():
        errors.append("This machine has no name.")
    elif not NAME_RE.match(m.name) or m.name.strip() != m.name:
        errors.append("That name will not work.")
    if not isinstance(m.ram_mb, int) or not RAM_MIN <= m.ram_mb <= RAM_MAX:
        errors.append(f"Memory has to be between {RAM_MIN} and {RAM_MAX} MB.")
    elif not ram_fits(m.ram_mb):
        errors.append(f"{m.ram_mb} MB does not split into pairs of DIMMs; "
                      f"try {', '.join(str(x) for x in RAM_CHOICES)} MB.")
    if m.keyswitch not in KEYSWITCH:
        errors.append(f"'{m.keyswitch}' is not a keyswitch position.")
    if m.power_supplies not in POWER_SUPPLIES:
        errors.append("The Network Server has one or two power supplies.")
    if m.display not in ("cocoa", "sdl", "gtk", "none"):
        errors.append(f"'{m.display}' is not a display setting.")
    elif m.display == "cocoa" and platform != "darwin":
        warnings.append("'cocoa' only works on a Mac.")

    if m.video not in VIDEO_CARDS:
        errors.append(f"'{m.video}' is not a video card setting.")

    net = m.network
    if net.mode not in NETWORK_MODES:
        errors.append(f"'{net.mode}' is not a network setting.")
    else:
        if net.mode != "none" and not MAC_RE.match(net.mac):
            errors.append("The Ethernet address has to look like 08:00:07:12:34:56.")
        if net.mode in NETWORK_MODES_WITH_IFNAME and not net.ifname.strip():
            errors.append("No interface named.")
        for r in net.hostfwd:
            if r.empty:
                continue
            if r.proto not in HOSTFWD_PROTOS:
                errors.append(f"'{r.proto}' is not a forwarding protocol.")
            if not (_port_ok(r.host_port) and _port_ok(r.guest_port)):
                errors.append("Port forwarding needs host and guest ports from 1 to 65535.")
        host = "win32" if paths.is_windows(platform) else platform
        if net.platform is not None and net.platform != host:
            warnings.append("This network setting only works on "
                            f"{'a Mac' if net.platform == 'darwin' else 'Windows'}.")

    used_ports: dict = {}
    for i, sp in enumerate(m.serial):
        if sp.mode not in SERIAL_MODES:
            errors.append(f"{SERIAL_NAMES[i]}: '{sp.mode}' is not a setting.")
        elif sp.mode == "telnet":
            if not _port_ok(sp.port):
                errors.append(f"{SERIAL_NAMES[i]}: the port has to be from 1 to 65535.")
            elif sp.port in used_ports:
                errors.append(f"{SERIAL_NAMES[i]} and {SERIAL_NAMES[used_ports[sp.port]]} "
                              "use the same port.")
            else:
                used_ports[sp.port] = i
    if net.mode == "user":
        for r in net.hostfwd:
            if not r.empty and _port_ok(r.host_port) and int(r.host_port) in used_ports:
                errors.append(f"Host port {r.host_port} is both forwarded and a serial port.")

    seen = set()
    for s in m.scsi:
        b = scsi_bus(s.bus)
        if b is None:
            errors.append(f"'{s.bus}' is not a SCSI bus of this machine.")
            continue
        if (s.bus, s.id) in seen:
            errors.append(f"{b.label}: two drives are both set to ID {s.id}.")
        seen.add((s.bus, s.id))
        if s.id == SCSI_SELF_ID:
            errors.append(f"{b.label}: ID {SCSI_SELF_ID} is the controller.")
        elif s.id not in SCSI_IDS:
            errors.append(f"{b.label}: ID {s.id} does not exist.")
        if s.kind not in DRIVE_KINDS:
            errors.append(f"{b.label} ID {s.id} has no drive type.")

    if check_files:
        qd = qemu_dir or ""
        if m.rom:
            rom = Path(paths.join_path(qd, m.rom, platform)) if qd else Path(m.rom).expanduser()
            if rom.is_absolute() or qd:
                try:
                    size = rom.stat().st_size
                except OSError:
                    size = None
                if size is None:
                    warnings.append(f"The ROM is missing: {rom}")
                elif size != ROM_SIZE:
                    warnings.append(f"{rom.name} is not a 4 MB ROM image.")
        if m.video == "rage128pro" and m.video_rom:
            card = (Path(paths.join_path(qd, m.video_rom, platform)) if qd
                    else Path(m.video_rom).expanduser())
            if card.is_absolute() or qd:
                ids = rom_pci_id(str(card))
                if not card.is_file():
                    warnings.append(f"The card ROM is missing: {card}")
                elif ids is None or ids[0] != ATI_VENDOR:
                    warnings.append(f"{card.name} is not an ATI card ROM.")
        for s in m.scsi:
            if not s.file:
                continue
            p = Path(s.file).expanduser()
            if not p.is_absolute():
                if machine_dir is None:
                    continue
                p = Path(machine_dir) / p
            if not p.is_file():
                b = scsi_bus(s.bus)
                warnings.append(f"{b.label if b else s.bus} ID {s.id}: {s.file} is not there.")
    return errors, warnings


# ---------------------------------------------------------------- machines

@dataclass
class DeleteResult:
    folder: Path
    removed: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    folder_removed: bool = False

    @property
    def kept_images(self) -> list:
        return [f for f in self.kept if looks_like_disk_image(f)]


class Library:
    """The Machines folder: one subfolder per machine, named after it."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root else paths.machines_dir()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, name: str) -> Path:
        return self.root / name

    def json_path(self, name: str) -> Path:
        return self.folder(name) / "machine.json"

    def names(self) -> list:
        if not self.root.is_dir():
            return []
        return [p.name for p in sorted(self.root.iterdir(), key=lambda x: x.name.lower())
                if p.is_dir() and (p / "machine.json").is_file()]

    def load(self, name: str) -> Machine:
        m = Machine.load(self.json_path(name))
        m.name = name  # the folder is authoritative
        return m

    def load_all(self) -> list:
        out = []
        for n in self.names():
            try:
                out.append(self.load(n))
            except (OSError, ValueError, KeyError, TypeError):
                out.append(Machine(name=n, notes="(unreadable)"))
        return out

    def save(self, m: Machine, old_name: str | None = None) -> Path:
        """Write machine.json; rename the folder if the name changed; make the
        machine's own blank NVRAM file if it has none yet. Nothing is deleted
        or overwritten."""
        if not NAME_RE.match(m.name):
            raise ValueError("illegal machine name")
        self.ensure()
        if old_name and old_name != m.name and self.folder(old_name).is_dir():
            if self.folder(m.name).exists():
                raise FileExistsError(f"A machine called '{m.name}' already exists.")
            old_folder, new_folder = self.folder(old_name), self.folder(m.name)
            moved = [(old_folder, new_folder),
                     (old_folder.resolve(), new_folder.parent.resolve() / new_folder.name)]
            old_folder.rename(new_folder)
            _repoint_images(m, moved)
        folder = self.folder(m.name)
        folder.mkdir(parents=True, exist_ok=True)
        m.save(self.json_path(m.name))
        own = self.own_nvram(m)
        if own and not own.exists():
            write_blank_nvram(own)
        return folder

    def own_nvram(self, m: Machine) -> Path | None:
        """The NVRAM file when it is the machine's own nvram.bin in its folder."""
        p = nvram_path(m, self.folder(m.name))
        if p and p.parent.resolve() == self.folder(m.name).resolve() and p.name == NVRAM_NAME:
            return p
        return None

    def exists(self, name: str) -> bool:
        return self.folder(name).exists()

    def has_record(self, name: str) -> bool:
        return self.json_path(name).is_file()

    def duplicate(self, name: str, new_name: str) -> Machine:
        """Copy the record with a blank NVRAM; disk images are not copied."""
        if self.exists(new_name):
            raise FileExistsError(f"A machine called '{new_name}' already exists.")
        m = self.load(name)
        m.name = new_name
        self.save(m)
        return m

    def folder_contents(self, name: str) -> list:
        d = self.folder(name)
        if not d.is_dir():
            return []
        return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() or p.is_symlink())

    def delete_preview(self, name: str) -> tuple:
        contents = self.folder_contents(name)
        return [f for f in contents if f in OWNED_FILES], [f for f in contents if f not in OWNED_FILES]

    def delete(self, name: str) -> DeleteResult:
        """Remove only ``OWNED_FILES``; the folder stays if anything is left."""
        d = self.folder(name)
        result = DeleteResult(folder=d)
        if not d.is_dir() or d.resolve().parent != self.root.resolve():
            return result
        for f in OWNED_FILES:
            p = d / f
            if p.is_symlink() or p.is_file():
                try:
                    p.unlink()
                except OSError:
                    continue
                result.removed.append(f)
        result.kept = self.folder_contents(name)
        if not result.kept:
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()),
                              key=lambda p: len(p.parts), reverse=True):
                try:
                    sub.rmdir()
                except OSError:
                    pass
            try:
                d.rmdir()
                result.folder_removed = True
            except OSError:
                pass
        return result

    def reset_nvram(self, m: Machine) -> Path | None:
        """Zero the machine's own nvram.bin (never a file chosen elsewhere)."""
        p = self.own_nvram(m)
        if p is None or p.is_symlink():
            return None
        p.write_bytes(b"\0" * NVRAM_SIZE)
        return p


IMAGE_NAME_SUFFIXES = (".img", ".qcow2", ".dsk")


def check_new_image_path(folder: Path | str, name: str, fmt: str) -> tuple:
    """Where a new disk image would go, or why it cannot. Never overwrites."""
    name = (name or "").strip()
    if not name:
        return None, "Give the disk a name."
    if "/" in name or "\\" in name or name in (".", ".."):
        return None, "The name cannot contain slashes."
    if not name.lower().endswith(IMAGE_NAME_SUFFIXES):
        name += ".qcow2" if fmt == "qcow2" else ".img"
    target = Path(folder) / name
    if target.exists() or target.is_symlink():
        return None, f"There is already a file called {name}."
    return target, None


def _repoint_images(m: Machine, moved: list) -> None:
    def fixed(p: str) -> str:
        if not p:
            return p
        for old_folder, new_folder in moved:
            try:
                rel = Path(p).relative_to(old_folder)
            except ValueError:
                continue
            return str(new_folder / rel)
        return p

    for s in m.scsi:
        s.file = fixed(s.file)
    m.nvram = fixed(m.nvram)
