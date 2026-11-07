"""The generated command line (headless, no Tk)."""

from __future__ import annotations

import shlex
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import command, model  # noqa: E402
from qemugui.model import Machine, ScsiDrive, SerialPort, HostFwd, Network  # noqa: E402

QD = "/Applications/ANS"
MD = "/Applications/ANS/Machines/AIX"


def aix_machine() -> Machine:
    """The machine of the hand-written ANS700.command: key normal, 512 MB,
    serial A off, B on telnet 4556, the AIX disk at internal bus 0 ID 2,
    slirp with telnet forwarded from 2323."""
    m = model.new_machine("AIX")
    m.rom = "ans700.rom"
    m.display = "cocoa"
    m.serial = [SerialPort("none", 4555), SerialPort("telnet", 4556)]
    m.scsi = [ScsiDrive("scsi.1", 2, "disk", "/disks/aix-cde.img", "raw")]
    return m


def opts(argv: list) -> list:
    return command.group_options(argv)


class Argv(unittest.TestCase):
    def test_the_working_line(self):
        argv = command.build_argv(aix_machine(), QD, MD, "darwin", qmp=47001, lcd=48001)
        self.assertEqual(argv[0], f"{QD}/qemu-system-ppc")
        self.assertEqual(opts(argv), [
            ["-M", f"ans700,keyswitch=normal,nvram={MD}/nvram.bin,lcd=lcd"],
            ["-m", "512"],
            ["-bios", f"{QD}/ans700.rom"],
            ["-chardev", "socket,id=lcd,host=127.0.0.1,port=48001,server=on,wait=off"],
            ["-serial", "null"],
            ["-chardev", "socket,id=serialb,host=127.0.0.1,port=4556,server=on,wait=off,telnet=on"],
            ["-serial", "chardev:serialb"],
            ["-display", "cocoa"],
            ["-nic", "user,mac=08:00:07:4e:53:01,hostfwd=tcp:127.0.0.1:2323-:23"],
            ["-drive", "if=none,id=hd-s1id2,file=/disks/aix-cde.img,format=raw"],
            ["-device", "scsi-hd,drive=hd-s1id2,bus=scsi.1,scsi-id=2"],
            ["-qmp", "tcp:127.0.0.1:47001,server=on,wait=off"],
        ])

    def test_install_cd_with_the_key_at_service(self):
        m = aix_machine()
        m.keyswitch = "service"
        m.scsi.append(ScsiDrive("scsi.1", 0, "cdrom", "/iso/AIX 4.1.5, Install.iso"))
        argv = command.build_argv(m, QD, MD, "darwin", qmp=1, lcd=2)
        self.assertIn("keyswitch=service", argv[argv.index("-M") + 1])
        drives = [g for g in opts(argv) if g[0] in ("-drive", "-device")]
        # sorted by bus, then ID: the CD (ID 0) first
        self.assertEqual(drives[0], ["-drive", "if=none,id=cd-s1id0,file=/iso/AIX 4.1.5,, Install.iso,"
                                               "format=raw,media=cdrom,readonly=on"])
        self.assertEqual(drives[1], ["-device", "scsi-cd,drive=cd-s1id0,bus=scsi.1,scsi-id=0"])

    def test_three_buses(self):
        m = aix_machine()
        m.scsi = [ScsiDrive("scsi.0", 3, "disk", "/d/ext.img"),
                  ScsiDrive("scsi.2", 4, "disk", "/d/d4.qcow2", "qcow2"),
                  ScsiDrive("scsi.1", 1, "disk", "/d/d1.img")]
        argv = command.build_argv(m, QD, MD, "darwin", qmp=1, lcd=2)
        devs = [argv[i + 1] for i, t in enumerate(argv) if t == "-device"]
        self.assertEqual(devs, ["scsi-hd,drive=hd-s1id1,bus=scsi.1,scsi-id=1",
                                "scsi-hd,drive=hd-s2id4,bus=scsi.2,scsi-id=4",
                                "scsi-hd,drive=hd-s0id3,bus=scsi.0,scsi-id=3"])
        self.assertIn("if=none,id=hd-s2id4,file=/d/d4.qcow2,format=qcow2", argv)

    def test_a_drive_without_a_file_is_left_out(self):
        m = aix_machine()
        m.scsi = [ScsiDrive("scsi.1", 0, "cdrom", "")]
        self.assertNotIn("-device", command.build_argv(m, QD, MD, "darwin"))

    def test_relative_image_and_nvram_paths_resolve_against_the_machine(self):
        m = aix_machine()
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", "aix.img")]
        argv = command.build_argv(m, QD, MD, "darwin", qmp=1, lcd=2)
        self.assertIn(f"if=none,id=hd-s1id2,file={MD}/aix.img,format=raw", argv)

    def test_qcow2_detected_from_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "disk.img"
            img.write_bytes(b"QFI\xfb" + b"\0" * 60)
            m = aix_machine()
            m.scsi = [ScsiDrive("scsi.1", 2, "disk", str(img), "raw")]
            argv = command.build_argv(m, QD, td, "darwin", qmp=1, lcd=2)
            self.assertIn(f"if=none,id=hd-s1id2,file={img},format=qcow2", argv)

    def test_commas_in_paths(self):
        m = aix_machine()
        m.nvram = "/n,v/nvram.bin"
        m.rom = "/r,om/ans.rom"
        argv = command.build_argv(m, QD, MD, "darwin", qmp=1, lcd=2)
        self.assertIn("nvram=/n,,v/nvram.bin", argv[argv.index("-M") + 1])
        self.assertEqual(argv[argv.index("-bios") + 1], "/r,om/ans.rom")   # -bios takes it raw

    def test_no_nvram_file(self):
        m = aix_machine()
        m.nvram = ""
        self.assertEqual(command.build_argv(m, QD, MD, "darwin")[2], "ans700,keyswitch=normal,lcd=lcd")

    def test_two_power_supplies(self):
        m = aix_machine()
        m.power_supplies = 2
        self.assertIn("power-supplies=2", command.build_argv(m, QD, MD, "darwin")[2])

    def test_rage128_pro_takes_its_rom_pci_id(self):
        with tempfile.TemporaryDirectory() as d:
            rom = bytearray(0x200)
            rom[0:2] = b"\x55\xaa"
            rom[0x18:0x1a] = (0x100).to_bytes(2, "little")
            rom[0x100:0x108] = b"PCIR" + (0x1002).to_bytes(2, "little") + (0x5046).to_bytes(2, "little")
            Path(d, "r128.rom").write_bytes(bytes(rom))
            m = aix_machine()
            m.video, m.video_rom = "rage128pro", "r128.rom"
            argv = command.build_argv(m, d, MD, "darwin", qmp=47001, lcd=48001)
        self.assertIn("onboard-video=off", argv[argv.index("-M") + 1])
        dev = argv[argv.index("-device", argv.index("-display")) + 1]
        self.assertEqual(dev, f"ati-rage128-pro,bus=bandit@f4000000,addr=0xd,"
                              f"romfile={d}/r128.rom,device-id=0x5046")
        self.assertEqual(model.start_blockers(m), [])

    def test_rage128_pro_without_rom_cannot_start(self):
        m = aix_machine()
        m.video = "rage128pro"
        self.assertIn("No ROM chosen for the Rage 128 Pro.", model.start_blockers(m))
        self.assertEqual(model.Machine.from_json(m.to_json()).video, "rage128pro")

    def test_built_in_video_by_default(self):
        argv = command.build_argv(aix_machine(), QD, MD, "darwin", qmp=47001, lcd=48001)
        self.assertNotIn("onboard-video=off", argv[argv.index("-M") + 1])
        self.assertFalse(any("ati-rage128-pro" in t for t in argv))

    def test_no_rom_leaves_bios_out(self):
        m = aix_machine()
        m.rom = ""
        self.assertNotIn("-bios", command.build_argv(m, QD, MD, "darwin"))

    def test_serial_ports(self):
        m = aix_machine()
        m.serial = [SerialPort("telnet", 5000), SerialPort("telnet", 5001)]
        self.assertEqual(command.serial_args(m), [
            "-chardev", "socket,id=seriala,host=127.0.0.1,port=5000,server=on,wait=off,telnet=on",
            "-serial", "chardev:seriala",
            "-chardev", "socket,id=serialb,host=127.0.0.1,port=5001,server=on,wait=off,telnet=on",
            "-serial", "chardev:serialb"])
        m.serial = [SerialPort("none", 1), SerialPort("none", 2)]
        self.assertEqual(command.serial_args(m), ["-serial", "null", "-serial", "null"])

    def test_headless_and_swapped_keys(self):
        m = aix_machine()
        m.display = "none"
        self.assertEqual(command.display_option(m), "none")
        m.display = "cocoa"
        m.swap_opt_cmd = True
        self.assertEqual(command.display_option(m), "cocoa,swap-opt-cmd=on")
        m.display = "sdl"
        self.assertEqual(command.display_option(m), "sdl")

    def test_network_modes(self):
        mac = model.DEFAULT_MAC
        self.assertEqual(command.nic_option(Network("none")), "none")
        self.assertEqual(command.nic_option(Network("user", mac, "", [])), f"user,mac={mac}")
        self.assertEqual(command.nic_option(Network("user", mac, "", [HostFwd("udp", "6000", "60"),
                                                                       HostFwd()])),
                         f"user,mac={mac},hostfwd=udp:127.0.0.1:6000-:60")
        self.assertEqual(command.nic_option(Network("vmnet-bridged", mac, "en0")),
                         f"vmnet-bridged,ifname=en0,mac={mac}")
        self.assertEqual(command.nic_option(Network("vmnet-shared", mac)), f"vmnet-shared,mac={mac}")
        self.assertEqual(command.nic_option(Network("tap", mac, "TAP 1")), f"tap,ifname=TAP 1,mac={mac}")
        with self.assertRaises(ValueError):
            command.nic_option(Network("carrier-pigeon"))

    def test_extra_arguments_go_last(self):
        m = aix_machine()
        m.extra_args = '-monitor stdio -d "unimp,guest_errors"'
        argv = command.build_argv(m, QD, MD, "darwin")
        self.assertEqual(argv[-4:], ["-monitor", "stdio", "-d", "unimp,guest_errors"])

    def test_windows_paths(self):
        m = aix_machine()
        m.rom = r"C:\roms\ans700.rom"
        argv = command.build_argv(m, r"C:\qemu-ans", r"C:\qemu-ans\Machines\AIX", "win32",
                                  qmp=1, lcd=2)
        self.assertEqual(argv[0], r"C:\qemu-ans\qemu-system-ppc.exe")
        self.assertIn(r"nvram=C:\qemu-ans\Machines\AIX\nvram.bin", argv[2])
        self.assertEqual(argv[argv.index("-bios") + 1], r"C:\roms\ans700.rom")

    def test_ports_in(self):
        argv = command.build_argv(aix_machine(), QD, MD, "darwin", qmp=47123, lcd=48123)
        self.assertEqual(command.ports_in(argv), (47123, 48123))


class Ports(unittest.TestCase):
    def test_each_machine_its_own_ports(self):
        a, b = model.new_machine("a"), model.new_machine("b")
        for m in (a, b):
            self.assertTrue(command.QMP_PORT_BASE <= command.qmp_port(m) < command.QMP_PORT_BASE + 1000)
            self.assertTrue(command.LCD_PORT_BASE <= command.lcd_port(m) < command.LCD_PORT_BASE + 1000)
        self.assertNotEqual(command.qmp_port(a), command.qmp_port(b))
        self.assertEqual(command.lcd_port(a) - command.LCD_PORT_BASE,
                         command.qmp_port(a) - command.QMP_PORT_BASE)

    def test_a_taken_port_is_skipped(self):
        import socket
        m = model.new_machine("taken")
        with socket.socket() as s:
            s.bind(("127.0.0.1", command.qmp_port(m)))
            s.listen(1)
            qmp, lcd = command.free_ports(m)
        self.assertNotEqual(qmp, command.qmp_port(m))
        self.assertEqual(lcd, command.lcd_port(m))


class Rendering(unittest.TestCase):
    def test_shell_launcher_runs_the_same_argv(self):
        argv = command.build_argv(aix_machine(), QD, MD, "darwin", qmp=1, lcd=2)
        text = command.render_shell(argv)
        self.assertTrue(text.startswith("#!/bin/bash\n# Written by"))
        self.assertIn('cd "$(dirname "$0")"', text)
        body = text.split('cd "$(dirname "$0")"\n', 1)[1]
        self.assertEqual(shlex.split(body.replace("\\\n", " ")), argv)

    def test_sudo_for_vmnet(self):
        m = aix_machine()
        m.network = Network("vmnet-shared")
        self.assertTrue(command.needs_sudo(m, "darwin"))
        self.assertFalse(command.needs_sudo(m, "win32"))
        text = command.launcher_text(m, QD, MD, "darwin")
        self.assertIn("sudo -v", text)
        self.assertIn("sudo " + shlex.quote(f"{QD}/qemu-system-ppc"), text)
        self.assertIn("chown", text)
        self.assertIn("nvram.bin", text)

    def test_low_forwarded_port_needs_sudo(self):
        m = aix_machine()
        m.network.hostfwd = [HostFwd("tcp", "23", "23")]
        self.assertTrue(command.needs_sudo(m, "darwin"))

    def test_bat(self):
        m = aix_machine()
        argv = command.build_argv(m, r"C:\q", r"C:\q\Machines\AIX", "win32", qmp=1, lcd=2)
        text = command.render_bat(argv, "AIX & co")
        self.assertTrue(text.startswith("@echo off\r\n"))
        self.assertIn("title AIX  co\r\n", text)
        self.assertIn('"ans700,keyswitch=normal,nvram=C:\\q\\Machines\\AIX\\nvram.bin,lcd=lcd" ^', text)
        self.assertTrue(text.endswith("if errorlevel 1 pause\r\n"))

    def test_one_line(self):
        argv = command.build_argv(aix_machine(), QD, MD, "darwin", qmp=1, lcd=2)
        line = command.one_line(argv, MD, "darwin")
        self.assertTrue(line.startswith(f"cd {MD} && {QD}/qemu-system-ppc -M "))
        self.assertEqual(shlex.split(line.split(" && ", 1)[1]), argv)

    def test_write_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            path, argv = command.write_launcher(aix_machine(), QD, td, "darwin", qmp=5, lcd=6)
            self.assertEqual(path.name, "run.command")
            self.assertTrue(path.stat().st_mode & 0o111)
            self.assertEqual(command.ports_in(argv), (5, 6))
            self.assertIn("port=6,server=on", path.read_text())


if __name__ == "__main__":
    unittest.main()
