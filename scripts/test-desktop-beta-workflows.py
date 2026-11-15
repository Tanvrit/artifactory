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
                if os == 'linux':
                    self.assertEqual(inputs['architecture']['default'], 'x64')
                    self.assertEqual(inputs['publish_artifacts']['default'], 'true')
                    self.assertEqual(labels[-1], "${{ inputs.architecture == 'arm64' && 'ARM64' || 'X64' }}")
                elif architecture:
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

    def test_linux_public_branding_uses_only_ephemeral_read_credentials(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['build-linux']['steps']
        branding = [step for step in steps if step.get('with', {}).get('path') == '_branding']
        self.assertEqual(len(branding), 1)
        checkout = branding[0]
        self.assertEqual(checkout['if'], "steps.guard.outputs.supported == 'true'")
        self.assertNotIn('continue-on-error', checkout)
        self.assertNotIn('env', checkout)
        self.assertEqual(checkout['with']['repository'], 'Tanvrit/artifactory')
        self.assertEqual(checkout['with']['token'], '${{ github.token }}')
        self.assertEqual(checkout['with']['persist-credentials'], 'false')
        self.assertEqual(checkout['with']['sparse-checkout'], 'branding/icons/dist/${{ inputs.product }}')

    def test_linux_publication_still_requires_separate_write_credential(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        steps = {step.get('name'): step for step in workflow['jobs']['build-linux']['steps'] if step.get('name')}
        preflight = steps['Verify release upload preconditions']
        self.assertIn('inputs.publish_artifacts', preflight['if'])
        self.assertEqual(preflight['env']['ARTIFACTS_REPO_TOKEN'], '${{ secrets.ARTIFACTS_REPO_TOKEN }}')
        self.assertIn('ARTIFACTS_TOKEN_PRESENT', preflight['run'])
        self.assertEqual(steps['Upload to GitHub Release (DEB)']['with']['token'], '${{ secrets.ARTIFACTS_REPO_TOKEN }}')
        publish = steps['Publish qualified immutable Jashva beta']
        self.assertIn('inputs.publish_artifacts', publish['if'])
        self.assertEqual(publish['env']['ARTIFACTS_REPO_TOKEN'], '${{ secrets.ARTIFACTS_REPO_TOKEN }}')

    def test_private_retention_fallback_requires_qualification_and_verified_backend(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        steps = {step.get('name'): step for step in workflow['jobs']['build-linux']['steps'] if step.get('name')}
        qualification = steps['Qualify private ARM64 beta candidate']
        archive = steps['Retain private ARM64 beta candidate']
        fallback = steps['Retain private ARM64 candidate in calling repository']
        guard = steps['Require verified private ARM64 candidate retention']
        self.assertEqual(qualification['id'], 'private_qualification')
        self.assertEqual(archive['id'], 'private_archive')
        self.assertEqual(archive['continue-on-error'], 'true')
        self.assertEqual(archive['with']['retention-days'], '7')
        for step in (archive, fallback, guard):
            self.assertIn("steps.private_qualification.outcome == 'success'", step['if'])
            self.assertIn('!inputs.publish_artifacts', step['if'])
        self.assertIn('!cancelled()', fallback['if'])
        self.assertIn("steps.private_archive.outcome == 'failure'", fallback['if'])
        self.assertNotIn('continue-on-error', fallback)
        self.assertEqual(fallback['with']['phase'], 'retain-private')
        self.assertEqual(fallback['with']['publish_artifacts'], 'false')
        self.assertEqual(fallback['env'], {'GITHUB_TOKEN': '${{ github.token }}'})
        self.assertIn('always()', guard['if'])
        self.assertNotIn('continue-on-error', guard)
        self.assertEqual(guard['env']['ARCHIVE_OUTCOME'], '${{ steps.private_archive.outcome }}')
        self.assertEqual(guard['env']['DRAFT_VERIFIED'], '${{ steps.private_draft.outputs.private_retained }}')
        for archive_outcome, draft_verified, expected in [('success', '', 0), ('failure', 'true', 0),
                                                        ('failure', '', 1), ('failure', 'false', 1), ('skipped', '', 1)]:
            result = subprocess.run(['bash', '-c', guard['run']], capture_output=True,
                                    env={'ARCHIVE_OUTCOME': archive_outcome, 'DRAFT_VERIFIED': draft_verified})
            self.assertEqual(result.returncode, expected)

    def test_channel_rejection_executes_without_credentials(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        step = next(step for step in workflow['jobs']['build-linux']['steps'] if step.get('name') == 'Validate desktop release channel')
        for product, channel, expected in [('friendly', 'stable', 0), ('jashva', 'beta', 0), ('friendly', 'beta', 1), ('jashva', 'arbitrary', 1)]:
            result = subprocess.run(['bash', '-c', step['run']], env={'PRODUCT': product, 'RELEASE_CHANNEL': channel, 'ARCHITECTURE': 'x64', 'PUBLISH_ARTIFACTS': 'true'}, capture_output=True)
            self.assertEqual(expected, result.returncode)

    def test_arm64_is_beta_deb_only_and_private_candidate_has_no_publication(self):
        workflow = yaml.load((ROOT / '.github/workflows/release-linux-template.yml').read_text(), Loader=yaml.BaseLoader)
        steps = {step.get('name'): step for step in workflow['jobs']['build-linux']['steps'] if step.get('name')}
        validation = steps['Validate desktop release channel']['run']
        for product, channel, arch, publish, expected in [
            ('jashva', 'beta', 'arm64', 'false', 0), ('jashva', 'beta', 'arm64', 'true', 0),
            ('jashva', 'stable', 'arm64', 'true', 1), ('friendly', 'beta', 'arm64', 'true', 1),
            ('jashva', 'beta', 'x64', 'false', 1), ('jashva', 'beta', 'unknown', 'true', 1),
        ]:
            result = subprocess.run(['bash', '-c', validation], capture_output=True,
                                    env={'PRODUCT': product, 'RELEASE_CHANNEL': channel,
                                         'ARCHITECTURE': arch, 'PUBLISH_ARTIFACTS': publish})
            self.assertEqual(result.returncode, expected)
        self.assertIn("inputs.architecture == 'x64'", steps['Build Linux AppImage']['if'])
        self.assertIn("inputs.architecture == 'x64'", steps['Install Linux packaging tools']['if'])
        self.assertNotIn('sudo', steps['Require native ARM64 packaging prerequisites']['run'])
        self.assertIn('inputs.publish_artifacts', steps['Publish qualified immutable Jashva beta']['if'])
        self.assertIn('!inputs.publish_artifacts', steps['Retain private ARM64 beta candidate']['if'])
        self.assertEqual(steps['Qualify private ARM64 beta candidate']['with']['phase'], 'qualify')

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
