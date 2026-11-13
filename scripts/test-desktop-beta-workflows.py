"""Native workflow and signing guard checks. Synthetic inputs only; no releases."""
import subprocess
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


class DesktopBetaWorkflowTest(unittest.TestCase):
    def test_opt_in_defaults_keep_existing_fleet_contracts(self):
        for os, architecture in [('linux', 'X64'), ('macos', 'ARM64'), ('windows', None)]:
            with self.subTest(os=os):
                workflow = yaml.load((ROOT / f'.github/workflows/release-{os}-template.yml').read_text(), Loader=yaml.BaseLoader)
                inputs = workflow['on']['workflow_call']['inputs']
                self.assertEqual(inputs['release_channel']['default'], 'stable')
                self.assertEqual(inputs['source_commit']['default'], '')
                self.assertEqual(inputs['expected_build_code']['default'], '')
                job = workflow['jobs']['build-macos-arm64' if os == 'macos' else 'build-' + os]
                labels = job['runs-on']['labels']
                if architecture:
                    self.assertIn(architecture, labels)
                self.assertIn('self-hosted', labels)
                steps = {step.get('name'): step for step in job['steps'] if step.get('name')}
                verify = steps['Verify exact Jashva beta source and native build']
                publish = steps['Publish qualified immutable Jashva beta']
                for step in (verify, publish):
                    self.assertIn("inputs.release_channel == 'beta'", step['if'])
                    self.assertNotIn('continue-on-error', step)
                self.assertEqual(verify['with']['phase'], 'verify')
                self.assertEqual(publish['with']['phase'], 'publish')
                for name, step in steps.items():
                    if name.startswith(('Upload to GitHub Release', 'Mirror to Cloudflare R2', 'Dispatch manifest update')):
                        self.assertIn("inputs.release_channel != 'beta'", step['if'], name)
                if os == 'macos':
                    self.assertIn("inputs.release_channel != 'beta'", steps['Upload to App Store Connect (xcrun altool)']['if'])
                    self.assertIn('xcrun notarytool submit', steps['Notarize + staple DMG']['run'])
                    self.assertIn('xcrun stapler staple', steps['Notarize + staple DMG']['run'])
                text = (ROOT / f'.github/workflows/release-{os}-template.yml').read_text()
                self.assertIn("inputs.release_channel == 'beta' && '-PdesktopChannel=beta' || ''", text)
                self.assertIn("inputs.release_channel == 'beta' && '0' || '1'", text)

    def test_windows_beta_fails_without_native_signature_and_timestamp(self):
        script = (ROOT / '.github/actions/desktop-beta/verify-windows.ps1').read_text()
        for token in ("$signature.Status -ne 'Valid'", '$signature.TimeStamperCertificate',
                      '$timestampChain.Build', 'verify /pa /all /tw', '$LASTEXITCODE -ne 0'):
            self.assertIn(token, script)
        workflow = yaml.load((ROOT / '.github/workflows/release-windows-template.yml').read_text(), Loader=yaml.BaseLoader)
        steps = {step.get('name'): step for step in workflow['jobs']['build-windows']['steps'] if step.get('name')}
        self.assertEqual(steps['Verify Jashva MSI signature and trusted timestamp']['if'],
                         "steps.guard.outputs.supported == 'true' && inputs.product == 'jashva' && inputs.release_channel == 'beta'")
        self.assertEqual(steps['Sign Windows MSI']['env']['JASHVA_STRICT_SIGNATURE'],
                         "${{ inputs.product == 'jashva' && inputs.release_channel == 'beta' && 'true' || 'false' }}")
        action = (ROOT / '.github/actions/desktop-beta/index.cjs').read_text()
        self.assertIn('WINDOWS_SIGNING_CERT && process.env.WINDOWS_SIGNING_PASSWORD', action)
        self.assertIn('verifyNative(contract, current, source)', action)

    def test_channel_rejection_executes_without_credentials(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        step = next(step for step in workflow['jobs']['build-linux']['steps'] if step.get('name') == 'Validate desktop release channel')
        for product, channel, expected in [('friendly', 'stable', 0), ('jashva', 'beta', 0), ('friendly', 'beta', 1), ('jashva', 'arbitrary', 1)]:
            result = subprocess.run(['bash', '-c', step['run']], env={'PRODUCT': product, 'RELEASE_CHANNEL': channel}, capture_output=True)
            self.assertEqual(expected, result.returncode)

    def test_manifest_update_keeps_beta_separate_and_does_not_cancel_inflight_update(self):
        workflow = yaml.load((ROOT / '.github/workflows/update-manifest.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], 'false')
        self.assertIn("release_channel != 'beta'", workflow['jobs']['update']['if'])
        beta = workflow['jobs']['update-beta']
        self.assertEqual(beta['runs-on']['labels'], ['self-hosted', 'Linux', 'ARM64'])
        for step in beta['steps']:
            self.assertNotIn('continue-on-error', step)
        self.assertNotIn('manifests/catalog.json', str(beta))
        self.assertIn('git fetch origin main', str(beta))
        self.assertIn('source_commit', (ROOT / '.github/actions/desktop-beta/manifest.cjs').read_text())


if __name__ == '__main__':
    unittest.main()
