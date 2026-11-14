"""Exercise the real installer with a private SDK tree and recording stub only."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("ensure-platform.sh")


class EnsurePlatformTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "private 'quoted' SDK"
        self.sdk.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.record = self.root / "sdkmanager-requests.json"
        self.sentinel = self.root / "sentinel"
        self.bash = shutil.which("bash")
        self.assertIsNotNone(self.bash)
        stub = self.bin / "sdkmanager"
        stub.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, re, sys\n"
            "path = pathlib.Path(os.environ['SDKMANAGER_RECORD'])\n"
            "calls = json.loads(path.read_text()) if path.exists() else []\n"
            "calls.append(sys.argv[1:])\n"
            "path.write_text(json.dumps(calls))\n"
            "code = int(os.environ['SDKMANAGER_EXIT'])\n"
            "if code == 0 and os.environ['SDKMANAGER_CREATE_JAR'] == '1':\n"
            "    assert len(sys.argv) == 2\n"
            "    package = sys.argv[1].removeprefix('platforms;')\n"
            "    assert re.fullmatch(r'android-[1-9][0-9]*(?:\\.(?:0|[1-9][0-9]*))?', package)\n"
            "    directory = pathlib.Path(os.environ['ANDROID_HOME']) / 'platforms' / package\n"
            "    if directory.is_file():\n"
            "        directory.unlink()\n"
            "    directory.mkdir(parents=True, exist_ok=True)\n"
            "    (directory / 'android.jar').write_bytes(b'synthetic installed platform')\n"
            "raise SystemExit(code)\n"
        )
        stub.chmod(0o755)

    def installed(self, name, complete=True):
        directory = self.sdk / "platforms" / name
        directory.mkdir(parents=True, exist_ok=True)
        if complete:
            (directory / "android.jar").write_bytes(b"synthetic platform fixture")

    def run_installer(self, sdk="37.1", exit_code=0, include_home=True, create_jar=True):
        # No inherited Android, credential or shell startup environment. The
        # executable named sdkmanager is always our private recording stub.
        environment = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "COMPILE_SDK": sdk,
            "SDKMANAGER_RECORD": str(self.record),
            "SDKMANAGER_EXIT": str(exit_code),
            "SDKMANAGER_CREATE_JAR": "1" if create_jar else "0",
            "PYTHONNOUSERSITE": "1",
        }
        if include_home:
            environment["ANDROID_HOME"] = str(self.sdk)
        return subprocess.run(
            [self.bash, str(SCRIPT)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def calls(self):
        return json.loads(self.record.read_text()) if self.record.exists() else []

    def test_explicit_minor_reuses_only_its_exact_installed_directory(self):
        self.installed("android-37.1")
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.calls())

    def test_other_major_or_minor_directories_cannot_satisfy_explicit_minor(self):
        for name in ["android-37.0", "android-37", "android-37.10"]:
            with self.subTest(installed=name):
                directory = self.sdk / "platforms" / name
                self.installed(name)
                try:
                    result = self.run_installer()
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual([["platforms;android-37.1"]], self.calls())
                finally:
                    shutil.rmtree(directory)
                    created = self.sdk / "platforms" / "android-37.1"
                    if created.is_dir():
                        shutil.rmtree(created)
                    self.record.unlink(missing_ok=True)

    def test_missing_explicit_minor_installs_one_exact_package_argument(self):
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-37.1"]], self.calls())

    def test_a_file_named_as_the_platform_does_not_count_as_installed(self):
        directory = self.sdk / "platforms"
        directory.mkdir()
        (directory / "android-37.1").write_text("not an SDK directory")
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-37.1"]], self.calls())
        self.assertTrue((directory / "android-37.1/android.jar").is_file())

    def test_partial_exact_minor_without_android_jar_requires_install(self):
        self.installed("android-37.1", complete=False)
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-37.1"]], self.calls())

    def test_explicit_zero_minor_requires_exact_zero_minor_directory(self):
        self.installed("android-37")
        self.installed("android-37.1")
        result = self.run_installer("37.0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-37.0"]], self.calls())
        self.record.unlink()
        self.installed("android-37.0")
        result = self.run_installer("37.0")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.calls())

    def test_integer_36_preserves_unsuffixed_install_package(self):
        result = self.run_installer("36")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-36"]], self.calls())

    def test_integer_37_preserves_zero_minor_install_package(self):
        result = self.run_installer("37")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([["platforms;android-37.0"]], self.calls())

    def test_integer_inputs_preserve_existing_major_and_minor_reuse(self):
        for sdk, name in [
            ("36", "android-36"),
            ("36", "android-36.2"),
            ("37", "android-37"),
            ("37", "android-37.0"),
            ("37", "android-37.1"),
        ]:
            with self.subTest(sdk=sdk, installed=name):
                directory = self.sdk / "platforms" / name
                self.installed(name)
                try:
                    result = self.run_installer(sdk)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual([], self.calls())
                finally:
                    shutil.rmtree(directory)

    def test_integer_inputs_preserve_legacy_directory_only_reuse(self):
        for sdk, name in [("36", "android-36"), ("37", "android-37.0")]:
            with self.subTest(sdk=sdk):
                directory = self.sdk / "platforms" / name
                self.installed(name, complete=False)
                try:
                    result = self.run_installer(sdk)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual([], self.calls())
                finally:
                    shutil.rmtree(directory)

    def test_malformed_or_injected_sdk_never_invokes_installer(self):
        for value in [
            "", "0", "037", "037.1", "37.01", "37.", ".1", "37.1.2",
            " 37.1", "37.1 ", "37.1\n37", "37/1", "37.1*",
            "37.1; touch sentinel", "$(touch sentinel)", "`touch sentinel`",
            "37.1 --licenses",
        ]:
            with self.subTest(value=value):
                result = self.run_installer(value)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], self.calls())
                self.assertFalse(self.sentinel.exists())

    def test_sdkmanager_failure_is_propagated_without_retry_or_substitution(self):
        result = self.run_installer(exit_code=23)
        self.assertEqual(23, result.returncode)
        self.assertEqual([["platforms;android-37.1"]], self.calls())

    def test_success_without_exact_minor_android_jar_fails_closed(self):
        result = self.run_installer(create_jar=False)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([["platforms;android-37.1"]], self.calls())
        self.assertFalse((self.sdk / "platforms/android-37.1/android.jar").exists())

    def test_integer_installs_preserve_legacy_exit_status_contract(self):
        for sdk, package in [("36", "android-36"), ("37", "android-37.0")]:
            with self.subTest(sdk=sdk):
                result = self.run_installer(sdk, create_jar=False)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([["platforms;" + package]], self.calls())
                self.record.unlink()

    def test_missing_android_home_fails_before_sdkmanager(self):
        result = self.run_installer(include_home=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("ANDROID_HOME is required", result.stderr)
        self.assertEqual([], self.calls())


if __name__ == "__main__":
    unittest.main()
