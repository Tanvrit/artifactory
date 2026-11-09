"""Exercise the real optional template step without credentials or deployments."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def browser_step():
    text = (ROOT / '.github/workflows/release-web-template.yml').read_text()
    step = text.split('      - name: Prepare optional public Firebase browser configuration\n', 1)[1]
    step = step.split('      - name: Build Wasm/JS bundle\n', 1)[0]
    script = '\n'.join(line[10:] for line in step.split('        run: |\n', 1)[1].splitlines())
    return step, script


class FirebaseBuildInputsTest(unittest.TestCase):
    def test_firebase_hosting_landing_readiness_is_optional_denied_and_build_only(self):
        text = (ROOT / '.github/workflows/release-firebase-template.yml').read_text()
        definition = text.split('      firebase_web_privacy_ready:\n', 1)[1].split('      assemble_script:', 1)[0]
        self.assertIn('type: boolean', definition)
        self.assertIn('required: false', definition)
        self.assertIn('default: false', definition)
        step = text.split('      - name: Build landing site\n', 1)[1].split('      - name:', 1)[0]
        binding = 'TANVRIT_FIREBASE_WEB_PRIVACY_READY: ${{ inputs.firebase_web_privacy_ready }}'
        self.assertEqual(1, text.count(binding))
        self.assertIn(binding, step)
        self.assertNotIn('secrets.', step)
        script = '\n'.join(line[10:] for line in step.split('        run: |\n', 1)[1].splitlines())
        script = script.replace('${{ inputs.landing_build_dir }}', 'web')
        for ready in ('false', 'true'):
            with self.subTest(ready=ready), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'web').mkdir()
                (root / 'bin').mkdir()
                npm = root / 'bin/npm'
                npm.write_text('#!/bin/sh\nprintf "%s:%s\\n" "$*" "$TANVRIT_FIREBASE_WEB_PRIVACY_READY" >> ../npm-calls\n')
                npm.chmod(0o700)
                env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                           TANVRIT_FIREBASE_WEB_PRIVACY_READY=ready)
                result = subprocess.run(['bash', '-c', script], cwd=root, env=env,
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([f'ci:{ready}', f'run build:{ready}'],
                                 (root / 'npm-calls').read_text().splitlines())

    def execute(self, root, config='', ready='false'):
        env = dict(os.environ, TANVRIT_FIREBASE_WEB_CONFIG_JSON=config,
                   TANVRIT_FIREBASE_WEB_PRIVACY_READY=ready)
        return subprocess.run(['bash', '-c', browser_step()[1]], cwd=root, env=env,
                              text=True, capture_output=True, timeout=10)

    def test_default_empty_inputs_preserve_callers_without_a_validator(self):
        with tempfile.TemporaryDirectory() as root:
            result = self.execute(root)
            self.assertEqual(result.returncode, 0)

    def test_opt_in_without_validator_refuses_instead_of_silently_skipping(self):
        with tempfile.TemporaryDirectory() as root:
            for config, ready in [('PUBLIC_CONFIG_CANARY', 'false'), ('', 'true')]:
                result = self.execute(root, config, ready)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('PUBLIC_CONFIG_CANARY', result.stdout + result.stderr)

    def test_validator_runs_with_empty_denied_inputs_to_clear_stale_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'scripts').mkdir()
            (root / 'old-ready.json').write_text('{"privacyReady":true}')
            (root / 'scripts/prepare-telemetry-web.mjs').write_text(
                "import fs from 'node:fs'; fs.rmSync('old-ready.json');")
            result = self.execute(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / 'old-ready.json').exists())

    def test_raw_json_is_environment_data_and_never_shell_code(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'scripts').mkdir()
            (root / 'scripts/prepare-telemetry-web.mjs').write_text(
                "import fs from 'node:fs'; fs.writeFileSync('received', process.env.TANVRIT_FIREBASE_WEB_CONFIG_JSON);")
            value = '{"public":"$(touch INJECTION_MARKER)`touch INJECTION_MARKER`","canary":"PUBLIC_CONFIG_CANARY"}'
            result = self.execute(root, value)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'received').read_text(), value)
            self.assertFalse((root / 'INJECTION_MARKER').exists())
            self.assertNotIn('PUBLIC_CONFIG_CANARY', result.stdout + result.stderr)

    def test_invalid_caller_configuration_blocks_the_build(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'scripts').mkdir()
            (root / 'scripts/prepare-telemetry-web.mjs').write_text('process.exitCode = 7;')
            self.assertEqual(self.execute(root, '{}').returncode, 7)

    def test_native_inputs_are_denied_by_default_and_do_not_add_credentials(self):
        for platform in ['android', 'ios']:
            text = (ROOT / f'.github/workflows/release-{platform}-template.yml').read_text()
            block = text.split('      firebase_privacy_ready:\n', 1)[1].split('      firebase_messaging:', 1)[0]
            self.assertIn('type: boolean', block)
            self.assertIn('default: false', block)
            self.assertIn('TANVRIT_FIREBASE_NATIVE_PRIVACY_READY: ${{ inputs.firebase_privacy_ready }}', text)
        step, _ = browser_step()
        self.assertIn("if: steps.guard.outputs.supported == 'true'", step)
        self.assertNotIn('secrets.', step)
        self.assertNotIn('${{ inputs.firebase_web_config_json }}', browser_step()[1])


if __name__ == '__main__':
    unittest.main()
