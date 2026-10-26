"""Exercise the composite action using synthetic tools and caller workspaces."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/verify-android-signatures/action.yml"


class AndroidVerificationActionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action = yaml.load(ACTION.read_text(), Loader=yaml.BaseLoader)
        cls.step = cls.action["runs"]["steps"][0]

    def invoke(self, *, signer=True, executable=True, helper_status=0, literal_paths=False, expected=""):
        with tempfile.TemporaryDirectory(prefix="synthetic-android-action-") as directory:
            root = Path(directory)
            caller = root / "caller checkout"
            caller.mkdir()
            action = root / "downloaded action"
            action.mkdir()
            capture = root / "arguments.json"
            helper = action / "verify-android-bundle.py"
            helper.write_text("import json,os,sys\nfrom pathlib import Path\n"
                              "Path(os.environ['SYNTHETIC_ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))\n"
                              f"sys.exit({helper_status})\n")
            sdk = root / "public sdk"
            if signer:
                for version in ("31.0.0", "35.0.0"):
                    tool = sdk / "build-tools" / version / "apksigner"
                    tool.parent.mkdir(parents=True)
                    tool.write_text("#!/bin/sh\nexit 0\n")
                    tool.chmod(0o700 if executable else 0o600)
            apk = "release $(touch injected) apks" if literal_paths else "release apks"
            bundle = "release bundles"
            environment = {"PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/opt/homebrew/bin",
                           "ANDROID_HOME": str(sdk), "APK_DIR": apk, "BUNDLE_DIR": bundle,
                           "VERIFY_ACTION_PATH": str(action), "SYNTHETIC_ARGUMENTS": str(capture),
                           "EXPECTED_CERTIFICATE_SHA256": expected,
                           "RELEASE_STORE_PASSWORD": "synthetic-private-canary"}
            result = subprocess.run(["bash", "-x", "-c", self.step["run"]], cwd=caller,
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertNotIn("synthetic-private-canary", result.stdout + result.stderr)
            if expected:
                self.assertNotIn(expected, result.stdout + result.stderr)
            self.assertFalse((caller / "injected").exists())
            arguments = json.loads(capture.read_text()) if capture.exists() else None
            return result, arguments, str(sdk / "build-tools/35.0.0/apksigner"), apk, bundle

    def test_missing_apksigner_fails_before_helper(self):
        result, arguments, *_ = self.invoke(signer=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIsNone(arguments)
        self.assertIn("apksigner is required", result.stdout)

    def test_nonexecutable_apksigner_fails_before_helper(self):
        result, arguments, *_ = self.invoke(executable=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIsNone(arguments)

    def test_action_supplies_downloaded_helper_and_latest_sdk_tool(self):
        result, arguments, signer, apk, bundle = self.invoke()
        self.assertEqual(0, result.returncode)
        self.assertEqual(["--bundle-dir", bundle, "--apk-dir", apk, "--apksigner", signer,
                          "--expected-certificate-sha256", ""], arguments)
        self.assertEqual("", self.action["inputs"]["expected_certificate_sha256"]["default"])
        self.assertEqual("false", self.action["inputs"]["expected_certificate_sha256"]["required"])

    def test_expected_certificate_is_transported_literally_without_logging(self):
        expected = ":".join(["A1"] * 32)
        result, arguments, *_ = self.invoke(expected=expected)
        self.assertEqual(0, result.returncode)
        self.assertEqual(["--expected-certificate-sha256", expected], arguments[-2:])

    def test_verifier_failure_stops_action(self):
        result, arguments, *_ = self.invoke(helper_status=19)
        self.assertEqual(19, result.returncode)
        self.assertIsNotNone(arguments)

    def test_inputs_are_literal_environment_arguments(self):
        result, arguments, _, apk, _ = self.invoke(literal_paths=True)
        self.assertEqual(0, result.returncode)
        self.assertEqual(apk, arguments[3])
        self.assertNotIn("${{ inputs.", self.step["run"])


if __name__ == "__main__":
    unittest.main()
