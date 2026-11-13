'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const c = require('./contract.cjs');
const sha = 'a'.repeat(40);
const source = { product: 'jashva', version: '1.0.0', expectedBuildCode: '12345',
  sourceRepository: 'Tanvrit/app-jashva', sourceCommit: sha, callerCommit: sha, headCommit: sha,
  ref: 'refs/heads/main', eventName: 'workflow_dispatch', mainReachable: true, properties: 'VERSION_NAME=1.0.0\nVERSION_CODE=12345\n' };
const contract = c.verifySource(source);
function receipt(platform = 'macos-arm64') {
  return { ...contract, schema: 1, artifacts: [{ platform, filename: c.filename(contract, platform),
    sha256: 'b'.repeat(64), size_bytes: 100 }] };
}
test('beta identity keeps public version separate from native build transport key', () => {
  assert.equal(contract.version, '1.0.0');
  assert.equal(contract.build, 12345);
  assert.equal(contract.release_key, '1.0.0-beta.12345');
  assert.equal(c.filename(contract, 'macos-arm64'), 'jashva-1.0.0-beta.12345-macos-arm64.dmg');
});
for (const delta of [{ product: 'wedding' }, { expectedBuildCode: '12346' }, { expectedBuildCode: '01' },
  { sourceCommit: 'main' }, { callerCommit: 'b'.repeat(40) }, { headCommit: 'b'.repeat(40) },
  { sourceRepository: 'Tanvrit/other' }, { ref: 'refs/pull/1/merge' }, { mainReachable: false }, { eventName: 'pull_request' },
  { properties: source.properties + 'VERSION_CODE=12345\n' }, { version: '1.0.0-beta.12345' }]) {
  test('source guard rejects ' + JSON.stringify(delta), () => assert.throws(() => c.verifySource({ ...source, ...delta })));
}
test('shared beta release requires one exact custody marker, correct tag and prerelease flag', () => {
  const release = { tag_name: contract.tag, prerelease: true, draft: false, body: c.sourceMarker(contract) };
  c.validateRelease(release, contract);
  for (const delta of [{ prerelease: false }, { draft: true }, { body: '' },
    { body: c.sourceMarker(contract) + c.sourceMarker(contract) }, { tag_name: 'jashva-v1.0.0' }]) {
    assert.throws(() => c.validateRelease({ ...release, ...delta }, contract));
  }
});
test('manifest includes only actually qualified targets and preserves public version', () => {
  const manifest = c.mergeManifest(null, receipt(), '2026-11-14T00:00:00Z');
  assert.deepEqual(Object.keys(manifest.platforms), ['macos-arm64']);
  assert.equal(manifest.version, '1.0.0');
  assert.equal(manifest.channel, 'beta');
  assert.ok(!Object.hasOwn(manifest, 'min_required_version'));
  assert.deepEqual(c.mergeManifest(manifest, receipt(), manifest.released_at), manifest);
  const merged = c.mergeManifest(manifest, receipt('windows-x64'), manifest.released_at);
  assert.deepEqual(Object.keys(merged.platforms), ['macos-arm64', 'windows-x64']);
  assert.deepEqual(Object.keys(manifest.platforms), ['macos-arm64']);
});
test('mixed source or replacement bytes cannot enter an existing beta manifest', () => {
  const manifest = c.mergeManifest(null, receipt(), '2026-11-14T00:00:00Z');
  const changed = receipt(); changed.artifacts[0].sha256 = 'c'.repeat(64);
  assert.throws(() => c.mergeManifest(manifest, changed, manifest.released_at));
  assert.throws(() => c.mergeManifest({ ...manifest, source_commit: 'c'.repeat(40) }, receipt(), manifest.released_at));
});
test('receipt rejects path injection, missing hashes, empty files and cross-platform batches', () => {
  for (const delta of [{ filename: '../file' }, { sha256: '' }, { size_bytes: 0 }, { platform: 'macos-x64' }]) {
    const changed = receipt(); Object.assign(changed.artifacts[0], delta);
    assert.throws(() => c.validateReceipt(changed, contract));
  }
  const changed = receipt(); changed.artifacts.push(receipt('windows-x64').artifacts[0]);
  assert.throws(() => c.validateReceipt(changed, contract));
});

test('immutable candidate tag remains valid when its exact source is still on main', () => {
  assert.deepEqual(c.verifySource({ ...source, ref: 'refs/tags/desktop-beta-candidate' }), contract);
});
