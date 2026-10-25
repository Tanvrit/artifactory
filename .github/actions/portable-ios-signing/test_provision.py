"""Synthetic App Store API/profile tests; no real account or signing material."""

import base64
import io
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import json
import os
import subprocess
import unittest
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import provision
from test_sign import Client, Tools, profile


class ProvisionTest(unittest.TestCase):
    def test_redirects_never_forward_the_token_to_another_endpoint(self):
        for code in (301, 302, 303, 307, 308):
            for body in (None, {"synthetic": True}):
                with self.subTest(code=code, method="POST" if body else "GET"):
                    calls = []

                    class SyntheticHTTPS(HTTPSHandler):
                        def https_open(self, request):
                            calls.append((request.full_url, request.get_header("Authorization")))
                            response = addinfourl(io.BytesIO(b"SYNTHETIC-PRIVATE-RESPONSE"),
                                                  {"Location": "https://foreign.example/token-sink"},
                                                  request.full_url, code)
                            response.msg = "Redirect"
                            return response

                    client = provision.AppStoreClient("SYNTHETIC-TOKEN")
                    client.opener = build_opener(provision.NoRedirect(), SyntheticHTTPS())
                    with self.assertRaisesRegex(provision.ProvisioningError, rf"HTTP {code}") as error:
                        client.request("certificates", body=body)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0][0], "https://api.appstoreconnect.apple.com/v1/certificates")
                    self.assertEqual(calls[0][1].split()[1], "SYNTHETIC-TOKEN")
                    self.assertNotIn("SYNTHETIC", str(error.exception))
                    self.assertNotIn("foreign.example", str(error.exception))

    def test_jwt_signature_verifies_with_generated_synthetic_p256_key(self):
        def execute(stage, args, env):
            result = subprocess.run(args, env=env, capture_output=True, check=True)
            return result.stdout

        with tempfile.TemporaryDirectory() as d:
            private = Path(d); key = private / "synthetic.p8"; public = private / "public.pem"
            env = {"PATH": os.environ["PATH"]}
            execute("generate synthetic key", ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)], env)
            public.write_bytes(execute("read synthetic public key", ["openssl", "pkey", "-in", str(key), "-pubout"], env))
            token = provision.authentication_token(private, key, "ABCDEFGHIJ", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", execute, env)
            header, payload, signature = token.split(".")
            self.assertEqual(json.loads(base64.urlsafe_b64decode(header + "=="))["alg"], "ES256")
            self.assertEqual(json.loads(base64.urlsafe_b64decode(payload + "=="))["aud"], "appstoreconnect-v1")
            raw = base64.urlsafe_b64decode(signature + "==")
            self.assertEqual(len(raw), 64)
            parts = []
            for value in (raw[:32], raw[32:]):
                value = value.lstrip(b"\0") or b"\0"
                if value[0] & 0x80:
                    value = b"\0" + value
                parts.append(b"\x02" + bytes([len(value)]) + value)
            sequence = b"".join(parts)
            sigfile = private / "synthetic.signature"; sigfile.write_bytes(b"\x30" + bytes([len(sequence)]) + sequence)
            message = private / "verify-message"; message.write_bytes(f"{header}.{payload}".encode())
            execute("verify synthetic signature", ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(sigfile), str(message)], env)

    def test_unsupported_capability_fails_before_any_api_call(self):
        class NoCalls:
            def collection(self, path, **query):
                raise AssertionError("Unsupported configuration must not contact Apple")
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(provision.ProvisioningError, "declared capabilities"):
            provision.application_profile(NoCalls(), b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test", Path(d), Tools(Path(d)), {}, {"unknown-capability": True})

    def test_es256_encoding_preserves_leading_zero_and_rejects_other_curve(self):
        self.assertEqual(provision.raw_es256(bytes.fromhex("3006020101020102")), b"\0" * 31 + b"\1" + b"\0" * 31 + b"\2")
        big = b"\1" * 33
        with self.assertRaises(provision.ProvisioningError):
            provision.raw_es256(b"\x30\x26\x02\x21" + big + b"\x02\x01\x01")
        with self.assertRaises(provision.ProvisioningError):
            provision.raw_es256(b"invalid")

    def test_profile_rejects_wrong_identity_expired_development_and_device_profiles(self):
        valid = profile()
        self.assertTrue(provision.profile_matches(valid, b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test", {}))
        changes = [{"TeamIdentifier": ["ZZZZZZZZZZ"]}, {"DeveloperCertificates": [b"OTHER-CERT"]},
                   {"ExpirationDate": datetime.now() - timedelta(days=1)}, {"ProvisionedDevices": ["DEVICE"]},
                   {"ProvisionsAllDevices": True}, {"Entitlements": dict(valid["Entitlements"], **{"get-task-allow": True})}]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(provision.profile_matches(dict(valid, **change), b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test", {}))
        self.assertFalse(provision.profile_matches(valid, b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test", {"aps-environment": "development"}))
        valid["Entitlements"]["aps-environment"] = "production"
        self.assertTrue(provision.profile_matches(valid, b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test", {"aps-environment": "development"}))

    def test_reuses_matching_profile_without_api_mutation(self):
        class Reuse(Client):
            def collection(self, path, **query):
                if path.endswith("/profiles"):
                    return [{"attributes": {"profileType": "IOS_APP_STORE", "profileState": "ACTIVE",
                                            "profileContent": base64.b64encode(b"SYNTHETIC-PROFILE").decode()}}]
                return super().collection(path, **query)

            def request(self, path, *, body=None):
                raise AssertionError("A matching profile must be reused")

        with tempfile.TemporaryDirectory() as d:
            result, path = provision.application_profile(Reuse("synthetic"), b"SYNTHETIC-CERT", "ABCDEFGHIJ",
                                                        "com.example.test", Path(d), Tools(Path(d)), {}, {})
            self.assertEqual(result["UUID"], profile()["UUID"])
            self.assertTrue(path.exists())

    def test_profiles_cannot_be_created_for_another_certificate(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(provision.ProvisioningError, "not uniquely registered"):
            provision.application_profile(Client("synthetic"), b"OTHER-CERT", "ABCDEFGHIJ", "com.example.test", Path(d), Tools(Path(d)), {}, {})

    def test_preserves_existing_profile_and_rolls_back_partial_install(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            source = home / "source.mobileprovision"; source.write_bytes(b"SYNTHETIC-PROFILE")
            first = provision.install_profile(profile(), source, home)
            self.assertEqual(len(first), 2)
            self.assertEqual(provision.install_profile(profile(), source, home), [])
            first[0].unlink()
            first[1].write_bytes(b"EXISTING-DIFFERENT-CONTENT")
            with self.assertRaisesRegex(provision.ProvisioningError, "different content"):
                provision.install_profile(profile(), source, home)
            self.assertFalse(first[0].exists())
            self.assertEqual(first[1].read_bytes(), b"EXISTING-DIFFERENT-CONTENT")


if __name__ == "__main__":
    unittest.main()
