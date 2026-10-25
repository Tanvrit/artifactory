"""Workflow guard regressions. Synthetic inputs only; never executes a release."""

import ast
import re
from pathlib import Path
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
CORE = {"Build APK + AAB", "Verify APK is release-signed", "Rename + checksum artifacts"}


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
        self.assertIn("apksigner", self.steps["Verify APK is release-signed"]["run"])

    def test_explicit_play_only_upload_never_enables_public_release_or_manifest(self):
        self.assertTrue(enabled(self.steps[PLAY]["if"], publication=False, play=True))
        self.assertFalse(enabled(self.steps[PLAY]["if"], publication=False, play=True, credentials=False))
        self.assertTrue(enabled(self.steps[PLAY_PREFLIGHT]["if"], publication=False, play=True, credentials=False))
        self.assertIn("PLAY_SA_PRESENT", self.steps[PLAY_PREFLIGHT]["run"])
        self.assertIn("exit 1", self.steps[PLAY_PREFLIGHT]["run"])
        self.assertEqual(self.steps[PLAY]["continue-on-error"], "${{ !inputs.upload_play_store }}")
        for name in PUBLIC:
            self.assertFalse(enabled(self.steps[name]["if"], publication=False, play=True), name)

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


if __name__ == "__main__":
    unittest.main()
