"""Synthetic compiler output only: no signing material or actual Xcode invocation."""

from contextlib import nullcontext, redirect_stdout
import io
import unittest
from unittest.mock import Mock, patch

from test_sign import sign
from xcode_diagnostics import XcodeFailureKind, classify


CANARY = b"SYNTHETIC-PRIVATE-OUTPUT-MUST-NOT-ESCAPE"


class XcodeDiagnosticTest(unittest.TestCase):
    def test_captured_output_becomes_only_a_fixed_hint(self):
        samples = {
            b"FirebaseCore_Privacy does not support provisioning profiles": XcodeFailureKind.PROFILE_ON_NON_APP_TARGET,
            b"Target requires a provisioning profile": XcodeFailureKind.PROFILE_MISSING,
            b"Profile doesn't include signing certificate": XcodeFailureKind.PROFILE_MISMATCH,
            b"errSecInternalComponent": XcodeFailureKind.CODE_SIGNING,
            b"Could not resolve package dependencies": XcodeFailureKind.PACKAGE_RESOLUTION,
            b"Undefined symbols for architecture arm64": XcodeFailureKind.LINKER,
            b"Multiple commands produce an output": XcodeFailureKind.DUPLICATE_OUTPUT,
            b"error: no such module": XcodeFailureKind.SOURCE_COMPILATION,
            b"Unable to find a destination": XcodeFailureKind.TOOLCHAIN,
            CANARY: XcodeFailureKind.UNKNOWN,
        }
        for sample, expected in samples.items():
            with self.subTest(kind=expected):
                process = Mock(returncode=65)
                process.communicate.return_value = (CANARY, sample + CANARY)
                output = io.StringIO()
                with patch.object(sign.subprocess, "Popen", return_value=process), redirect_stdout(output):
                    with self.assertRaises(sign.XcodeFailure) as caught:
                        sign.run("Archive application", ["xcodebuild", CANARY.decode()], {})
                self.assertIs(caught.exception.kind, expected)
                self.assertNotIn(CANARY.decode(), str(caught.exception))
                self.assertEqual(output.getvalue(), "")

    def test_main_emits_only_a_real_enum_from_exact_exception_type(self):
        for kind in (XcodeFailureKind.CODE_SIGNING, CANARY.decode()):
            output = io.StringIO()
            with patch.object(sign.os, "environ", {"RUNNER_TEMP": "/unused"}), \
                    patch.object(sign, "configuration"), patch.object(sign.signal, "signal"), \
                    patch.object(sign, "signing_lock", return_value=nullcontext()), \
                    patch.object(sign, "private_directory", return_value=nullcontext("/unused")), \
                    patch.object(sign, "perform", side_effect=sign.XcodeFailure(kind)), redirect_stdout(output):
                self.assertEqual(sign.main(), 1)
            self.assertNotIn(CANARY.decode(), output.getvalue())
            self.assertEqual("Portable signing Xcode diagnostic" in output.getvalue(),
                             kind is XcodeFailureKind.CODE_SIGNING)

    def test_diagnostic_is_not_applied_to_other_tools_or_successes(self):
        for executable, stage, status in (("security", "Archive application", 1),
                                          ("xcodebuild", "Resolve application identity", 1),
                                          ("xcodebuild", "Archive application", 0)):
            process = Mock(returncode=status)
            process.communicate.return_value = (CANARY, b"errSecInternalComponent")
            with patch.object(sign.subprocess, "Popen", return_value=process):
                if status:
                    with self.assertRaises(sign.SigningError) as caught:
                        sign.run(stage, [executable], {})
                    self.assertIs(type(caught.exception), sign.SigningError)
                else:
                    self.assertEqual(sign.run(stage, [executable], {}), CANARY)

    def test_only_bounded_tails_are_classified(self):
        prefix = b"errSecInternalComponent" + b" " * 1048576
        self.assertIs(classify(prefix, prefix), XcodeFailureKind.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
