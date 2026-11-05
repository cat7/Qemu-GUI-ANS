"""The machine record, its checks, and the Machines folder."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from qemugui import model  # noqa: E402
from qemugui.model import Library, Machine, ScsiDrive, SerialPort, HostFwd, Network  # noqa: E402


def hammerhead_fits(mb: int) -> bool:
    """QEMU's Hammerhead inventory in bytes (hw/misc/hammerhead.c)."""
    mib = 1 << 20
    rest = mb * mib
    for _pair in range(4):
        if rest < 2 * mib:
            break
        half = rest // 2
        dimm = 64 * mib
        while dimm > half:
            dimm >>= 1
        rest -= 2 * dimm
    return rest == 0


class Memory(unittest.TestCase):
    def test_same_answer_as_the_board(self):
        for mb in range(model.RAM_MIN, model.RAM_MAX + 1):
            self.assertEqual(model.ram_fits(mb), hammerhead_fits(mb), mb)

    def test_choices_all_fit(self):
        for mb in model.RAM_CHOICES:
            self.assertTrue(model.ram_fits(mb), mb)

    def test_limits(self):
        self.assertFalse(model.ram_fits(513))
        self.assertFalse(model.ram_fits(1024))
        self.assertFalse(model.ram_fits(8))
        self.assertFalse(model.ram_fits(17))     # 8+8 leaves 1 MB over


class Record(unittest.TestCase):
    def test_new_machine_names_no_disk_and_no_rom(self):
        m = model.new_machine("x")
        self.assertEqual((m.rom, m.scsi), ("", []))
        self.assertEqual(m.nvram, "nvram.bin")
        self.assertEqual(m.ram_mb, 512)
        self.assertEqual(m.keyswitch, "normal")
        self.assertEqual([(s.mode, s.port) for s in m.serial], [("telnet", 4555), ("telnet", 4556)])
        self.assertEqual([r.to_dict() for r in m.network.hostfwd],
                         [{"proto": "tcp", "host_port": "2323", "guest_port": "23"}])

    def test_json_round_trip(self):
        m = model.new_machine("AIX 4.1.5")
        m.rom = "/roms/ans.rom"
        m.keyswitch = "service"
        m.power_supplies = 2
        m.scsi = [ScsiDrive("scsi.2", 5, "disk", "/d/b.img"), ScsiDrive("scsi.1", 0, "cdrom", "/i/a.iso")]
        m.serial = [SerialPort("none", 4555), SerialPort("telnet", 6000)]
        m.network = Network("vmnet-bridged", "08:00:07:00:00:01", "en1", [HostFwd("tcp", "80", "80")])
        d = json.loads(m.to_json())
        self.assertEqual(d["machine"], "ans700")
        self.assertEqual([s["bus"] for s in d["scsi"]], ["scsi.1", "scsi.2"])   # sorted by bay
        back = Machine.from_json(m.to_json())
        self.assertEqual(back.to_dict(), m.to_dict())

    def test_missing_and_bad_fields_fall_back(self):
        m = Machine.from_dict({"name": "old", "ram_mb": "lots", "serial": [{"mode": "none"}],
                               "scsi": [{"id": "x"}, None, {"bus": "scsi.2", "id": 4}]})
        self.assertEqual(m.ram_mb, model.RAM_DEFAULT)
        self.assertEqual((m.serial[0].mode, m.serial[0].port), ("none", 4555))
        self.assertEqual((m.serial[1].mode, m.serial[1].port), ("telnet", 4556))
        self.assertEqual([(s.bus, s.id) for s in m.scsi], [("scsi.2", 4)])

    def test_of_aliases(self):
        self.assertEqual([model.of_alias("scsi.1", i) for i in range(7)],
                         ["disk0", "disk1", "disk2", "disk3", "", "", ""])
        self.assertEqual([model.of_alias("scsi.2", i) for i in range(7)],
                         ["", "", "", "", "disk4", "disk5", "disk6"])
        self.assertEqual(model.of_alias("scsi.0", 2), "")

    def test_keyswitch_labels(self):
        for k in model.KEYSWITCH:
            self.assertEqual(model.keyswitch_by_label(model.KEYSWITCH_LABELS[k]), k)


class Validate(unittest.TestCase):
    def check(self, m, **kw):
        return model.validate(m, None, platform="darwin", check_files=False, **kw)

    def test_a_new_machine_is_fine(self):
        self.assertEqual(self.check(model.new_machine("ok")), ([], []))

    def test_errors(self):
        cases = [
            (dict(name=""), "no name"),
            (dict(name="a/b"), "will not work"),
            (dict(ram_mb=1024), "between 16 and 512"),
            (dict(ram_mb=17), "pairs of DIMMs"),
            (dict(keyswitch="sideways"), "keyswitch"),
            (dict(power_supplies=3), "power supplies"),
            (dict(display="vnc"), "display"),
        ]
        for fields, text in cases:
            m = model.new_machine("ok")
            for k, v in fields.items():
                setattr(m, k, v)
            errors, _ = self.check(m)
            self.assertTrue(any(text in e for e in errors), (fields, errors))

    def test_scsi_checks(self):
        m = model.new_machine("ok")
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", "/a"), ScsiDrive("scsi.1", 2, "cdrom", "/b"),
                  ScsiDrive("scsi.2", 7, "disk", "/c"), ScsiDrive("scsi.9", 1, "disk", "/d"),
                  ScsiDrive("scsi.0", 3, "tape", "/e")]
        errors, _ = self.check(m)
        self.assertTrue(any("both set to ID 2" in e for e in errors), errors)
        self.assertTrue(any("ID 7 is the controller" in e for e in errors), errors)
        self.assertTrue(any("'scsi.9'" in e for e in errors), errors)
        self.assertTrue(any("no drive type" in e for e in errors), errors)
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", "/a"), ScsiDrive("scsi.2", 2, "disk", "/b")]
        self.assertEqual(self.check(m)[0], [])          # same ID on another bus is fine

    def test_serial_checks(self):
        m = model.new_machine("ok")
        m.serial = [SerialPort("telnet", 4556), SerialPort("telnet", 4556)]
        self.assertTrue(any("same port" in e for e in self.check(m)[0]))
        m.serial = [SerialPort("telnet", 0), SerialPort("none", 0)]
        self.assertTrue(any("1 to 65535" in e for e in self.check(m)[0]))
        m.serial = [SerialPort("telnet", 2323), SerialPort("none", 0)]
        self.assertTrue(any("both forwarded and a serial port" in e for e in self.check(m)[0]))

    def test_network_checks(self):
        m = model.new_machine("ok")
        m.network = Network("user", "nonsense")
        self.assertTrue(any("Ethernet address" in e for e in self.check(m)[0]))
        m.network = Network("vmnet-bridged", model.DEFAULT_MAC, "")
        self.assertTrue(any("No interface" in e for e in self.check(m)[0]))
        m.network = Network("tap", model.DEFAULT_MAC, "t0")
        self.assertTrue(any("only works on Windows" in w for w in self.check(m)[1]))
        m.network = Network("user", model.DEFAULT_MAC, "", [HostFwd("tcp", "99999", "23")])
        self.assertTrue(any("Port forwarding" in e for e in self.check(m)[0]))

    def test_rom_file_checks(self):
        with tempfile.TemporaryDirectory() as td:
            m = model.new_machine("ok")
            m.rom = str(Path(td) / "ans.rom")
            _, w = model.validate(m, td, "darwin")
            self.assertTrue(any("missing" in x for x in w), w)
            Path(m.rom).write_bytes(b"\0" * 1024)
            _, w = model.validate(m, td, "darwin")
            self.assertTrue(any("not a 4 MB ROM" in x for x in w), w)
            Path(m.rom).write_bytes(b"\0" * model.ROM_SIZE)
            self.assertEqual(model.validate(m, td, "darwin"), ([], []))
            self.assertEqual(model.start_blockers(m, td), [])
            m.rom = ""
            self.assertEqual(model.start_blockers(m, td), ["No ROM chosen."])

    def test_missing_disk_is_a_warning(self):
        m = model.new_machine("ok")
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", "/nowhere/aix.img")]
        errors, warnings = model.validate(m, None, "darwin")
        self.assertEqual(errors, [])
        self.assertTrue(any("ID 2: /nowhere/aix.img is not there" in w for w in warnings), warnings)


class Folder(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.lib = Library(Path(self.td.name) / "Machines")

    def tearDown(self):
        self.td.cleanup()

    def test_save_makes_a_blank_nvram(self):
        folder = self.lib.save(model.new_machine("AIX"))
        nv = folder / "nvram.bin"
        self.assertEqual(nv.read_bytes(), b"\0" * 8192)
        self.assertEqual(self.lib.names(), ["AIX"])

    def test_save_keeps_an_existing_nvram(self):
        m = model.new_machine("AIX")
        folder = self.lib.save(m)
        (folder / "nvram.bin").write_bytes(b"\1" * 8192)
        self.lib.save(m)
        self.assertEqual((folder / "nvram.bin").read_bytes(), b"\1" * 8192)

    def test_reset_nvram(self):
        m = model.new_machine("AIX")
        folder = self.lib.save(m)
        (folder / "nvram.bin").write_bytes(b"\1" * 8192)
        self.assertEqual(self.lib.reset_nvram(m), folder / "nvram.bin")
        self.assertEqual((folder / "nvram.bin").read_bytes(), b"\0" * 8192)

    def test_reset_never_touches_a_file_from_elsewhere(self):
        other = Path(self.td.name) / "elsewhere.bin"
        other.write_bytes(b"\1" * 10)
        m = model.new_machine("AIX")
        m.nvram = str(other)
        self.lib.save(m)
        self.assertIsNone(self.lib.reset_nvram(m))
        self.assertEqual(other.read_bytes(), b"\1" * 10)

    def test_delete_keeps_disk_images(self):
        m = model.new_machine("AIX")
        folder = self.lib.save(m)
        (folder / "aix.img").write_bytes(b"disk")
        (folder / "last-run.log").write_text("log")
        res = self.lib.delete("AIX")
        self.assertEqual(sorted(res.removed), ["last-run.log", "machine.json", "nvram.bin"])
        self.assertEqual(res.kept_images, ["aix.img"])
        self.assertTrue((folder / "aix.img").is_file())
        self.assertFalse(res.folder_removed)

    def test_delete_an_empty_folder(self):
        self.lib.save(model.new_machine("gone"))
        self.assertTrue(self.lib.delete("gone").folder_removed)
        self.assertEqual(self.lib.names(), [])

    def test_rename_repoints_images_inside_the_folder(self):
        m = model.new_machine("old")
        folder = self.lib.save(m)
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", str(folder / "aix.img"))]
        self.lib.save(m)
        m.name = "new"
        self.lib.save(m, old_name="old")
        self.assertEqual(m.scsi[0].file, str(self.lib.folder("new") / "aix.img"))
        self.assertEqual(self.lib.names(), ["new"])

    def test_duplicate(self):
        m = model.new_machine("a")
        m.scsi = [ScsiDrive("scsi.1", 2, "disk", "/d/aix.img")]
        self.lib.save(m)
        (self.lib.folder("a") / "nvram.bin").write_bytes(b"\1" * 8192)
        copy = self.lib.duplicate("a", "b")
        self.assertEqual(copy.scsi[0].file, "/d/aix.img")
        self.assertEqual((self.lib.folder("b") / "nvram.bin").read_bytes(), b"\0" * 8192)
        with self.assertRaises(FileExistsError):
            self.lib.duplicate("a", "b")

    def test_new_image_path_never_overwrites(self):
        folder = Path(self.td.name)
        target, why = model.check_new_image_path(folder, "aix", "raw")
        self.assertEqual((target, why), (folder / "aix.img", None))
        target.write_bytes(b"")
        self.assertIsNone(model.check_new_image_path(folder, "aix", "raw")[0])
        self.assertEqual(model.check_new_image_path(folder, "a/b", "raw")[1],
                         "The name cannot contain slashes.")


if __name__ == "__main__":
    unittest.main()
