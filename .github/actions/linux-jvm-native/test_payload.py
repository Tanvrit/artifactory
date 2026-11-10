"""Synthetic uncompressed tar payloads only: no apt, native loader or network."""
import importlib.util
import io
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("linux_jvm_payload", Path(__file__).with_name("payload.py"))
PAYLOAD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PAYLOAD
SPEC.loader.exec_module(PAYLOAD)

PREFIX = "./usr/lib/aarch64-linux-gnu/"
CANARY = "private-token-path-never-print"


def entry(name, data=b"", kind=tarfile.REGTYPE, target="", mode=0o644):
    member = tarfile.TarInfo(name)
    member.type, member.linkname, member.mode = kind, target, mode
    member.size = len(data) if kind == tarfile.REGTYPE else 0
    return member, data


def archive(*entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for member, data in entries:
            tar.addfile(member, io.BytesIO(data) if member.isreg() else None)
    return output.getvalue()


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / "libraries"
        self.destination.mkdir(mode=0o700)
        self.seen = set()

    def extract(self, raw):
        return PAYLOAD.extract_tar_stream(io.BytesIO(raw), self.destination, self.seen)

    def refused(self, raw, destination=None):
        with self.assertRaises(PAYLOAD.PayloadError) as caught:
            if destination is None:
                self.extract(raw)
            else:
                PAYLOAD.extract_tar_stream(io.BytesIO(raw), destination, set())
        self.assertNotIn(CANARY, str(caught.exception))

    def test_real_debian_prefixes_directories_and_soname_link_are_supported(self):
        raw = archive(
            entry(".", kind=tarfile.DIRTYPE), entry("./usr/", kind=tarfile.DIRTYPE),
            entry("./usr/lib/", kind=tarfile.DIRTYPE),
            entry(PREFIX + "libGL.so.1.7.0", b"synthetic-library"),
            entry(PREFIX + "libGL.so.1", kind=tarfile.SYMTYPE, target="libGL.so.1.7.0"),
        )
        self.assertEqual(self.extract(raw), 1)
        PAYLOAD.finish_library_links(self.destination)
        self.assertEqual((self.destination / "libGL.so.1").read_bytes(), b"synthetic-library")
        self.assertTrue((self.destination / "libGL.so.1").is_symlink())
        self.assertEqual((self.destination / "libGL.so.1").resolve(), self.destination.resolve() / "libGL.so.1.7.0")
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"libGL.so.1", "libGL.so.1.7.0"})

    def test_both_native_library_roots_flatten_without_extracting_docs_or_scripts(self):
        self.assertEqual(self.extract(archive(
            entry("lib/aarch64-linux-gnu/libEGL.so.1.1.0", b"egl"),
            entry("usr/share/doc/libegl1/copyright", b"licensed data"),
            entry("usr/share/doc/libegl1/changelog", kind=tarfile.SYMTYPE, target="../libglvnd0/changelog"),
            entry("DEBIAN/postinst", b"this inert fixture must not be written or executed"),
            entry("usr/bin/irrelevant", b"ignored"),
        )), 1)
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"libEGL.so.1.1.0"})

    def test_absolute_and_parent_member_paths_refuse_even_for_unselected_data(self):
        escape = self.root / CANARY
        for name in [str(escape), "../" + CANARY, "usr/share/doc/../../" + CANARY,
                     PREFIX + "../" + CANARY, PREFIX + "../../../../" + CANARY]:
            with self.subTest(name=name):
                self.refused(archive(entry(name, b"never outside")))
                self.assertFalse(escape.exists())

    def test_unsafe_selected_symlink_targets_never_escape(self):
        outside = self.root / CANARY
        for target in [str(outside), "../" + CANARY, "subdir/libGL.so.1", "./libGL.so.1", CANARY]:
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.refused(archive(entry(PREFIX + "libGL.so.1", kind=tarfile.SYMTYPE, target=target)), Path(directory))
                self.assertFalse(outside.exists())

    def test_devices_fifos_and_hardlinks_are_rejected(self):
        for kind in [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.LNKTYPE]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.refused(archive(entry(PREFIX + "libXi.so.6", kind=kind, target="libXi.so.6.1.0")), Path(directory))
        self.assertFalse(any(self.destination.iterdir()))

    def test_unselected_special_members_cannot_hide_outside_the_library_filter(self):
        self.refused(archive(entry("usr/share/doc/device", kind=tarfile.CHRTYPE)))

    def test_duplicate_flattened_filenames_are_rejected_across_tar_streams(self):
        self.extract(archive(entry(PREFIX + "libXi.so.6.1.0", b"first")))
        self.refused(archive(entry("lib/aarch64-linux-gnu/libXi.so.6.1.0", b"second")))
        self.assertEqual((self.destination / "libXi.so.6.1.0").read_bytes(), b"first")

    def test_existing_regular_collision_is_not_overwritten(self):
        existing = self.destination / "libXi.so.6.1.0"
        existing.write_bytes(b"preserve")
        self.refused(archive(entry(PREFIX + existing.name, b"replace")))
        self.assertEqual(existing.read_bytes(), b"preserve")

    def test_existing_symlink_is_never_followed_by_a_regular_write(self):
        outside = self.root / CANARY
        outside.write_bytes(b"preserve")
        (self.destination / "libXi.so.6.1.0").symlink_to(outside)
        self.refused(archive(entry(PREFIX + "libXi.so.6.1.0", b"replace")))
        self.assertEqual(outside.read_bytes(), b"preserve")

    def test_link_then_regular_collision_cannot_replace_the_link_target(self):
        raw = archive(entry(PREFIX + "libXi.so.6", kind=tarfile.SYMTYPE, target="libXi.so.6.1.0"),
                      entry(PREFIX + "libXi.so.6", b"collision"))
        self.refused(raw)
        self.assertFalse((self.destination / "libXi.so.6.1.0").exists())

    def test_member_byte_limit_applies_to_libraries_and_ignored_docs(self):
        for name in [PREFIX + "libXi.so.6.1.0", "usr/share/doc/data"]:
            with self.subTest(name=name), patch.object(PAYLOAD, "MAX_MEMBER_BYTES", 8):
                self.refused(archive(entry(name, b"ninebytes")))

    def test_stream_byte_limit_includes_unselected_payloads(self):
        raw = archive(entry("usr/share/doc/data", b"not an extracted library"))
        with patch.object(PAYLOAD, "MAX_TAR_BYTES", 512):
            self.refused(raw)

    def test_stream_limit_includes_bytes_after_the_tar_end_marker(self):
        raw = archive(entry(PREFIX + "libXi.so.6.1.0", b"small"))
        with patch.object(PAYLOAD, "MAX_TAR_BYTES", len(raw)):
            self.refused(raw + b"x" * 513)

    def test_member_count_bound_includes_ignored_records(self):
        with patch.object(PAYLOAD, "MAX_MEMBERS", 2):
            self.refused(archive(*(entry(f"usr/share/doc/{index}", b"small") for index in range(3))))

    def test_library_byte_budget_accumulates_across_streams_before_new_file_creation(self):
        with patch.object(PAYLOAD, "MAX_LIBRARY_BYTES", 6):
            self.assertEqual(self.extract(archive(entry(PREFIX + "libGL.so.1.7.0", b"four"))), 1)
            self.refused(archive(entry(PREFIX + "libEGL.so.1.1.0", b"new")))
        self.assertEqual((self.destination / "libGL.so.1.7.0").read_bytes(), b"four")
        self.assertFalse((self.destination / "libEGL.so.1.1.0").exists(), "Over-budget file was partially written")

    def test_dangling_and_cyclic_soname_links_refuse_at_finish(self):
        for members in [
            [entry(PREFIX + "libXi.so.6", kind=tarfile.SYMTYPE, target="libXi.so.6.1.0")],
            [entry(PREFIX + "libXi.so.6", kind=tarfile.SYMTYPE, target="libXi.so.7"),
             entry(PREFIX + "libXi.so.7", kind=tarfile.SYMTYPE, target="libXi.so.6")],
        ]:
            with self.subTest(members=members), tempfile.TemporaryDirectory(dir=self.root) as directory:
                destination = Path(directory)
                PAYLOAD.extract_tar_stream(io.BytesIO(archive(*members)), destination, set())
                with self.assertRaises(PAYLOAD.PayloadError):
                    PAYLOAD.finish_library_links(destination)

    def test_safe_soname_chains_resolve_only_inside_the_library_directory(self):
        self.extract(archive(
            entry(PREFIX + "libXi.so.6.1.0", b"pixels"),
            entry(PREFIX + "libXi.so.7", kind=tarfile.SYMTYPE, target="libXi.so.6.1.0"),
            entry(PREFIX + "libXi.so.6", kind=tarfile.SYMTYPE, target="libXi.so.7"),
        ))
        PAYLOAD.finish_library_links(self.destination)
        self.assertEqual((self.destination / "libXi.so.6").resolve(), self.destination.resolve() / "libXi.so.6.1.0")

    def test_payload_modes_never_introduce_setuid_or_setgid_bits(self):
        self.extract(archive(entry(PREFIX + "libXi.so.6.1.0", b"data", mode=0o6755)))
        self.assertEqual(stat.S_IMODE((self.destination / "libXi.so.6.1.0").stat().st_mode) & 0o6000, 0)


if __name__ == "__main__":
    unittest.main()
