#!/usr/bin/env python3
"""Real JDK signature checks using disposable synthetic archives and test keys."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        with self.assertRaises(VERIFY.SignatureVerificationError):
            VERIFY.bundle_signers(self.unsigned)

    def test_tampered_signed_payload_fails(self):
        tampered = self.root / "tampered.aab"
        with zipfile.ZipFile(self.signed) as before, zipfile.ZipFile(tampered, "w") as after:
            for entry in before.infolist():
                data = b"changed synthetic payload" if entry.filename == "base/dex/classes.dex" else before.read(entry)
                after.writestr(entry, data)
        with self.assertRaises(VERIFY.SignatureVerificationError):
            VERIFY.bundle_signers(tampered)

    def test_unsigned_extra_payload_fails_even_when_other_entries_are_signed(self):
        partial = self.root / "partial.aab"
        shutil.copyfile(self.signed, partial)
        with zipfile.ZipFile(partial, "a") as archive:
            archive.writestr("base/assets/unsigned.txt", b"unsigned synthetic fixture")
        with self.assertRaises(VERIFY.SignatureVerificationError):
            VERIFY.bundle_signers(partial)

    def test_unsigned_meta_inf_application_metadata_is_not_a_signature_exception(self):
        partial = self.root / "partial-metadata.aab"
        shutil.copyfile(self.signed, partial)
        with zipfile.ZipFile(partial, "a") as archive:
            archive.writestr("META-INF/services/synthetic.Provider", b"unsigned synthetic metadata")
        with self.assertRaises(VERIFY.SignatureVerificationError):
            VERIFY.bundle_signers(partial)

    def test_duplicate_payload_names_fail_closed(self):
        duplicate = self.root / "duplicate.aab"
        shutil.copyfile(self.signed, duplicate)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "a") as archive:
                archive.writestr("base/dex/classes.dex", b"synthetic payload fixture")
        with self.assertRaises(VERIFY.SignatureVerificationError):
            VERIFY.bundle_signers(duplicate)

    def exercise_cli(self, *, matching=True, signer_failure=False, bundle_present=True):
        with tempfile.TemporaryDirectory(prefix="synthetic-signature-cli-") as directory:
            root = Path(directory)
            apk_dir, bundle_dir = root / "apks", root / "bundles"
            apk_dir.mkdir()
            bundle_dir.mkdir()
            (apk_dir / "synthetic.apk").write_bytes(b"fake APK; signer is independently stubbed")
            if bundle_present:
                shutil.copyfile(self.signed, bundle_dir / "synthetic.aab")
            signer = root / "apksigner"
            digest = next(iter(self.signers)) if matching else "0" * 64
            signer.write_text(f"#!{sys.executable}\nimport sys\n"
                              f"print('Signer #1 certificate SHA-256 digest: {digest}')\n"
                              "print('synthetic-diagnostic-canary', file=sys.stderr)\n"
                              f"sys.exit({1 if signer_failure else 0})\n")
            signer.chmod(0o700)
            result = subprocess.run([sys.executable, str(HELPER),
                                     "--bundle-dir", str(bundle_dir), "--apk-dir", str(apk_dir),
                                     "--apksigner", str(signer)], cwd=root, env=VERIFY.tool_environment(),
                                    capture_output=True, text=True, timeout=120, check=False)
            self.assertNotIn(digest, result.stdout + result.stderr)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
            self.assertNotIn("synthetic-diagnostic-canary", result.stdout + result.stderr)
            return result.returncode

    def test_bundle_matches_verified_apk_signer(self):
        self.assertEqual(0, self.exercise_cli())

    def test_other_apk_certificate_is_rejected(self):
        self.assertNotEqual(0, self.exercise_cli(matching=False))

    def test_apk_verification_failure_is_private_and_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(signer_failure=True))

    def test_missing_bundle_fails_closed(self):
        self.assertNotEqual(0, self.exercise_cli(bundle_present=False))


if __name__ == "__main__":
    unittest.main()
