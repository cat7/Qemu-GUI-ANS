# Qemu-system-ppc ANS GUI

A launcher for the Apple Network Server 700/200 in `qemu-system-ppc`
(`-M ans700`): one 200 MHz PowerPC 604e, the Network Server ROM, and AIX
4.1.5 (ROM 1.1.22) or Mac OS (ROM 2.0, with an ATI Rage 128 Pro). Its main window carries a copy of the
machine's front panel: the four-line, twenty-character LCD, live, with the
keyswitch and the reset button beside it.

The program must sit in the same folder as `qemu-system-ppc` (and
`qemu-img`). Machines are kept in a `Machines` folder next to it, one
subfolder per machine with its `machine.json`, its `nvram.bin`, a generated
`run.command` (`run.bat` on Windows) and the last run's log. The launchers
are written from `machine.json` and should not be edited by hand.

## Which emulator

The `ans700` machine comes from the `ANS-rom` branch of
https://github.com/cat7/ANS-rom. Build `qemu-system-ppc` from that branch
and put this launcher next to it. Upstream QEMU has no Network Server.

## The front panel

The real machine shows POST progress, Open Firmware messages and AIX's
three-digit start-up codes on its LCD. QEMU writes each line that changes
to the machine's `lcd=` character device as `lcdN: <20 characters>`; the
generated command line makes that a local socket server
(`-chardev socket,id=lcd,host=127.0.0.1,port=…,server=on,wait=off`), and
the window connects to it, reconnecting whenever the connection drops.
When a reader connects, QEMU sends all four lines, so the panel shows the
current text however late it connects: also for a machine started in
Terminal or before the program was opened. The LCD is dark while the
machine is off.

Beside it:

- the state (Off, Running, Paused, …), asked over QEMU's control connection
  (QMP, `-qmp tcp:127.0.0.1:…`);
- the keyswitch: Service, Unlocked (QEMU's `normal`) and Locked. Turning it
  while the machine runs turns the key in the machine
  (`qom-set /machine keyswitch`); either way the machine keeps the new
  position for its next start. A key turned by other means shows here too;
- Reset, the front reset button (`system_reset`).

Each machine has its own pair of local ports (QMP from 47000, LCD from
48000, the next free one if taken), so the command shown is the one that
runs.

## What it sets up

- ROM (`-bios`): the Network Server ROM, a 4 MB image, chosen by the
  person; a new machine has none. 1.1.22 runs AIX; 2.0 runs Mac OS.
- Memory (`-m`): 16 to 512 MB in pairs of DIMMs (16, 32, 48, 64, 96, 128,
  192, 256, 384, 512 MB, and any other size that splits into up to four
  pairs of DIMMs of at most 64 MB).
- Keyswitch at power-on (`keyswitch=`), power supplies (1 or 2,
  `power-supplies=`).
- NVRAM (`nvram=`): each machine's own 8 KB `nvram.bin`, blank when the
  machine is first saved; "Reset NVRAM" clears it. With a blank NVRAM Open
  Firmware's console is the screen and the ADB keyboard; with the key at
  Service it looks for an AIX Install CD (or diagnostics floppy) to start
  from.
- Drives (`scsi-hd` / `scsi-cd`, CDs read-only), IDs 0 to 6 on each of
  the three buses (ID 7 is the controller):
  - internal bus 0, the first 53C825A (`scsi.1`): IDs 0-3 are Open
    Firmware's `disk0`-`disk3`;
  - internal bus 1, the second 53C825A (`scsi.2`): IDs 4-6 are `disk4`-`disk6`;
  - the external 53C94 (`scsi.0`).
  The AIX install that is known to work: Install CD at internal bus 0 ID 0,
  disk at ID 2, key at Service, blank NVRAM. Mac OS 8.5 starts from its CD
  on internal bus 0 or 1 (ROM 2.0, Rage 128 Pro). New blank disks are made with
  `qemu-img`.
- Serial ports A and B (the ESCC): a telnet server on a local port each
  (4555 and 4556 by default; `telnet 127.0.0.1 PORT`), or off. AIX's
  serial console is on port B; Open Firmware's on port A once its console
  is set to `ttya`.
- Video card: the built-in Cirrus 54M30 (AIX), or an ATI Rage 128 Pro in
  PCI slot D of the second bridge in its place (Mac OS; `onboard-video=off`),
  with the card's Mac ROM chosen by the person. The card is given the PCI
  device id from its ROM (`device-id=`), as Open Firmware requires.
- Screen: shown with cocoa or sdl (gtk off a Mac), or `none` for no window at all (the front panel and the serial
  ports still work). Option/Command swap with cocoa.
- Network: the built-in MACE Ethernet (`-nic`) with slirp (port forwards
  from 127.0.0.1; 2323 -> 23 is telnet into AIX), vmnet (macOS) or tap
  (Windows), or none. The Ethernet address is editable per machine.
- Extra QEMU arguments, added at the end.

The main window shows the command line, copies it as one line, starts the
machine (its output shown from `last-run.log`) and stops it. "Start in
Terminal" runs `run.command` in Terminal instead; vmnet and host ports
below 1024 always go that way, since they need `sudo`.

## Requirements

Python 3.11+ with tkinter, standard library only. PyInstaller to build a
bundle.

## Run from source

    python qemu_gui.py

(from a folder holding `qemu-system-ppc`, or put the sources there).

## Build on macOS

With a universal2 python.org framework build of Python:

    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
        -m PyInstaller --noconfirm QemuGUI.spec

Result: `dist/Qemu-system-ppc ANS GUI.app`; put it in the folder that
holds `qemu-system-ppc`. For a build that runs only on this machine's
architecture, prefix the command with `QEMUGUI_TARGET_ARCH=arm64` (or
`x86_64`).

## Build on Windows

The same spec, with a Python that has tkinter:

    python -m PyInstaller --noconfirm QemuGUI.spec

Result: the single `dist\Qemu-system-ppc ANS GUI.exe`; put it next to
`qemu-system-ppc.exe`.

## Tests

    python -m unittest discover -s tests      # or: python -m pytest tests

The window tests run when a display is there. `tests/test_live.py` starts
the real emulator headless with the generated command line and reads the
real LCD through the program's own LCD code; it is skipped unless asked
for:

    QEMUGUI_LIVE=1 QEMUGUI_QEMU_DIR=/path/to/qemu QEMUGUI_ROM=/path/to/ans700.rom \
        python -m unittest tests.test_live -v
