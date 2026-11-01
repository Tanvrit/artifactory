"""Closed IPA boundary labels; never render exception or credential payloads."""

from contextlib import nullcontext, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_sign import sign, Tools, Client, environment


CANARY = "SYNTHETIC-PRIVATE-IPA-DETAIL-MUST-NOT-ESCAPE"


class IpaDiagnosticTest(unittest.TestCase):
    def test_subprocess_failures_keep_exact_boundary_and_cleanup(self):
        cases = {"Unpack exported IPA": sign.IpaFailureKind.UNPACK,
                 "Verify exported code signature": sign.IpaFailureKind.SIGNATURE,
                 "Inspect exported certificate": sign.IpaFailureKind.CERTIFICATE,
                 "Inspect exported signing identity": sign.IpaFailureKind.ENTITLEMENTS}
        for stage, expected in cases.items():
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); private = root / "private"; private.mkdir()
                tools = Tools(private, fail=stage)
                with self.assertRaises(sign.IpaFailure) as caught:
                    sign.perform(environment(root), private, tools, Client)
                self.assertIs(caught.exception.kind, expected)
                self.assertEqual(tools.calls[-1][0], "Delete temporary keychain")
                self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_context_discards_arbitrary_exception_details(self):
        with self.assertRaises(sign.IpaFailure) as caught:
            with sign.ipa_check(sign.IpaFailureKind.BUNDLE):
                raise ValueError(CANARY)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_main_only_emits_exact_exception_and_real_enum(self):
        class Derived(sign.IpaFailure):
            pass
        cases = [(sign.IpaFailure(sign.IpaFailureKind.SIGNATURE), True),
                 (sign.IpaFailure(CANARY), False),
                 (Derived(sign.IpaFailureKind.SIGNATURE), False),
                 (ValueError(CANARY), False)]
        for error, emitted in cases:
            output = io.StringIO()
            with patch.object(sign.os, "environ", {"RUNNER_TEMP": "/unused"}), \
                    patch.object(sign, "configuration"), patch.object(sign.signal, "signal"), \
                    patch.object(sign, "signing_lock", return_value=nullcontext()), \
                    patch.object(sign, "private_directory", return_value=nullcontext("/unused")), \
                    patch.object(sign, "perform", side_effect=error), redirect_stdout(output):
                self.assertEqual(sign.main(), 1)
            self.assertNotIn(CANARY, output.getvalue())
            self.assertEqual("Portable signing IPA diagnostic" in output.getvalue(), emitted)


if __name__ == "__main__":
    unittest.main()
