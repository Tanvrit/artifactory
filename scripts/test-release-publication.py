"""Workflow guard regressions. Synthetic inputs only; never executes a release."""

import ast
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / ".github/workflows/release-android-template.yml"
PUBLIC = {
    "Verify release upload preconditions",
    "Upload to GitHub Release",
    "Mirror to Cloudflare R2",
    "Dispatch manifest update",
}
PLAY = "Upload to Google Play"
PLAY_PREFLIGHT = "Validate explicit Play upload prerequisites"
CORE = {"Build APK + AAB", "Verify APK + AAB release signatures", "Rename + checksum artifacts"}


def enabled(condition, *, publication, play=False, supported=True, credentials=True):
    """Evaluate the boolean subset used by publication guards, with all calls forbidden."""
    values = {
        "inputs.publish_artifacts": publication,
        "inputs.upload_play_store": play,
        "steps.guard.outputs.supported": str(supported).lower(),
        "env.PLAY_SA_PRESENT": str(credentials).lower(),
        "env.ARTIFACTS_TOKEN_PRESENT": str(credentials).lower(),
    }
    text = condition.strip().removeprefix("${{").removesuffix("}}")

    def substitute(match):
        if match[0] not in values:
            raise AssertionError("Unexpected publication guard input: " + match[0])
        return repr(values[match[0]])

    text = re.sub(r"\b(?:inputs|steps|env)\.[A-Za-z0-9_.-]+\b", substitute, text)
    text = text.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(text.strip(), mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.Constant, ast.Compare, ast.Eq,
               ast.NotEq, ast.And, ast.Or, ast.UnaryOp, ast.Not)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError("Unexpected publication guard expression")
    return bool(eval(compile(tree, "<synthetic-publication-guard>", "eval"), {"__builtins__": {}}))


class AndroidPublicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader preserves YAML's 'on' key and scalar strings consistently.
        cls.workflow = yaml.load(ANDROID.read_text(), Loader=yaml.BaseLoader)
        cls.steps = {step.get("name"): step for step in cls.workflow["jobs"]["build-android"]["steps"] if step.get("name")}

    def test_defaults_preserve_existing_release_callers(self):
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["publish_artifacts"]["type"], "boolean")
        self.assertEqual(inputs["publish_artifacts"]["default"], "true")
        self.assertEqual(inputs["upload_play_store"]["type"], "boolean")
        self.assertEqual(inputs["upload_play_store"]["default"], "false")
        for name in PUBLIC | {PLAY}:
            self.assertTrue(enabled(self.steps[name]["if"], publication=True), name)

    def test_validation_builds_and_verifies_with_no_external_publication_even_with_credentials(self):
        for name in PUBLIC | {PLAY, PLAY_PREFLIGHT}:
            self.assertFalse(enabled(self.steps[name]["if"], publication=False), name)
        for name in CORE:
            self.assertTrue(enabled(self.steps[name]["if"], publication=False), name)
        self.assertIn("assembleRelease", self.steps["Build APK + AAB"]["run"])
        self.assertIn("bundleRelease", self.steps["Build APK + AAB"]["run"])
        self.assertRegex(self.steps["Verify APK + AAB release signatures"]["uses"],
                         r"^Tanvrit/artifactory/\.github/actions/verify-android-signatures@[0-9a-f]{40}$")

    def test_signature_action_is_immutable_available_outside_caller_checkout_and_before_publication(self):
        step = self.steps["Verify APK + AAB release signatures"]
        self.assertNotIn("run", step)
        self.assertEqual("${{ steps.guard.outputs.module_dir }}/build/outputs/apk/release", step["with"]["apk-dir"])
        self.assertEqual("${{ steps.guard.outputs.module_dir }}/build/outputs/bundle/release", step["with"]["bundle-dir"])
        action_commit = step["uses"].rsplit("@", 1)[1]
        self.assertRegex(action_commit, r"^[0-9a-f]{40}$")
        self.assertEqual("9bf4b76ba7bb34b5ab2d8b190000f8ffc3762eb1", action_commit)
        helper = ROOT / ".github/actions/verify-android-signatures/verify-android-bundle.py"
        self.assertEqual("d52e43da83d00148a53714cc2c0e10d1e184fbd4bc5cc89494e55cdd80ba1119",
                         hashlib.sha256(helper.read_bytes()).hexdigest())
        names = list(self.steps)
        self.assertLess(names.index("Verify APK + AAB release signatures"), names.index("Rename + checksum artifacts"))
        for name in PUBLIC | {PLAY}:
            self.assertLess(names.index("Verify APK + AAB release signatures"), names.index(name))
        self.assertNotIn("APK Sig Block 42", ANDROID.read_text())
        self.assertNotIn("signature-block scan", ANDROID.read_text())

    def test_optional_expected_certificate_preserves_empty_default_and_reaches_signature_gate(self):
        value = self.workflow["on"]["workflow_call"]["inputs"]["expected_certificate_sha256"]
        self.assertEqual("string", value["type"])
        self.assertEqual("false", value["required"])
        self.assertEqual("", value["default"])
        gate = self.steps["Verify APK + AAB release signatures"]
        self.assertEqual("${{ inputs.expected_certificate_sha256 }}", gate["with"]["expected_certificate_sha256"])
        self.assertTrue(enabled(gate["if"], publication=False))
        self.assertNotIn("expected_certificate_sha256", self.steps["Build APK + AAB"]["env"])

    def test_signature_stage_reporter_cannot_override_verifier_failure_or_publish(self):
        gate = self.steps["Verify APK + AAB release signatures"]
        self.assertEqual("signature_verify", gate["id"])
        self.assertNotIn("continue-on-error", gate)
        reporters = [step for name, step in self.steps.items()
                     if name.startswith("Signature verification status — ")]
        self.assertEqual(1, len(reporters))
        reporter = reporters[0]
        self.assertEqual("always() && steps.guard.outputs.supported == 'true' && "
                         "steps.signature_verify.outcome != 'skipped' && steps.signature_verify.outcome != ''",
                         reporter["if"])
        self.assertEqual(":", reporter["run"])
        self.assertNotIn("continue-on-error", reporter)
        self.assertNotIn("env", reporter)
        names = list(self.steps)
        self.assertEqual(names.index("Verify APK + AAB release signatures") + 1,
                         names.index(reporter["name"]))
        self.assertLess(names.index(reporter["name"]), names.index("Rename + checksum artifacts"))

    def test_signature_metadata_name_accepts_only_source_owned_enums(self):
        name = next(name for name in self.steps if name.startswith("Signature verification status — "))
        match = re.fullmatch(r"Signature verification status — \$\{\{ (.+) \}\}", name)
        self.assertIsNotNone(match)
        source = "steps.signature_verify.outputs.verification_stage"
        branches = re.findall(re.escape(source) + r" == '([a-z-]+)' && '([a-z-]+)'", match.group(1))
        self.assertTrue(branches)
        self.assertEqual(" || ".join([f"{source} == '{key}' && '{literal}'" for key, literal in branches] +
                                    ["'diagnostic_unavailable'"]), match.group(1))
        helper = ROOT / ".github/actions/verify-android-signatures/verify-android-bundle.py"
        tree = ast.parse(helper.read_text())
        enum = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FailureStage")
        known = {node.value.value for node in enum.body if isinstance(node, ast.Assign)}
        allowed = [key for key, literal in branches]
        self.assertTrue(all(key == literal for key, literal in branches))
        self.assertEqual(known | {"success", "apksigner-missing"}, set(allowed))
        self.assertEqual(len(allowed), len(set(allowed)))
        # Actions comparisons ignore case; each matched value must still render
        # a canonical literal, never the source output's original spelling.
        for candidate in allowed + [value.upper() for value in allowed] + ["", "synthetic-private-canary"]:
            label = next((literal for key, literal in branches if candidate.casefold() == key.casefold()),
                         "diagnostic_unavailable")
            self.assertIn(label, set(allowed) | {"diagnostic_unavailable"})
            self.assertNotIn("synthetic-private-canary", label)

    def test_explicit_play_only_upload_never_enables_public_release_or_manifest(self):
        self.assertTrue(enabled(self.steps[PLAY]["if"], publication=False, play=True))
        self.assertFalse(enabled(self.steps[PLAY]["if"], publication=False, play=True, credentials=False))
        self.assertTrue(enabled(self.steps[PLAY_PREFLIGHT]["if"], publication=False, play=True, credentials=False))
        self.assertIn("PLAY_SA_PRESENT", self.steps[PLAY_PREFLIGHT]["run"])
        self.assertIn("exit 1", self.steps[PLAY_PREFLIGHT]["run"])
        self.assertEqual(self.steps[PLAY]["continue-on-error"], "${{ !inputs.upload_play_store }}")
        for name in PUBLIC:
            self.assertFalse(enabled(self.steps[name]["if"], publication=False, play=True), name)

    def test_real_signature_fixtures_are_required_in_existing_selftest_job(self):
        workflow = yaml.load((ROOT / ".github/workflows/selftest-release-publication.yml").read_text(),
                             Loader=yaml.BaseLoader)
        self.assertEqual("read", workflow["permissions"]["contents"])
        self.assertEqual({"publication-guards"}, set(workflow["jobs"]))
        steps = workflow["jobs"]["publication-guards"]["steps"]
        java = next(step for step in steps if step.get("uses", "").startswith("actions/setup-java@"))
        self.assertEqual("21", java["with"]["java-version"])
        fixture_step = next(step for step in steps if "scripts/test-android-bundle-signatures.py" in step.get("run", ""))
        self.assertNotIn("continue-on-error", fixture_step)
        self.assertNotIn("if", fixture_step)
        self.assertIn("set -euo pipefail", fixture_step["run"])
        for script in ("test-release-publication.py", "test-android-verification-action.py",
                       "test-android-bundle-signatures.py"):
            self.assertEqual(1, fixture_step["run"].count("scripts/" + script))
            for event in ("pull_request", "push"):
                self.assertIn("scripts/" + script, workflow["on"][event]["paths"])
        for event in ("pull_request", "push"):
            self.assertIn(".github/actions/verify-android-signatures/**", workflow["on"][event]["paths"])

    def test_normal_pr_smoke_proves_failed_composite_output_and_preserves_original_outcome(self):
        workflow = yaml.load((ROOT / ".github/workflows/selftest-release-publication.yml").read_text(),
                             Loader=yaml.BaseLoader)
        self.assertIn("pull_request", workflow["on"])
        job = workflow["jobs"]["publication-guards"]
        self.assertEqual({"group": "tanvrit", "labels": ["self-hosted", "macOS", "ARM64"]}, job["runs-on"])
        steps = job["steps"]
        fixture = next(step for step in steps if step.get("id") == "signature_metadata_fixture")
        self.assertEqual("true", fixture["continue-on-error"])
        self.assertEqual("./.github/actions/verify-android-signatures", fixture["uses"])
        self.assertNotIn("secrets.", json.dumps(fixture))
        assertion = next(step for step in steps if step.get("name", "").startswith("Signature metadata smoke — "))
        self.assertNotIn("continue-on-error", assertion)
        self.assertEqual("always()", assertion["if"])
        self.assertEqual("${{ steps.signature_metadata_fixture.outcome }}", assertion["env"]["FIXTURE_OUTCOME"])
        self.assertEqual("${{ steps.signature_metadata_fixture.outputs.verification_stage }}",
                         assertion["env"]["FIXTURE_STAGE"])
        self.assertEqual("Signature metadata smoke — ${{ steps.signature_metadata_fixture.outputs.verification_stage "
                         "== 'artifact-discovery' && 'artifact-discovery' || 'diagnostic_unavailable' }}",
                         assertion["name"])
        self.assertIn('"$FIXTURE_OUTCOME" != \'failure\'', assertion["run"])
        self.assertIn('"$FIXTURE_STAGE" != \'artifact-discovery\'', assertion["run"])
        self.assertIn("exit 1", assertion["run"])

    def test_synthetic_metadata_fixture_lifecycle_and_assertions_execute_without_private_tools(self):
        workflow = yaml.load((ROOT / ".github/workflows/selftest-release-publication.yml").read_text(),
                             Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["publication-guards"]["steps"]
        prepare = next(step for step in steps if step.get("id") == "metadata_fixture")
        assertion = next(step for step in steps if step.get("name", "").startswith("Signature metadata smoke — "))
        cleanup = next(step for step in steps if step.get("name") == "Remove job-owned signature metadata fixture")
        self.assertEqual("always()", cleanup["if"])
        action = yaml.load((ROOT / ".github/actions/verify-android-signatures/action.yml").read_text(),
                           Loader=yaml.BaseLoader)
        with tempfile.TemporaryDirectory(prefix="synthetic-metadata-workflow-") as directory:
            temp = Path(directory)
            adjacent = temp / "unrelated-file"
            adjacent.write_text("synthetic-neighbor")
            transport = temp / "prepare-output"
            transport.touch(mode=0o600)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_TEMP": str(temp),
                   "GITHUB_OUTPUT": str(transport)}
            result = subprocess.run(["bash", "-c", prepare["run"]], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode)
            smoke = Path(transport.read_text().removeprefix("root=").strip())
            self.assertEqual(0o700, smoke.stat().st_mode & 0o777)
            transport.write_text("")
            result = subprocess.run(["bash", "-c", action["runs"]["steps"][0]["run"]], cwd=ROOT,
                                    env=env | {"ANDROID_HOME": str(smoke / "sdk"),
                                               "APK_DIR": str(smoke / "apks"), "BUNDLE_DIR": str(smoke / "bundles"),
                                               "EXPECTED_CERTIFICATE_SHA256": "",
                                               "VERIFY_ACTION_PATH": str(ROOT / ".github/actions/verify-android-signatures")},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(1, result.returncode)
            self.assertEqual("verification_stage=artifact-discovery\n", transport.read_text())
            for outcome, stage, expected in [("failure", "artifact-discovery", 0),
                                             ("success", "artifact-discovery", 1),
                                             ("failure", "", 1),
                                             ("failure", "synthetic-private-canary", 1)]:
                result = subprocess.run(["bash", "-c", assertion["run"]], env=env | {
                    "FIXTURE_OUTCOME": outcome, "FIXTURE_STAGE": stage},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(expected, result.returncode)
                self.assertNotIn("synthetic-private-canary", result.stdout + result.stderr)
            result = subprocess.run(["bash", "-c", cleanup["run"]], env=env | {"SMOKE_ROOT": str(smoke)},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode)
            self.assertFalse(smoke.exists())
            self.assertTrue(adjacent.exists())

    def test_unsupported_target_never_builds_or_publishes(self):
        for name in CORE | PUBLIC | {PLAY, PLAY_PREFLIGHT}:
            self.assertFalse(enabled(self.steps[name]["if"], publication=True, play=True, supported=False), name)

    def test_all_current_publication_steps_are_in_guard_inventory(self):
        found = set()
        for name, step in self.steps.items():
            action = step.get("uses", "")
            run = step.get("run", "")
            if (re.search(r"upload|publish|mirror|dispatch", name, re.I)
                    or re.search(r"softprops/action-gh-release|r0adkll/upload-google-play|peter-evans/repository-dispatch|actions/upload-artifact", action)
                    or "--upload-file" in run or "gh release" in run):
                found.add(name)
        self.assertEqual(found, PUBLIC | {PLAY, PLAY_PREFLIGHT})

    def test_credential_cleanup_is_not_disabled_by_validation(self):
        cleanup = self.steps["Remove temporary Firebase configuration"]
        self.assertIn("always()", cleanup["if"])
        self.assertNotIn("inputs.publish_artifacts", cleanup["if"])
        keystore = self.steps["Remove temporary Android signing keystore"]
        self.assertIn("always()", keystore["if"])
        self.assertNotIn("inputs.publish_artifacts", keystore["if"])
        self.assertIn('"$KEYSTORE_PATH"', keystore["run"])
        decode = self.steps["Decode signing keystore"]
        self.assertEqual(decode["id"], "android-keystore")
        self.assertIn("umask 077", decode["run"])
        self.assertIn('mktemp "$RUNNER_TEMP/release-signing.XXXXXX"', decode["run"])
        self.assertEqual(keystore["env"]["KEYSTORE_PATH"], "${{ steps.android-keystore.outputs.path }}")

    def test_synthetic_keystore_is_private_unique_and_cleaned_without_overwriting_an_existing_file(self):
        decode = self.steps["Decode signing keystore"]["run"]
        self.assertNotIn("secrets.", decode)
        cleanup = self.steps["Remove temporary Android signing keystore"]["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "release.keystore"
            existing.write_bytes(b"existing-unrelated-file")
            existing.chmod(0o644)
            env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": directory,
                   "GITHUB_ENV": str(root / "environment"), "GITHUB_OUTPUT": str(root / "output"),
                   "ANDROID_SIGNING_KEYSTORE": base64.b64encode(b"synthetic-key-bytes").decode()}
            result = subprocess.run(["bash", "-c", decode], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in (root / "output").read_text().splitlines())
            created = Path(values["path"])
            self.assertNotEqual(created, existing)
            self.assertEqual(created.read_bytes(), b"synthetic-key-bytes")
            self.assertEqual(created.stat().st_mode & 0o777, 0o600)
            self.assertEqual(existing.read_bytes(), b"existing-unrelated-file")
            env["KEYSTORE_PATH"] = str(created)
            result = subprocess.run(["bash", "-c", cleanup], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(created.exists())
            self.assertTrue(existing.exists())

    def test_signing_values_are_scoped_to_build_environment_without_rendered_secret_scripts(self):
        private = {
            "RELEASE_STORE_PASSWORD": "ANDROID_SIGNING_KEYSTORE_PASSWORD",
            "RELEASE_KEY_ALIAS": "ANDROID_SIGNING_KEY_ALIAS",
            "RELEASE_KEY_PASSWORD": "ANDROID_SIGNING_KEY_PASSWORD",
        }
        build = self.steps["Build APK + AAB"]
        self.assertEqual(build["env"]["RELEASE_STORE_FILE"], "${{ steps.android-keystore.outputs.path }}")
        for key, secret in private.items():
            self.assertEqual(build["env"][key], "${{ secrets." + secret + " }}")
            for name, step in self.steps.items():
                self.assertNotIn("secrets." + secret, step.get("run", ""), name)
                self.assertNotIn("GITHUB_ENV", step.get("run", ""), name)
                if name != "Build APK + AAB":
                    self.assertNotIn(key, step.get("env", {}), name)
        for flag in ("--no-daemon", "--no-configuration-cache", "-Pkotlin.compiler.execution.strategy=in-process"):
            self.assertIn(flag, build["run"])

    def test_private_project_cache_is_scoped_to_owned_temporary_directory(self):
        prepare = self.steps["Prepare private Android project cache"]
        cleanup = self.steps["Remove private Android project cache"]
        self.assertIn("umask 077", prepare["run"])
        self.assertIn('mktemp -d "$RUNNER_TEMP/release-project-cache.XXXXXX"', prepare["run"])
        self.assertIn("always()", cleanup["if"])
        self.assertNotIn("inputs.publish_artifacts", cleanup["if"])
        self.assertEqual(self.steps["Build APK + AAB"]["env"]["ANDROID_PROJECT_CACHE"], "${{ steps.android-project-cache.outputs.path }}")
        self.assertIn('--project-cache-dir "$ANDROID_PROJECT_CACHE"', self.steps["Build APK + AAB"]["run"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / "existing-project-cache"
            unrelated.mkdir()
            result = subprocess.run(["bash", "-c", cleanup["run"]], cwd=root,
                                    env={"PATH": os.environ["PATH"], "RUNNER_TEMP": directory,
                                         "ANDROID_PROJECT_CACHE": str(unrelated)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(unrelated.exists())

    def test_generated_signing_configuration_cleanup_is_always_and_bounded_without_reading_files(self):
        cleanup = self.steps["Remove generated Android signing configuration"]
        self.assertIn("always()", cleanup["if"])
        self.assertNotIn("inputs.publish_artifacts", cleanup["if"])
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            module = root / "composeApp"
            target = module / "build/intermediates/signing_config_data"
            private_file = target / "release/signingConfigWriterRelease/signing-config-data.json"
            private_file.parent.mkdir(parents=True)
            private_file.write_bytes(b"SYNTHETIC private signing fixture; never read by cleanup")
            private_file.chmod(0o000)
            public_file = module / "build/outputs/apk/release/test.apk"
            public_file.parent.mkdir(parents=True)
            public_file.write_bytes(b"unrelated synthetic artifact")
            env = {"PATH": os.environ["PATH"], "MODULE_DIR": "composeApp"}
            result = subprocess.run(["bash", "-c", cleanup["run"]], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(target.exists())
            self.assertTrue(public_file.exists())
            self.assertNotIn("SYNTHETIC private", result.stdout + result.stderr)
            outside_target = Path(outside) / "build/intermediates/signing_config_data"
            outside_target.mkdir(parents=True)
            result = subprocess.run(["bash", "-c", cleanup["run"]], cwd=root,
                                    env=dict(env, MODULE_DIR=outside), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(outside_target.exists())

    def test_invalid_synthetic_keystore_is_reported_without_values_and_owned_file_is_cleanable(self):
        fixture = "%%% invalid SYNTHETIC encoded private input %%%"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": directory,
                   "GITHUB_ENV": str(root / "environment"), "GITHUB_OUTPUT": str(root / "output"),
                   "ANDROID_SIGNING_KEYSTORE": fixture}
            result = subprocess.run(["bash", "-x", "-c", self.steps["Decode signing keystore"]["run"]],
                                    cwd=root, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(fixture, result.stdout + result.stderr)
            self.assertIn("values were withheld", result.stdout)
            created = Path(dict(line.split("=", 1) for line in (root / "output").read_text().splitlines())["path"])
            self.assertEqual(created.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / "environment").exists())
            result = subprocess.run(["bash", "-c", self.steps["Remove temporary Android signing keystore"]["run"]],
                                    cwd=root, env=dict(env, KEYSTORE_PATH=str(created)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(created.exists())

    def test_synthetic_build_receives_literal_private_values_without_persistence_on_success_or_failure(self):
        # Execute only a fake Gradle wrapper with synthetic strings. Shell syntax
        # in the fixture must remain literal, never become generated shell source.
        private = {"RELEASE_STORE_PASSWORD": 'SYNTHETIC $(touch injected) "password"\nsecond line',
                   "RELEASE_KEY_ALIAS": "SYNTHETIC `touch injected` alias",
                   "RELEASE_KEY_PASSWORD": "SYNTHETIC key password"}
        build = self.steps["Build APK + AAB"]["run"]
        build = build.replace("${{ inputs.gradle_module }}", "composeApp").replace("${{ inputs.version }}", "0.0.0")
        decode = self.steps["Decode signing keystore"]["run"]
        cleanup = self.steps["Remove temporary Android signing keystore"]["run"]
        for exit_code in (0, 23):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wrapper = root / "gradlew"
                wrapper.write_text("#!/usr/bin/env python3\nimport os,sys\n"
                                   + "from pathlib import Path\n"
                                   + "assert all(os.environ.get(k) == v for k,v in " + repr(private) + ".items())\n"
                                   + "assert 'ANDROID_SIGNING_KEYSTORE' not in os.environ\n"
                                   + "assert {'--no-daemon','--no-configuration-cache','-Pkotlin.compiler.execution.strategy=in-process'} <= set(sys.argv)\n"
                                   + "cache = Path(sys.argv[sys.argv.index('--project-cache-dir') + 1])\n"
                                   + "assert cache == Path(os.environ['ANDROID_PROJECT_CACHE']) and cache.stat().st_mode & 0o777 == 0o700\n"
                                   + "(cache / 'execution-history-fixture').write_text('SYNTHETIC private history; cleanup must not read it')\n"
                                   + "sys.exit(int(os.environ['SYNTHETIC_EXIT']))\n")
                wrapper.chmod(0o700)
                command_env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": directory,
                               "GITHUB_ENV": str(root / "environment"), "GITHUB_OUTPUT": str(root / "output")}
                result = subprocess.run(["bash", "-x", "-c", decode], cwd=root,
                                        env=dict(command_env, ANDROID_SIGNING_KEYSTORE=base64.b64encode(b"synthetic-key").decode()),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(base64.b64encode(b"synthetic-key").decode(), result.stdout + result.stderr)
                created = Path(dict(line.split("=", 1) for line in (root / "output").read_text().splitlines())["path"])
                result = subprocess.run(["bash", "-c", self.steps["Prepare private Android project cache"]["run"]], cwd=root,
                                        env=dict(command_env, GITHUB_OUTPUT=str(root / "cache-output")), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                cache = Path(dict(line.split("=", 1) for line in (root / "cache-output").read_text().splitlines())["path"])
                result = subprocess.run(["bash", "-x", "-c", build], cwd=root,
                                        env=dict(command_env, RELEASE_STORE_FILE=str(created), ANDROID_PROJECT_CACHE=str(cache), SYNTHETIC_EXIT=str(exit_code), **private),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertFalse((root / "injected").exists())
                self.assertFalse((root / "environment").exists())
                for value in private.values():
                    self.assertNotIn(value, result.stdout + result.stderr + (root / "output").read_text())
                cleanup_result = subprocess.run(["bash", "-c", cleanup], cwd=root,
                                                env=dict(command_env, KEYSTORE_PATH=str(created)), capture_output=True, text=True)
                self.assertEqual(cleanup_result.returncode, 0, cleanup_result.stderr)
                self.assertFalse(created.exists())
                cache_cleanup = subprocess.run(["bash", "-c", self.steps["Remove private Android project cache"]["run"]], cwd=root,
                                               env=dict(command_env, ANDROID_PROJECT_CACHE=str(cache)), capture_output=True, text=True)
                self.assertEqual(cache_cleanup.returncode, 0, cache_cleanup.stderr)
                self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
