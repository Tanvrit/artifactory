"""Synthetic fixed-label transport and lifecycle failures; no account/tool access."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from diagnostics import CleanupStage, Diagnostics, Stage
import provision
from test_sign import Client, Tools, environment, sign


ROOT = Path(__file__).resolve().parents[3]
CANARY = "SYNTHETIC-PRIVATE-ERROR-MUST-NOT-ESCAPE"


class DiagnosticTest(unittest.TestCase):
    def run_main(self, root, failure=None, output_directory=False):
        env = environment(root)
        output = root / "github-output"
        output.mkdir() if output_directory else output.touch(mode=0o600)
        env["GITHUB_OUTPUT"] = str(output)
        original = sign.perform

        def perform(env, private, diagnostics):
            tools = Tools(private, fail=failure)
            original(env, private, tools, Client, diagnostics)

        stdout = io.StringIO()
        with patch.object(sign.os, "environ", env), patch.object(sign.Path, "home", return_value=root / "home"), \
                patch.object(sign.signal, "signal"), patch.object(sign, "perform", side_effect=perform), redirect_stdout(stdout):
            result = sign.main()
        values = {} if output_directory else dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertFalse(list(root.glob("portable-ios-*")))
        self.assertFalse(list((root / "home").rglob("*.mobileprovision")))
        return result, values, stdout.getvalue()

    def test_main_success_only_reports_completed_after_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            result, values, stdout = self.run_main(Path(d))
            self.assertEqual(result, 0)
            self.assertEqual(values, {"stage": "completed", "cleanup-stage": "completed"})
            self.assertNotIn(CANARY, stdout)

    def test_each_tool_failure_keeps_nonzero_and_fixed_boundary_after_cleanup(self):
        cases = {
            "Validate App Store key": "api-key-validation",
            "Check certificate team": "certificate-validation",
            "Resolve application identity": "xcode-identity",
            "Authenticate provisioning request": "api-authentication",
            "Validate app-specific provisioning profile": "profile-validation",
            "Set keychain timeout": "keychain-setup",
            "Archive application": "archive",
            "Export signed IPA": "export",
            "Verify exported code signature": "ipa-validation",
        }
        for failure, expected in cases.items():
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                result, values, stdout = self.run_main(Path(d), failure)
                self.assertEqual(result, 1)
                self.assertEqual(values, {"stage": expected, "cleanup-stage": "completed"})
                self.assertEqual(stdout, "::error::Portable iOS signing failed; diagnostics withheld.\n")

    def test_primary_and_cleanup_failure_are_independent_and_all_finally_blocks_run(self):
        class DoubleFailure(Tools):
            def __call__(self, stage, args, env, timeout=180):
                if stage in ("Archive application", "Read cleanup search list"):
                    self.calls.append((stage, args, env))
                    raise sign.SigningError(CANARY)
                return super().__call__(stage, args, env, timeout)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d); diagnostics = Diagnostics()
            with self.assertRaises(sign.SigningError):
                with sign.private_directory(diagnostics, root) as private:
                    tools = DoubleFailure(Path(private))
                    sign.perform(environment(root), Path(private), tools, Client, diagnostics)
            self.assertEqual(diagnostics.values(), ("archive", "search-list"))
            self.assertEqual(tools.calls[-1][0], "Delete temporary keychain")
            self.assertFalse(list(root.glob("portable-ios-*")))
            self.assertFalse(list((root / "home").rglob("*.mobileprovision")))

    def test_cleanup_only_failure_is_not_mislabeled_as_last_successful_operation(self):
        with tempfile.TemporaryDirectory() as d:
            result, values, stdout = self.run_main(Path(d), "Read cleanup search list")
            self.assertEqual(result, 1)
            self.assertEqual(values, {"stage": "cleanup", "cleanup-stage": "search-list"})
            self.assertNotIn("SigningError", stdout)

    def test_private_directory_cleanup_failure_has_a_separate_fixed_label(self):
        class Directory:
            name = "SYNTHETIC-NONEXISTENT"

            def cleanup(self):
                raise OSError(CANARY)

        diagnostics = Diagnostics(); diagnostics.begin(Stage.ARCHIVE)
        with patch.object(sign.tempfile, "TemporaryDirectory", return_value=Directory()):
            with self.assertRaises(OSError):
                with sign.private_directory(diagnostics, "/SYNTHETIC"):
                    raise sign.SigningError(CANARY)
        self.assertEqual(diagnostics.values(), ("archive", "private-directory"))

    def test_output_transport_failure_never_changes_original_exit_or_emits_exception(self):
        for failure, expected_exit in ((None, 0), ("Archive application", 1)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                result, values, stdout = self.run_main(Path(d), failure, output_directory=True)
                self.assertEqual(result, expected_exit)
                self.assertEqual(values, {})
                self.assertIn("::warning::Portable iOS diagnostic stage: output-transport\n", stdout)
                self.assertNotIn("IsADirectoryError", stdout)
                self.assertNotIn(str(d), stdout)

    def test_arbitrary_exception_and_signal_message_are_never_rendered(self):
        for interruption in (False, True):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as d:
                root = Path(d); env = environment(root); output = root / "output"; output.touch()
                env["GITHUB_OUTPUT"] = str(output)
                handlers = {}

                def fail(_env, _private, diagnostics):
                    diagnostics.begin(Stage.ARCHIVE)
                    if interruption:
                        handlers[sign.signal.SIGTERM](None, None)
                    raise RuntimeError(CANARY)

                stdout = io.StringIO()
                with patch.object(sign.os, "environ", env), patch.object(sign.Path, "home", return_value=root / "home"), \
                        patch.object(sign.signal, "signal", side_effect=lambda key, value: handlers.update({key: value})), \
                        patch.object(sign, "perform", side_effect=fail), redirect_stdout(stdout):
                    self.assertEqual(sign.main(), 1)
                self.assertEqual(stdout.getvalue(), "::error::Portable iOS signing failed; diagnostics withheld.\n")
                self.assertEqual(output.read_text(), "stage=archive\ncleanup-stage=completed\n")

    def test_only_enum_values_can_cross_output_transport(self):
        diagnostics = Diagnostics()
        with self.assertRaises(TypeError):
            diagnostics.begin(CANARY)
        diagnostics.current = CANARY; diagnostics.cleanup = CANARY
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "output"; output.touch()
            diagnostics.publish({"GITHUB_OUTPUT": str(output)})
            self.assertEqual(output.read_text(), "stage=unknown\ncleanup-stage=unknown\n")

    def test_output_exception_payload_and_missing_output_are_not_rendered(self):
        for env in ({}, {"GITHUB_OUTPUT": "SYNTHETIC"}):
            stdout = io.StringIO()
            with patch("diagnostics.os.open", side_effect=OSError(CANARY)), redirect_stdout(stdout):
                Diagnostics().publish(env)
            self.assertEqual(stdout.getvalue(), "::warning::Portable iOS diagnostic stage: output-transport\n")

    def test_unexpected_output_failure_preserves_prior_success_and_failure(self):
        original_open = os.open
        for failure, expected in ((None, 0), ("Archive application", 1)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                root = Path(d)

                def fail_output(path, *args, **kwargs):
                    if str(path) == str(root / "github-output") and args[0] & os.O_APPEND:
                        raise RuntimeError(CANARY)
                    return original_open(path, *args, **kwargs)

                with patch("diagnostics.os.open", side_effect=fail_output):
                    result, values, stdout = self.run_main(root, failure)
                self.assertEqual(result, expected)
                self.assertEqual(values, {})
                self.assertNotIn(CANARY, stdout)
                self.assertNotIn("RuntimeError", stdout)
                self.assertTrue(stdout.endswith("::warning::Portable iOS diagnostic stage: output-transport\n"))

    def test_transport_warning_is_also_best_effort_without_exception_payload(self):
        with patch("diagnostics.os.open", side_effect=RuntimeError(CANARY)), \
                patch("builtins.print", side_effect=BrokenPipeError(CANARY)):
            Diagnostics().publish({"GITHUB_OUTPUT": "SYNTHETIC"})

    def test_existing_certificate_must_match_before_any_bundle_or_profile_operation(self):
        calls = []

        class MissingCertificate(Client):
            def collection(self, path, **query):
                calls.append(path)
                return []

            def request(self, path, *, body=None):
                raise AssertionError("No mutation is allowed without the existing matching certificate")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d); diagnostics = Diagnostics()
            with self.assertRaises(provision.ProvisioningError):
                provision.application_profile(MissingCertificate("SYNTHETIC"), b"SYNTHETIC-CERT", "ABCDEFGHIJ",
                                              "com.example.test", root, Tools(root), {}, {}, diagnostics)
            diagnostics.failed()
            self.assertEqual(calls, ["certificates"])
            self.assertEqual(diagnostics.values(), ("registered-certificate-lookup", "not-started"))

    def test_api_failures_label_source_boundary_without_claiming_write_confirmation(self):
        targets = {
            "certificates": Stage.REGISTERED_CERTIFICATE,
            "bundleIds": Stage.BUNDLE_LOOKUP,
            "bundleIds/BUNDLE1/bundleIdCapabilities": Stage.CAPABILITY_LOOKUP,
            "bundleIds/BUNDLE1/profiles": Stage.PROFILE_LOOKUP,
            "profiles": Stage.PROFILE_CREATE,
            "bundleIds:post": Stage.BUNDLE_CREATE,
            "bundleIdCapabilities": Stage.CAPABILITY_ENABLE,
        }
        for target, expected in targets.items():
            class API(Client):
                def collection(self, path, **query):
                    if path == target:
                        raise provision.ProvisioningError(CANARY)
                    if target == "bundleIds:post" and path == "bundleIds":
                        return []
                    if path.endswith("/bundleIdCapabilities") and target != "bundleIdCapabilities":
                        return [{"attributes": {"capabilityType": "PUSH_NOTIFICATIONS"}}]
                    return super().collection(path, **query)

                def request(self, path, *, body=None):
                    if path == target or (target == "bundleIds:post" and path == "bundleIds"):
                        raise provision.ProvisioningError(CANARY)
                    return super().request(path, body=body)

            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                diagnostics = Diagnostics(); root = Path(d)
                with self.assertRaises(provision.ProvisioningError):
                    provision.application_profile(API("SYNTHETIC"), b"SYNTHETIC-CERT", "ABCDEFGHIJ", "com.example.test",
                                                  root, Tools(root), {}, {"aps-environment": "development"}, diagnostics)
                diagnostics.failed()
                self.assertEqual(diagnostics.values()[0], expected.value)
                if expected in (Stage.PROFILE_CREATE, Stage.BUNDLE_CREATE, Stage.CAPABILITY_ENABLE):
                    self.assertTrue(expected.value.endswith("-attempt"))

    def test_framework_labels_preserve_original_link_commands_and_failure_status(self):
        template = (ROOT / ".github/workflows/release-ios-template.yml").read_text()
        fragment = template.split("      - name: Build KMP framework\n", 1)[1].split("      # Fixed labels", 1)[0]
        script = textwrap.dedent(fragment.split("        run: |\n", 1)[1]).replace("${{ inputs.gradle_module }}", "composeApp")
        for task, failing, expected in (("pod", False, "completed"), ("plain", False, "completed"),
                                        ("none", False, "deferred-to-xcode"), ("pod", True, "pod-framework-link"),
                                        ("plain", True, "plain-framework-link")):
            with self.subTest(task=task, failing=failing), tempfile.TemporaryDirectory() as d:
                root = Path(d); output = root / "output"; output.touch()
                gradle = root / "gradlew"
                available = "linkPodReleaseFrameworkIosArm64" if task == "pod" else "linkReleaseFrameworkIosArm64" if task == "plain" else ""
                gradle.write_text("#!/bin/bash\nif [[ $1 == ':composeApp:tasks' ]]; then\n"
                                  + "  echo '" + available + "'\nelse\n"
                                  + "  [[ $2 == '--no-daemon' ]] || exit 88\n"
                                  + "  exit " + ("31" if failing else "0") + "\nfi\n")
                gradle.chmod(0o700)
                result = subprocess.run(["bash", "-c", script], cwd=root,
                                        env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output)}, capture_output=True)
                self.assertEqual(result.returncode, 31 if failing else 0)
                self.assertEqual(output.read_text().splitlines()[-1], "stage=" + expected)

    def test_metadata_labels_are_static_exact_guarded_and_never_continue_on_error(self):
        template = (ROOT / ".github/workflows/release-ios-template.yml").read_text()
        for prefix, step, key, values in (
                ("Portable signing stage", "portable-signing", "stage", [v.value for v in Stage]),
                ("Portable cleanup stage", "portable-signing", "cleanup-stage", [v.value for v in CleanupStage])):
            for value in values:
                marker = f'      - name: "{prefix}: {value}"\n'
                fragment = template.split(marker, 1)[1].split("\n\n", 1)[0]
                self.assertIn("always()", fragment)
                self.assertIn(f"steps.{step}.outputs.{key} == '{value}'", fragment)
                self.assertTrue(fragment.endswith("        run: ':'"))
            self.assertIn(f'      - name: "{prefix}: unknown or unreported"', template)
        signing = template.split("      - name: Portable certificate/API-key signing\n", 1)[1].split("      # Static labels", 1)[0]
        self.assertNotIn("continue-on-error", signing)
        self.assertNotRegex(template, r'- name:.*\$\{\{.*outputs')
        action = (ROOT / ".github/actions/portable-ios-signing/action.yml").read_text()
        self.assertIn("value: ${{ steps.sign.outputs.stage }}", action)
        self.assertIn("value: ${{ steps.sign.outputs.cleanup-stage }}", action)
        self.assertNotIn("continue-on-error", action)

    def test_empty_fixture_stops_before_home_lock_private_files_or_tools(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); output = root / "output"; output.touch()
            env = environment(root); env.update({key: "" for key in sign.SECRET_NAMES})
            env["GITHUB_OUTPUT"] = str(output)
            stdout = io.StringIO()
            with patch.object(sign.os, "environ", env), patch.object(sign.signal, "signal"), \
                    patch.object(sign.Path, "home", side_effect=AssertionError("Fixture must not access HOME")), \
                    patch.object(sign, "perform", side_effect=AssertionError("Fixture must not perform signing")), \
                    patch.object(sign, "signing_lock", side_effect=AssertionError("Fixture must not acquire lock")), \
                    redirect_stdout(stdout):
                self.assertEqual(sign.main(), 1)
            self.assertEqual(output.read_text(), "stage=configuration\ncleanup-stage=not-started\n")
            self.assertEqual(list(root.iterdir()), [output])
            self.assertEqual(stdout.getvalue(), "::error::Portable iOS signing failed; diagnostics withheld.\n")

    def test_only_negative_fixture_relaxes_failure_and_asserts_fixed_outputs(self):
        workflow = (ROOT / ".github/workflows/selftest-portable-ios-signing.yml").read_text()
        self.assertEqual(workflow.count("continue-on-error: true"), 1)
        fixture = workflow.split("      - name: Reject empty synthetic signing inputs\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("uses: ./.github/actions/portable-ios-signing", fixture)
        self.assertIn("continue-on-error: true", fixture)
        self.assertIn("upload-testflight: 'false'", fixture)
        for name in sign.SECRET_NAMES:
            self.assertIn(f"          {name}: ''\n", fixture)
        self.assertNotIn("${{ secrets.", workflow)
        self.assertIn('actual != ("failure", "configuration", "not-started")', workflow)
        self.assertIn('name: "Portable iOS diagnostic fixture: configuration; cleanup not-started"', workflow)
        script = textwrap.dedent(workflow.rsplit("        run: |\n", 1)[1])
        for values, expected in ((("failure", "configuration", "not-started"), 0),
                                 (("success", "configuration", "not-started"), 1),
                                 (("failure", CANARY, "not-started"), 1), (("", "", ""), 1)):
            env = dict(zip(("FIXTURE_OUTCOME", "FIXTURE_STAGE", "FIXTURE_CLEANUP_STAGE"), values))
            env["PATH"] = os.environ["PATH"]
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            self.assertNotIn(CANARY, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "Portable iOS diagnostic fixture " + ("passed.\n" if not expected else "failed.\n"))


if __name__ == "__main__":
    unittest.main()
