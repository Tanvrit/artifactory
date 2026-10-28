"""Synthetic API failures: only closed category labels may leave the signer."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import provision
from provision import ApiFailure, ProvisioningApiError
from test_sign import environment, sign


CANARY = "SYNTHETIC-PRIVATE-API-PAYLOAD-MUST-NOT-ESCAPE"


class ApiDiagnosticsTest(unittest.TestCase):
    def test_http_status_is_closed_metadata_and_response_is_never_read(self):
        cases = {400: ApiFailure.BAD_REQUEST, 401: ApiFailure.UNAUTHENTICATED,
                 403: ApiFailure.FORBIDDEN, 404: ApiFailure.NOT_FOUND,
                 409: ApiFailure.CONFLICT, 429: ApiFailure.RATE_LIMITED,
                 503: ApiFailure.UPSTREAM, 302: ApiFailure.OTHER_HTTP}
        for status, expected in cases.items():
            with self.subTest(status=status):
                client = provision.AppStoreClient(CANARY)
                response = io.BytesIO(CANARY.encode())
                error = HTTPError("https://private.invalid/" + CANARY, status, CANARY, {}, response)
                with patch.object(client.opener, "open", side_effect=error), \
                        patch.object(response, "read", side_effect=AssertionError("Must not read API error body")):
                    with self.assertRaises(ProvisioningApiError) as caught:
                        client.request("bundleIds/synthetic/bundleIdCapabilities")
                self.assertIs(caught.exception.category, expected)
                self.assertNotIn(CANARY, str(caught.exception))
                self.assertTrue(response.closed)

    def test_transport_failure_does_not_retain_exception_text(self):
        client = provision.AppStoreClient(CANARY)
        with patch.object(client.opener, "open", side_effect=URLError(CANARY)):
            with self.assertRaises(ProvisioningApiError) as caught:
                client.request("bundleIds/synthetic/bundleIdCapabilities")
        self.assertIs(caught.exception.category, ApiFailure.TRANSPORT)
        self.assertNotIn(CANARY, str(caught.exception))

    def test_collection_rejects_bad_shape_without_rendering_payload(self):
        client = provision.AppStoreClient(CANARY)
        for payload in ({"data": CANARY}, {CANARY: []}, [CANARY]):
            with self.subTest(payload=type(payload)), patch.object(client, "request", return_value=payload):
                with self.assertRaises(ProvisioningApiError) as caught:
                    client.collection("bundleIds/synthetic/bundleIdCapabilities")
                self.assertIs(caught.exception.category, ApiFailure.SCHEMA)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_main_retains_failure_and_cleanup_but_emits_only_enum_annotation(self):
        for category in ApiFailure:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); env = environment(root)
                output = root / "output"; output.touch(mode=0o600)
                env["GITHUB_OUTPUT"] = str(output)
                stdout = io.StringIO()
                with patch.object(sign.os, "environ", env), patch.object(sign.signal, "signal"), \
                        patch.object(sign.Path, "home", return_value=root / "home"), \
                        patch.object(sign, "perform", side_effect=ProvisioningApiError(CANARY, category)), \
                        redirect_stdout(stdout):
                    self.assertEqual(sign.main(), 1)
                self.assertEqual(stdout.getvalue(), "::error::Portable iOS signing failed; diagnostics withheld.\n" +
                                 "::error title=Portable signing API diagnostic::" + category.value + "\n")
                self.assertNotIn(CANARY, stdout.getvalue())
                self.assertIn("cleanup-stage=completed", output.read_text())
                self.assertFalse(list(root.glob("portable-ios-*")))

    def test_arbitrary_category_cannot_cross_annotation_boundary(self):
        error = ProvisioningApiError(CANARY, CANARY)
        self.assertIs(error.category, ApiFailure.SCHEMA)


if __name__ == "__main__":
    unittest.main()
