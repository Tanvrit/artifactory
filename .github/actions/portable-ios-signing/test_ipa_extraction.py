"""Synthetic ad-hoc signing and hostile ZIP metadata; no distribution credentials."""

import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from test_sign import sign


class IpaExtractionTest(unittest.TestCase):
    def test_unsafe_entry_paths_are_rejected_before_native_extraction(self):
        for name in ("../outside", "/outside", "Payload/../outside", "Payload\\outside"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); ipa = root / "test.ipa"
                with zipfile.ZipFile(ipa, "w") as archive:
                    archive.writestr(name, b"synthetic")
                execute = Mock()
                with self.assertRaises(sign.SigningError):
                    sign.unpack_ipa(ipa, root / "unpacked", execute, {})
                execute.assert_not_called()

    def test_unsafe_links_and_writes_through_links_are_rejected(self):
        for target, child in (("../../outside", False), ("/outside", False), ("safe", True)):
            with self.subTest(target=target, child=child), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); ipa = root / "test.ipa"
                with zipfile.ZipFile(ipa, "w") as archive:
                    link = zipfile.ZipInfo("Payload/link")
                    link.create_system = 3
                    link.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(link, target)
                    if child:
                        archive.writestr("Payload/link/child", b"synthetic")
                execute = Mock()
                with self.assertRaises(sign.SigningError):
                    sign.unpack_ipa(ipa, root / "unpacked", execute, {})
                execute.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("codesign") and shutil.which("ditto"), "Native metadata check requires macOS")
    def test_native_extraction_preserves_a_valid_ad_hoc_bundle_signature(self):
        env = {key: value for key, value in os.environ.items() if key in ("HOME", "TMPDIR", "LANG")}
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        with tempfile.TemporaryDirectory(prefix="ipa-signature-fixture-") as folder:
            root = Path(folder); app = root / "original/Payload/Synthetic.app"
            executable = app / "Contents/MacOS/Synthetic"
            executable.parent.mkdir(parents=True)
            shutil.copyfile("/bin/echo", executable)
            executable.chmod(0o755)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Synthetic",
                "CFBundleIdentifier": "invalid.fixture.synthetic", "CFBundlePackageType": "APPL", "CFBundleVersion": "1"}))

            def invoke(args):
                return subprocess.run(args, env=env, capture_output=True, timeout=60).returncode

            self.assertEqual(invoke(["codesign", "--force", "--sign", "-", str(app)]), 0)
            self.assertEqual(invoke(["codesign", "--verify", "--deep", "--strict", str(app)]), 0)
            ipa = root / "Synthetic.ipa"
            self.assertEqual(invoke(["ditto", "-c", "-k", "--keepParent", str(app.parent), str(ipa)]), 0)
            python_extract = root / "python"
            with zipfile.ZipFile(ipa) as archive:
                archive.extractall(python_extract)
            # Python extraction loses Unix executable permissions. Whether codesign
            # rejects this synthetic macOS bundle varies between OS versions; the
            # metadata loss itself is the portable regression assertion.
            self.assertEqual((python_extract / "Payload/Synthetic.app/Contents/MacOS/Synthetic").stat().st_mode & 0o111, 0)
            unpacked = root / "native"
            sign.unpack_ipa(ipa, unpacked, sign.run, env)
            self.assertEqual((unpacked / "Payload/Synthetic.app/Contents/MacOS/Synthetic").stat().st_mode & 0o777, 0o755)
            self.assertEqual(invoke(["codesign", "--verify", "--deep", "--strict", str(unpacked / "Payload/Synthetic.app")]), 0)


if __name__ == "__main__":
    unittest.main()
