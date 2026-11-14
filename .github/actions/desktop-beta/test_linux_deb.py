"""Synthetic native archive checks; never executes or installs a package."""

import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

spec = importlib.util.spec_from_file_location("native_deb", Path(__file__).with_name("verify-linux-deb.py"))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def archive(machine=183, launcher=True, extra=None):
    header = bytearray(b"\x7fELF\x02\x01" + bytes(14))
    header[18:20] = machine.to_bytes(2, "little")
    files = {"./opt/jashva/lib/runtime/lib/server/libjvm.so": bytes(header),
             "./opt/jashva/lib/runtime/lib/libjava.so": bytes(header)}
    if launcher:
        files["./opt/jashva/bin/Jashva"] = bytes(header)
    if extra:
        files.update(extra)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    output.seek(0)
    return output


class NativeDebTest(unittest.TestCase):
    def test_arm64_launcher_and_bundled_runtime_are_required(self):
        result = native.inspect_payload(archive())
        self.assertEqual(result["elf_machine"], 183)
        self.assertTrue(result["launcher_verified"])
        self.assertFalse(result["installed"])
        with self.assertRaises(ValueError):
            native.inspect_payload(archive(launcher=False))

    def test_x64_substitution_or_mixed_native_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            native.inspect_payload(archive(machine=62))
        header = bytearray(b"\x7fELF\x02\x01" + bytes(14))
        header[18:20] = (62).to_bytes(2, "little")
        with self.assertRaises(ValueError):
            native.inspect_payload(archive(extra={"./opt/jashva/lib/foreign.so": bytes(header)}))

    def test_unsafe_archive_paths_are_rejected_without_extraction(self):
        with self.assertRaises(ValueError):
            native.inspect_payload(archive(extra={"../../outside": b"ignored"}))


if __name__ == "__main__":
    unittest.main()
