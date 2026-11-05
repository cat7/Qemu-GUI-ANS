"""Where everything is: the folder the program is installed in, the emulator
beside it, and the Machines folder beside it.

There is no configurable QEMU folder and no configurable machine library.
The launcher is a companion to one copy of ``qemu-system-ppc``: it lives in
the same folder as that program, and keeps its machines in a ``Machines``
folder next to itself.

No Tk in here. Platform strings follow ``sys.platform``: ``darwin``,
``win32``, anything else is treated as Linux/POSIX.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

APP_NAME = "Qemu-system-ppc ANS GUI"
HOST_PLATFORM = sys.platform  # "darwin" | "win32" | "linux"

MACHINES_DIR_NAME = "Machines"
SETTINGS_FILE_NAME = "settings.json"

# The folder holding qemu_gui.py, used when running from source.
SOURCE_ROOT = Path(__file__).resolve().parent.parent

# Set only by the tests. There is deliberately no user-facing setting, no
# command-line option and no environment variable for this.
_install_dir_override: Path | None = None


def use_install_dir(folder: Path | str | None) -> None:
    """Testing hook: pretend the program is installed in *folder*."""
    global _install_dir_override
    _install_dir_override = Path(folder).expanduser().resolve() if folder else None


def resolve_install_dir(*, frozen: bool, executable: str, source_root: str) -> Path:
    """The folder a person sees this program in:

    * running from source -- the folder holding ``qemu_gui.py``;
    * frozen inside a macOS application bundle -- walk up out of the ``.app``;
    * frozen as a plain executable (Windows, Linux) -- the folder holding it.
    """
    if not frozen:
        return Path(source_root).resolve()
    exe = Path(executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent.parent
    return exe.parent


def install_dir() -> Path:
    if _install_dir_override is not None:
        return _install_dir_override
    return resolve_install_dir(frozen=bool(getattr(sys, "frozen", False)),
                               executable=sys.executable, source_root=str(SOURCE_ROOT))


def machines_dir() -> Path:
    """Next to the program; never inside an application bundle."""
    return install_dir() / MACHINES_DIR_NAME


def settings_path() -> Path:
    return machines_dir() / SETTINGS_FILE_NAME


def is_windows(platform: str = HOST_PLATFORM) -> bool:
    return platform.startswith("win")


def qemu_binary_name(platform: str = HOST_PLATFORM) -> str:
    return "qemu-system-ppc.exe" if is_windows(platform) else "qemu-system-ppc"


def qemu_img_name(platform: str = HOST_PLATFORM) -> str:
    return "qemu-img.exe" if is_windows(platform) else "qemu-img"


def launcher_name(platform: str = HOST_PLATFORM) -> str:
    return "run.bat" if is_windows(platform) else "run.command"


def qemu_binary(platform: str = HOST_PLATFORM) -> Path:
    return install_dir() / qemu_binary_name(platform)


def qemu_img_binary(platform: str = HOST_PLATFORM) -> Path:
    return install_dir() / qemu_img_name(platform)


def has_qemu(folder: Path | str | None, platform: str = HOST_PLATFORM) -> bool:
    if not folder:
        return False
    return (Path(folder) / qemu_binary_name(platform)).is_file()


def pure_path(p: str, platform: str = HOST_PLATFORM) -> PurePath:
    return PureWindowsPath(p) if is_windows(platform) else PurePosixPath(p)


def join_path(base: str, name: str, platform: str = HOST_PLATFORM) -> str:
    """``base/name`` unless *name* is already absolute; rendered for *platform*."""
    n = pure_path(name, platform)
    if n.is_absolute():
        return str(n)
    return str(pure_path(base, platform) / n)


def browse_start_dir(current: str | None, fallback: Path | str | None = None) -> Path:
    """Folder a file dialog should open in: the folder of whatever is already
    filled in, else *fallback*, else the home folder."""
    for cand in (current, fallback):
        if not cand:
            continue
        p = Path(str(cand)).expanduser()
        d = p if p.is_dir() else p.parent
        if d.is_dir():
            return d
    return Path.home()


# ------------------------------------------------------------ startup check

MISSING_QEMU_MESSAGE = """\
To run this GUI, put it in a folder containing a "{binary}" application.\
"""

UNWRITABLE_MESSAGE = """The "{name}" folder next to this program could not be created: {reason}"""


def startup_problem(platform: str = HOST_PLATFORM) -> str | None:
    """A reason the program cannot run where it is, or None."""
    folder = install_dir()
    if not has_qemu(folder, platform):
        return MISSING_QEMU_MESSAGE.format(binary=qemu_binary_name(platform))
    machines = machines_dir()
    try:
        machines.mkdir(parents=True, exist_ok=True)
        probe = machines / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return UNWRITABLE_MESSAGE.format(name=MACHINES_DIR_NAME, reason=e)
    return None


# ---------------------------------------------------------------- settings

@dataclass
class Settings:
    """The only thing remembered between runs: the selected machine."""

    last_machine: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        v = data.get("last_machine") if isinstance(data, dict) else None
        if isinstance(v, str):
            s.last_machine = v
        return s

    def save(self, path: Path | None = None) -> None:
        path = Path(path or settings_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
