#!/usr/bin/env python3
"""Real JDK signature checks using disposable synthetic archives and test keys."""
import importlib.util
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/actions/verify-android-signatures/verify-android-bundle.py"
SPEC = importlib.util.spec_from_file_location("bundle_signatures", HELPER)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)
PASSWORD = "synthetic-fixture-only-never-production"


class AndroidBundleSignaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="synthetic-bundle-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.java_bin = Path(os.environ["JAVA_HOME"]) / "bin" if os.environ.get("JAVA_HOME") else None
        cls.env = VERIFY.tool_environment() | {"FIXTURE_KEYSTORE_PASSWORD": PASSWORD}
        cls.store = cls.root / "synthetic-test-store.p12"
        cls.tool("keytool", "-genkeypair", "-alias", "synthetic-test", "-keyalg", "RSA", "-keysize", "2048",
                 "-validity", "2", "-dname", "CN=synthetic-test-only", "-storetype", "PKCS12",
                 "-keystore", str(cls.store), "-storepass:env", "FIXTURE_KEYSTORE_PASSWORD",
                 "-keypass:env", "FIXTURE_KEYSTORE_PASSWORD", "-noprompt")
        cls.unsigned = cls.root / "unsigned.aab"
        with zipfile.ZipFile(cls.unsigned, "w") as archive:
            archive.writestr("base/manifest/AndroidManifest.xml", b"synthetic manifest fixture")
            archive.writestr("base/dex/classes.dex", b"synthetic payload fixture")
        cls.signed = cls.root / "signed.aab"
        shutil.copyfile(cls.unsigned, cls.signed)
        cls.tool("jarsigner", "-keystore", str(cls.store), "-storepass:env", "FIXTURE_KEYSTORE_PASSWORD",
                 "-keypass:env", "FIXTURE_KEYSTORE_PASSWORD", "-sigalg", "SHA256withRSA",
                 "-digestalg", "SHA-256", str(cls.signed), "synthetic-test")
        cls.signers = VERIFY.bundle_signers(cls.signed)

    @classmethod
    def tool(cls, name, *arguments):
        executable = str(cls.java_bin / name) if cls.java_bin else name
        result = subprocess.run([executable, *arguments], env=cls.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            raise AssertionError("Synthetic JDK fixture preparation failed")

    def test_valid_self_signed_bundle_passes_without_trust_store_changes(self):
        self.assertEqual(1, len(self.signers))
        self.assertEqual(self.signers, VERIFY.bundle_signers(self.signed))

    def test_unsigned_bundle_fails(self):
        with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
            VERIFY.bundle_signers(self.unsigned)
        self.assertIs(VERIFY.FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION, failure.exception.stage)
        self.assertNotEqual(0, self.exercise_cli(bundle_fixture=self.unsigned,
                            expected_stage="aab-payload-or-tool-verification"))

    def test_tampered_signed_payload_fails(self):
        tampered = self.root / "tampered.aab"
        with zipfile.ZipFile(self.signed) as before, zipfile.ZipFile(tampered, "w") as after:
            for entry in before.infolist():
                data = b"changed synthetic payload" if entry.filename == "base/dex/classes.dex" else before.read(entry)
                after.writestr(entry, data)
        with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
            VERIFY.bundle_signers(tampered)
        self.assertIs(VERIFY.FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION, failure.exception.stage)

    def test_unsigned_extra_payload_fails_even_when_other_entries_are_signed(self):
        partial = self.root / "partial.aab"
        shutil.copyfile(self.signed, partial)
        with zipfile.ZipFile(partial, "a") as archive:
            archive.writestr("base/assets/unsigned.txt", b"unsigned synthetic fixture")
        with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
            VERIFY.bundle_signers(partial)
        self.assertIs(VERIFY.FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION, failure.exception.stage)

    def test_unsigned_meta_inf_application_metadata_is_not_a_signature_exception(self):
        partial = self.root / "partial-metadata.aab"
        shutil.copyfile(self.signed, partial)
        with zipfile.ZipFile(partial, "a") as archive:
            archive.writestr("META-INF/services/synthetic.Provider", b"unsigned synthetic metadata")
        with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
            VERIFY.bundle_signers(partial)
        self.assertIs(VERIFY.FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION, failure.exception.stage)

    def test_duplicate_payload_names_fail_closed(self):
        duplicate = self.root / "duplicate.aab"
        shutil.copyfile(self.signed, duplicate)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "a") as archive:
                archive.writestr("base/dex/classes.dex", b"synthetic payload fixture")
        with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
            VERIFY.bundle_signers(duplicate)
        self.assertIs(VERIFY.FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION, failure.exception.stage)

    def exercise_cli(self, *, matching=True, signer_failure=False, bundle_present=True, expected=None,
                     signer_label="Signer #1", extra_certificate_line=None, expected_stage=None,
                     multiple_apks=False, bundle_fixture=None, metadata_unwritable=False):
        with tempfile.TemporaryDirectory(prefix="synthetic-signature-cli-") as directory:
            root = Path(directory)
            apk_dir, bundle_dir = root / "apks", root / "bundles"
            apk_dir.mkdir()
            bundle_dir.mkdir()
            (apk_dir / "synthetic.apk").write_bytes(b"fake APK; signer is independently stubbed")
            if multiple_apks:
                (apk_dir / "second.apk").write_bytes(b"second fake APK; distinct signer stub")
            if bundle_present:
                shutil.copyfile(bundle_fixture or self.signed, bundle_dir / "synthetic.aab")
            signer = root / "apksigner"
            digest = next(iter(self.signers)) if matching else "0" * 64
            signer.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                              f"digest = {'0' * 64!r} if {multiple_apks!r} and Path(sys.argv[-1]).name == 'second.apk' else {digest!r}\n"
                              f"print({signer_label + ' certificate SHA-256 digest: '!r} + digest)\n"
                              + (f"print({extra_certificate_line!r})\n" if extra_certificate_line else "") +
                              "print('synthetic-diagnostic-canary', file=sys.stderr)\n"
                              f"sys.exit({1 if signer_failure else 0})\n")
            signer.chmod(0o700)
            arguments = [sys.executable, str(HELPER),
                         "--bundle-dir", str(bundle_dir), "--apk-dir", str(apk_dir),
                         "--apksigner", str(signer)]
            if expected is not None:
                value = next(iter(self.signers)) if expected in ("match", "colon-match") else expected
                if expected == "colon-match":
                    value = ":".join(value[index:index + 2] for index in range(0, len(value), 2)).upper()
                arguments += ["--expected-certificate-sha256", value]
            metadata = root / "runner-output"
            if metadata_unwritable:
                metadata.mkdir()
            else:
                metadata.touch(mode=0o600)
            result = subprocess.run(arguments, cwd=root,
                                    env=VERIFY.tool_environment() | {"GITHUB_OUTPUT": str(metadata)},
                                    capture_output=True, text=True, timeout=120, check=False)
            output = result.stdout + result.stderr
            for private in (digest, PASSWORD, "synthetic-diagnostic-canary", str(root), str(self.store)):
                self.assertNotIn(private, output)
            if expected is not None and value.strip():
                self.assertNotIn(value, output)
            if result.returncode:
                lines = result.stderr.splitlines()
                self.assertEqual("", result.stdout)
                self.assertEqual(2, len(lines))
                self.assertEqual("::error::Android release signature verification failed.", lines[0])
                allowed = {f"::error::Android release signature failure stage: {stage.value}."
                           for stage in VERIFY.FailureStage}
                self.assertIn(lines[1], allowed)
                if expected_stage is not None:
                    self.assertEqual(f"::error::Android release signature failure stage: {expected_stage}.", lines[1])
            else:
                self.assertIsNone(expected_stage)
                self.assertEqual("", result.stderr)
            if not metadata_unwritable:
                stage = expected_stage or "success"
                self.assertEqual(f"verification_stage={stage}\n", metadata.read_text())
            return result.returncode

    def test_bundle_matches_verified_apk_signer(self):
        self.assertEqual(0, self.exercise_cli())

    def test_approved_certificate_is_enforced(self):
        self.assertEqual(0, self.exercise_cli(expected="match"))

    def test_current_build_tools_v30_signer_label(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label="V3.0 Signer:"))

    def test_current_build_tools_v31_signer_sdk_range(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label=
                         "V3.1 Signer: (minSdkVersion=33, maxSdkVersion=2147483647)"))

    def test_legacy_v31_signer_sdk_range(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label=
                         "Signer (minSdkVersion=33 (dev release=true), maxSdkVersion=2147483647)"))

    def test_current_build_tools_single_legacy_signer_label(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label="Signer:"))

    def test_current_build_tools_v1_single_signer_label(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label="V1 Signer:"))

    def test_current_build_tools_v2_single_signer_label(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label="V2 Signer:"))

    def test_current_build_tools_scheme_numbered_signer_labels(self):
        for scheme in ("V1", "V2", "V3.0"):
            with self.subTest(scheme=scheme):
                self.assertEqual(0, self.exercise_cli(expected="match", signer_label=f"{scheme} Signer #1:"))

    def test_prefixed_second_signer_cannot_be_omitted_from_approved_identity(self):
        self.assertNotEqual(0, self.exercise_cli(expected="match", signer_label="V2 Signer #1:",
                            extra_certificate_line="V2 Signer #2: certificate SHA-256 digest: " + "0" * 64,
                            expected_stage="approved-pin-mismatch"))

    def test_prefixed_second_signer_cannot_be_omitted_from_bundle_match(self):
        self.assertNotEqual(0, self.exercise_cli(signer_label="V2 Signer #1:",
                            extra_certificate_line="V2 Signer #2: certificate SHA-256 digest: " + "0" * 64,
                            expected_stage="aab-signer-mismatch"))

    def test_malformed_prefixed_certificate_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(expected="match", signer_label="V2 Signer:",
                            extra_certificate_line="V1 Signer: certificate SHA-256 digest: " + "0" * 63,
                            expected_stage="apk-certificate-format"))

    def test_unknown_scheme_and_invalid_index_remain_fail_closed(self):
        for label in ("V4 Signer:", "V2 Signer #0:", "V2 Signer #-1:", "V3.2 Hybrid Classical Signer:"):
            with self.subTest(label=label):
                self.assertNotEqual(0, self.exercise_cli(expected="match", signer_label=label,
                                    expected_stage="apk-certificate-format"))

    def test_prefixed_certificate_crlf_and_horizontal_whitespace(self):
        self.assertEqual(0, self.exercise_cli(expected="match", signer_label="V2 Signer:",
                         extra_certificate_line="V2 Signer: certificate SHA-256 digest: " + next(iter(self.signers)) + "\t\r"))

    def test_other_sdk_range_signer_is_not_ignored(self):
        self.assertNotEqual(0, self.exercise_cli(expected="match", extra_certificate_line=
                            "V3.1 Signer: (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: " + "0" * 64,
                            expected_stage="approved-pin-mismatch"))

    def test_unknown_certificate_label_fails_closed_instead_of_being_ignored(self):
        self.assertNotEqual(0, self.exercise_cli(expected="match", extra_certificate_line=
                            "Unknown signer certificate SHA-256 digest: " + "0" * 64,
                            expected_stage="apk-certificate-format"))

    def test_source_stamp_is_distinct_from_application_signer(self):
        self.assertEqual(0, self.exercise_cli(expected="match", extra_certificate_line=
                         "Source Stamp Signer: certificate SHA-256 digest: " + "0" * 64))

    def test_public_colon_separated_certificate_is_normalized(self):
        self.assertEqual(0, self.exercise_cli(expected="colon-match"))

    def test_matching_apk_and_bundle_from_unapproved_key_are_rejected(self):
        self.assertNotEqual(0, self.exercise_cli(expected="0" * 64, expected_stage="approved-pin-mismatch"))

    def test_malformed_expected_certificate_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(expected="synthetic-invalid-certificate",
                            expected_stage="approved-pin-format"))

    def test_other_apk_certificate_is_rejected(self):
        self.assertNotEqual(0, self.exercise_cli(matching=False, expected_stage="aab-signer-mismatch"))

    def test_apk_verification_failure_is_private_and_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(signer_failure=True, expected_stage="apk-tool-verification"))

    def test_missing_bundle_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(bundle_present=False, expected_stage="artifact-discovery"))

    def test_distinct_apk_signer_sets_have_a_fixed_stage(self):
        self.assertNotEqual(0, self.exercise_cli(multiple_apks=True, expected_stage="multiple-apk-signers"))

    def test_malformed_aab_signer_output_has_a_fixed_stage(self):
        with mock.patch.object(VERIFY, "run_tool", return_value="synthetic-diagnostic-canary"):
            with self.assertRaises(VERIFY.SignatureVerificationError) as failure:
                VERIFY.bundle_signers(self.signed)
        self.assertIs(VERIFY.FailureStage.AAB_SIGNER_FORMAT, failure.exception.stage)

    def test_failure_stage_rejects_arbitrary_error_text(self):
        with self.assertRaises(ValueError) as failure:
            VERIFY.SignatureVerificationError("synthetic-diagnostic-canary")
        self.assertEqual("", str(failure.exception))

    def test_tool_exception_details_are_not_emitted_by_cli(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-private-stage-") as directory:
            root = Path(directory)
            stdout, stderr = io.StringIO(), io.StringIO()
            arguments = [str(HELPER), "--apk-dir", str(root), "--bundle-dir", str(root),
                         "--apksigner", str(root / "private-tool-path")]
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(sys, "argv", arguments), \
                 mock.patch.object(VERIFY, "release_files", return_value=[self.signed]), \
                 mock.patch.object(VERIFY.subprocess, "run", side_effect=OSError("synthetic-diagnostic-canary")), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(1, VERIFY.main())
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("::error::Android release signature verification failed.\n"
                             "::error::Android release signature failure stage: apk-tool-verification.\n",
                             stderr.getvalue())

    def test_unavailable_metadata_preserves_failed_verifier_exit(self):
        self.assertEqual(1, self.exercise_cli(signer_failure=True, metadata_unwritable=True,
                                            expected_stage="apk-tool-verification"))

    def test_unavailable_metadata_preserves_successful_verifier_exit(self):
        self.assertEqual(0, self.exercise_cli(metadata_unwritable=True))

    def test_metadata_rejects_arbitrary_diagnostic_payload(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-stage-metadata-") as directory:
            output = Path(directory) / "runner-output"
            output.touch(mode=0o600)
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=True):
                VERIFY.write_stage_metadata("synthetic-private-canary\nverification_stage=success")
                self.assertEqual("", output.read_text())
                VERIFY.write_stage_metadata(VERIFY.FailureStage.APK_TOOL_VERIFICATION)
            self.assertEqual("verification_stage=apk-tool-verification\n", output.read_text())

    def test_child_tools_never_receive_metadata_transport_or_private_environment(self):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": "synthetic-private-output-path",
                                         "RELEASE_STORE_PASSWORD": "synthetic-private-canary",
                                         "PATH": "/usr/bin:/bin"}, clear=True):
            self.assertEqual({"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                             VERIFY.tool_environment())

    def test_malformed_metadata_path_preserves_success_and_failure_without_payload(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                stdout, stderr = io.StringIO(), io.StringIO()
                arguments = [str(HELPER), "--apk-dir", "synthetic-apks", "--bundle-dir", "synthetic-bundles",
                             "--apksigner", "synthetic-tool"]
                error = VERIFY.SignatureVerificationError(VERIFY.FailureStage.APK_TOOL_VERIFICATION) if failure else None
                # os.environ cannot normally transport NUL; a malformed mapping
                # still must not let diagnostic I/O mask a cryptographic outcome.
                with mock.patch.object(VERIFY.os, "environ", {"GITHUB_OUTPUT": "synthetic-private-canary\0output"}), \
                     mock.patch.object(sys, "argv", arguments), \
                     mock.patch.object(VERIFY, "release_files", return_value=[self.signed]), \
                     mock.patch.object(VERIFY, "apk_signers", return_value=self.signers, side_effect=error), \
                     mock.patch.object(VERIFY, "bundle_signers", return_value=self.signers), \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(1 if failure else 0, VERIFY.main())
                output = stdout.getvalue() + stderr.getvalue()
                self.assertNotIn("synthetic-private-canary", output)
                self.assertNotIn("Traceback", output)
                self.assertNotIn(next(iter(self.signers)), output)
                if failure:
                    self.assertEqual("::error::Android release signature verification failed.\n"
                                     "::error::Android release signature failure stage: apk-tool-verification.\n",
                                     stderr.getvalue())
                else:
                    self.assertEqual("Android bundle payload signatures match the verified release APK signer.\n",
                                     stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
