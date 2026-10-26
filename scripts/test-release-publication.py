"""Workflow guard regressions. Synthetic inputs only; never executes a release."""

import ast
import base64
import hashlib
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
        self.assertEqual("e1065305ec9b02c9de35bc83a83314de2b0e9bf8", action_commit)
        helper = ROOT / ".github/actions/verify-android-signatures/verify-android-bundle.py"
        self.assertEqual("a10ac8274ef34fbefa7723ead56079a19098df33f79bd05a3c1becbef89ba10e",
                         hashlib.sha256(helper.read_bytes()).hexdigest())
        names = list(self.steps)
        self.assertLess(names.index("Verify APK + AAB release signatures"), names.index("Rename + checksum artifacts"))
        for name in PUBLIC | {PLAY}:
            self.assertLess(names.index("Verify APK + AAB release signatures"), names.index(name))
        self.assertNotIn("APK Sig Block 42", ANDROID.read_text())
        self.assertNotIn("signature-block scan", ANDROID.read_text())

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
