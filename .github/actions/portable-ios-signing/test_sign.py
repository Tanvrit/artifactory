"""Synthetic subprocess contract tests; never load a real signing credential."""

import base64
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("portable_sign", Path(__file__).with_name("sign.py"))
sign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sign)


def environment(root):
    return {
        "IOS_DISTRIBUTION_CERTIFICATE_BASE64": base64.b64encode(b"SYNTHETIC-P12").decode(),
        "IOS_P12_PASSWORD": "SYNTHETIC-PASSWORD",
        "APPSTORE_CONNECT_PRIVATE_KEY": "SYNTHETIC-PEM",
        "APPSTORE_CONNECT_KEY_ID": "ABCDEFGHIJ",
        "APPSTORE_CONNECT_ISSUER_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "APPLE_TEAM_ID": "ABCDEFGHIJ",
        "PORTABLE_IOS_PROJECT": "iosApp/iosApp.xcodeproj",
        "PORTABLE_IOS_SCHEME": "iosApp",
        "PORTABLE_IOS_CONFIGURATION": "Release",
        "PORTABLE_IOS_BUNDLE_ID": "com.example.test",
        "PORTABLE_IOS_TEAM_ID": "ABCDEFGHIJ",
        "PORTABLE_IOS_EXPORT_METHOD": "app-store",
        "PORTABLE_IOS_UPLOAD_TESTFLIGHT": "false",
        "RUNNER_TEMP": str(root),
        "HOME": str(root / "home"),
        "PATH": os.environ["PATH"],
    }


class Tools:
    def __init__(self, private, fail=None):
        self.private, self.fail = private, fail
        self.calls = []
        self.search_list = ["/Users/CI User/Library/Keychains/login.keychain-db"]
        self.options = []
        self.bundle = "com.example.test"
        self.team = "ABCDEFGHIJ"
        self.exported_certificate = b"SYNTHETIC-CERT"

    def __call__(self, stage, args, env, timeout=180):
        self.calls.append((stage, args, env))
        if stage == self.fail:
            raise sign.SigningError("Synthetic stage failure")
        if stage == "Check certificate team":
            return b"subject=CN=Apple Distribution: Synthetic,OU=ABCDEFGHIJ,O=Synthetic\n"
        if stage == "Read distribution certificate DER":
            return b"SYNTHETIC-CERT"
        if stage == "Authenticate provisioning request":
            return bytes.fromhex("3006020101020102")
        if stage == "Validate app-specific provisioning profile":
            return plistlib.dumps(profile())
        if stage in ("Read keychain search list", "Read cleanup search list"):
            if stage.startswith("Read cleanup"):
                self.search_list.append("/Users/CI User/another-job.keychain-db")
            return "\n".join(json.dumps(p) for p in self.search_list).encode()
        if stage in ("Add temporary keychain", "Remove temporary keychain"):
            self.search_list = args[5:]
        if stage == "Resolve application identity":
            return json.dumps([{"buildSettings": {"WRAPPER_EXTENSION": "app", "PRODUCT_BUNDLE_IDENTIFIER": self.bundle}}]).encode()
        if stage == "Archive application":
            archive = Path(args[args.index("-archivePath") + 1])
            archive.mkdir()
            (archive / "Info.plist").write_bytes(plistlib.dumps({"ApplicationProperties": {
                "CFBundleIdentifier": self.bundle, "Team": self.team}}))
        if stage in ("Export signed IPA", "Upload internal-only TestFlight build", "Upload App Store-eligible build"):
            self.options.append(plistlib.loads((self.private / "exportOptions.plist").read_bytes()))
        if stage == "Export signed IPA":
            export = Path(args[args.index("-exportPath") + 1])
            export.mkdir()
            with zipfile.ZipFile(export / "application.ipa", "w") as ipa:
                ipa.writestr("Payload/Test.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": self.bundle}))
        if stage == "Inspect exported signing identity":
            return plistlib.dumps({"application-identifier": f"OLDPREFIX1.{self.bundle}",
                                   "com.apple.developer.team-identifier": self.team, "get-task-allow": False})
        if stage == "Unpack exported IPA":
            with zipfile.ZipFile(args[-2]) as ipa:
                ipa.extractall(args[-1])
        if stage == "Inspect exported certificate":
            certificate_args = [arg for arg in args if arg.startswith("--extract-certificates=")]
            if len(certificate_args) != 1:
                raise AssertionError("Certificate prefix must be attached to the optional codesign argument")
            Path(certificate_args[0].split("=", 1)[1] + "0").write_bytes(self.exported_certificate)
        return b""


def profile():
    return {"UUID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Name": "Synthetic app-specific profile",
            "TeamIdentifier": ["ABCDEFGHIJ"], "ApplicationIdentifierPrefix": ["OLDPREFIX1"],
            "DeveloperCertificates": [b"SYNTHETIC-CERT"], "ExpirationDate": datetime.now() + timedelta(days=60),
            "Entitlements": {"application-identifier": "OLDPREFIX1.com.example.test",
                             "com.apple.developer.team-identifier": "ABCDEFGHIJ", "get-task-allow": False}}


class Client:
    def __init__(self, token):
        self.token = token

    def collection(self, path, **query):
        if path == "certificates":
            return [{"id": "CERT1", "attributes": {"certificateContent": base64.b64encode(b"SYNTHETIC-CERT").decode()}}]
        if path == "bundleIds":
            return [{"id": "BUNDLE1", "attributes": {"identifier": "com.example.test"}}]
        return []

    def request(self, path, *, body=None):
        assert path == "profiles"
        assert body["data"]["attributes"]["profileType"] == "IOS_APP_STORE"
        assert body["data"]["relationships"]["certificates"]["data"] == [{"type": "certificates", "id": "CERT1"}]
        return {"data": {"attributes": {"profileContent": base64.b64encode(b"SYNTHETIC-PROFILE").decode()}}}


class PortableSigningTest(unittest.TestCase):
    def test_legacy_p12_retries_public_certificate_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private, fail="Read public distribution certificate")
            sign.perform(environment(root), private, tools, Client)
            retries = [(args, env) for stage, args, env in tools.calls if stage == "Read legacy public distribution certificate"]
            self.assertEqual(len(retries), 1)
            self.assertIn("-legacy", retries[0][0])
            self.assertIn("-nokeys", retries[0][0])
            self.assertNotIn("SYNTHETIC-PASSWORD", retries[0][0])

    def test_exports_without_implicit_upload_and_strips_build_secret_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private)
            sign.perform(environment(root), private, tools, Client)
            self.assertTrue((root / "export/application.ipa").exists())
            self.assertFalse(any("Upload" in s for s, _, _ in tools.calls))
            for stage, args, env in tools.calls:
                if stage.startswith(("Archive", "Export", "Resolve", "Verify", "Unpack")):
                    self.assertFalse(set(sign.SECRET_NAMES) & set(env))
                self.assertFalse(any(a.startswith("PRODUCT_BUNDLE_IDENTIFIER=") for a in args))
            self.assertEqual(tools.search_list, ["/Users/CI User/Library/Keychains/login.keychain-db",
                                                  "/Users/CI User/another-job.keychain-db"])
            self.assertEqual(tools.calls[-1][0], "Delete temporary keychain")
            self.assertFalse(tools.options[0]["manageAppVersionAndBuildNumber"])
            archive = next(args for stage, args, _ in tools.calls if stage == "Archive application")
            self.assertIn("CODE_SIGN_STYLE=Manual", archive)
            self.assertIn("PROVISIONING_PROFILE_SPECIFIER=$(PORTABLE_PROFILE_$(WRAPPER_EXTENSION))", archive)
            self.assertIn("PROVISIONING_PROFILE=$(PORTABLE_PROFILE_UUID_$(WRAPPER_EXTENSION))", archive)
            self.assertIn("PORTABLE_PROFILE_app=" + profile()["Name"], archive)
            self.assertIn("PORTABLE_PROFILE_UUID_app=" + profile()["UUID"], archive)
            self.assertNotIn("PROVISIONING_PROFILE_SPECIFIER=" + profile()["Name"], archive)
            self.assertNotIn("-allowProvisioningUpdates", archive)
            self.assertEqual(tools.options[0]["provisioningProfiles"], {"com.example.test": profile()["UUID"]})
            self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_explicit_upload_is_internal_only_and_keeps_version(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private); env = environment(root)
            env["PORTABLE_IOS_UPLOAD_TESTFLIGHT"] = "true"
            sign.perform(env, private, tools, Client)
            self.assertEqual([o["destination"] for o in tools.options], ["export", "upload"])
            self.assertTrue(tools.options[1]["testFlightInternalTestingOnly"])
            self.assertFalse(tools.options[1]["manageAppVersionAndBuildNumber"])

    def test_appstore_upload_requires_explicit_scope_and_keeps_version(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private); env = environment(root)
            env["PORTABLE_IOS_UPLOAD_APPSTORE"] = "true"
            sign.perform(env, private, tools, Client)
            self.assertEqual([o["destination"] for o in tools.options], ["export", "upload"])
            self.assertFalse(tools.options[1]["testFlightInternalTestingOnly"])
            self.assertFalse(tools.options[1]["manageAppVersionAndBuildNumber"])
            self.assertIn("Upload App Store-eligible build", [c[0] for c in tools.calls])

    def test_conflicting_upload_scopes_are_rejected_before_signing(self):
        with tempfile.TemporaryDirectory() as d:
            env = environment(Path(d))
            env.update(PORTABLE_IOS_UPLOAD_APPSTORE="true", PORTABLE_IOS_UPLOAD_TESTFLIGHT="true")
            with self.assertRaises(sign.SigningError):
                sign.configuration(env)

    def test_cleanup_on_each_failure_after_keychain_creation(self):
        for stage in ["Set keychain timeout", "Unlock temporary keychain", "Import distribution identity",
                      "Allow codesign access", "Read keychain search list", "Add temporary keychain", "Archive application",
                      "Export signed IPA", "Verify exported code signature", "Upload internal-only TestFlight build",
                      "Upload App Store-eligible build"]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                root = Path(d); private = root / "private"; private.mkdir()
                tools = Tools(private, fail=stage); env = environment(root)
                upload_flag = "PORTABLE_IOS_UPLOAD_APPSTORE" if stage == "Upload App Store-eligible build" else "PORTABLE_IOS_UPLOAD_TESTFLIGHT"
                env[upload_flag] = "true"
                with self.assertRaises(sign.SigningError):
                    sign.perform(env, private, tools, Client)
                self.assertEqual(tools.calls[-1][0], "Delete temporary keychain")
                self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_profile_and_keychain_cleanup_survives_search_list_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private, fail="Read cleanup search list")
            with self.assertRaises(sign.SigningError):
                sign.perform(environment(root), private, tools, Client)
            self.assertEqual(tools.calls[-1][0], "Delete temporary keychain")
            self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_rejects_identity_drift_before_archive_and_cleans(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private); tools.bundle = "com.other.app"
            with self.assertRaisesRegex(sign.SigningError, "Resolved application identity"):
                sign.perform(environment(root), private, tools, Client)
            self.assertFalse(any(stage == "Archive application" for stage, _, _ in tools.calls))
            self.assertFalse(any(stage == "Create temporary keychain" for stage, _, _ in tools.calls))

    def test_rejects_different_export_certificate_and_cleans_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); private = root / "private"; private.mkdir()
            tools = Tools(private); tools.exported_certificate = b"DIFFERENT-CERT"
            with self.assertRaises(sign.IpaFailure) as caught:
                sign.perform(environment(root), private, tools, Client)
            self.assertIs(caught.exception.kind, sign.IpaFailureKind.CERTIFICATE)
            self.assertFalse((root / "export/application.ipa").exists())
            self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_missing_or_conflicting_credentials_fail_closed(self):
        env = environment(Path("/tmp/synthetic"))
        for name in sign.SECRET_NAMES[:-1]:
            with self.subTest(name=name), self.assertRaises(sign.SigningError):
                sign.configuration({key: value for key, value in env.items() if key != name})
        env["APPLE_TEAM_ID"] = "ZZZZZZZZZZ"
        with self.assertRaisesRegex(sign.SigningError, "disagree"):
            sign.configuration(env)


if __name__ == "__main__":
    unittest.main()
