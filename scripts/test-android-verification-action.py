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

    def invoke(self, *, signer=True, executable=True, helper_status=0, literal_paths=False, expected="",
               failure_stage=None, metadata_stage=None):
        with tempfile.TemporaryDirectory(prefix="synthetic-android-action-") as directory:
            root = Path(directory)
            caller = root / "caller checkout"
            caller.mkdir()
            action = root / "downloaded action"
            action.mkdir()
            capture = root / "arguments.json"
            metadata = root / "runner-output"
            metadata.touch(mode=0o600)
            helper = action / "verify-android-bundle.py"
            fixed_failure = ("print('::error::Android release signature verification failed.', file=sys.stderr)\n"
                             f"print({'::error::Android release signature failure stage: ' + failure_stage + '.'!r}, file=sys.stderr)\n"
                             if failure_stage else "")
            helper.write_text("import json,os,sys\nfrom pathlib import Path\n"
                              "Path(os.environ['SYNTHETIC_ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))\n"
                              + (f"Path(os.environ['GITHUB_OUTPUT']).write_text({'verification_stage=' + metadata_stage + chr(10)!r})\n"
                                 if metadata_stage is not None else "")
                              + fixed_failure +
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
                           "GITHUB_OUTPUT": str(metadata),
                           "RELEASE_STORE_PASSWORD": "synthetic-private-canary"}
            result = subprocess.run(["bash", "-x", "-c", self.step["run"]], cwd=caller,
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertNotIn("synthetic-private-canary", result.stdout + result.stderr)
            if expected:
                self.assertNotIn(expected, result.stdout + result.stderr)
            self.assertFalse((caller / "injected").exists())
            arguments = json.loads(capture.read_text()) if capture.exists() else None
            result.verification_metadata = metadata.read_text()
            return result, arguments, str(sdk / "build-tools/35.0.0/apksigner"), apk, bundle

    def test_missing_apksigner_fails_before_helper(self):
        result, arguments, *_ = self.invoke(signer=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIsNone(arguments)
        self.assertIn("apksigner is required", result.stdout)
        self.assertEqual("verification_stage=apksigner-missing\n", result.verification_metadata)

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

    def test_fixed_failure_stage_reaches_caller_without_exposing_values(self):
        result, arguments, *_ = self.invoke(helper_status=1, failure_stage="approved-pin-mismatch",
                                           expected="A1" * 32)
        self.assertEqual(1, result.returncode)
        self.assertIsNotNone(arguments)
        self.assertIn("::error::Android release signature verification failed.\n", result.stderr)
        self.assertIn("::error::Android release signature failure stage: approved-pin-mismatch.\n", result.stderr)

    def test_inputs_are_literal_environment_arguments(self):
        result, arguments, _, apk, _ = self.invoke(literal_paths=True)
        self.assertEqual(0, result.returncode)
        self.assertEqual(apk, arguments[3])
        self.assertNotIn("${{ inputs.", self.step["run"])

    def test_failed_helper_keeps_nonzero_exit_and_fixed_output_for_composite_mapping(self):
        result, *_ = self.invoke(helper_status=19, metadata_stage="approved-pin-mismatch")
        self.assertEqual(19, result.returncode)
        self.assertEqual("verification_stage=approved-pin-mismatch\n", result.verification_metadata)
        self.assertEqual("verify", self.step["id"])
        self.assertEqual("${{ steps.verify.outputs.verification_stage }}",
                         self.action["outputs"]["verification_stage"]["value"])
        self.assertNotIn("continue-on-error", self.step)

    def test_abnormal_failure_with_no_output_is_not_reported_as_success(self):
        result, *_ = self.invoke(helper_status=23)
        self.assertEqual(23, result.returncode)
        self.assertEqual("", result.verification_metadata)


if __name__ == "__main__":
    unittest.main()
