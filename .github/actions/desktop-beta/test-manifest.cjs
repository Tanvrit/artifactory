'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const c = require('./contract.cjs');
const { qualify, mergeFiles } = require('./manifest.cjs');
const contract = c.identity('jashva', '1.0.0', '12345', 'Tanvrit/app-jashva', 'a'.repeat(40));
const row = { platform: 'macos-arm64', filename: c.filename(contract, 'macos-arm64'), sha256: 'b'.repeat(64), size_bytes: 100 };
const receipt = { ...contract, schema: 1, artifacts: [row] };
const payload = { ...contract, release_channel: 'beta', receipt: `jashva-${contract.release_key}-macos-arm64.release.json` };
function io(options = {}) {
  return {
    async jsonAt(url, optional) {
      if (url.includes('api.github.com')) return { value: { tag_name: contract.tag, prerelease: true, draft: false, body: c.sourceMarker(contract) } };
      if (!url.endsWith(payload.receipt)) { assert.equal(optional, true); return null; }
      return { value: receipt, bytes: Buffer.from(url.includes('github.com') && options.badReceipt ? 'wrong' : 'same-synthetic-receipt') };
    },
    async fingerprintAt() { return options.badArtifact ? { sha256: 'c'.repeat(64), size_bytes: 100 } : row; },
  };
}
test('manifest consumer independently verifies only present platform receipts and complete bytes', async () => {
  const result = await qualify(payload, io()); assert.deepEqual(result.receipts, [receipt]);
  await assert.rejects(qualify({ ...payload, release_channel: 'stable' }, io()));
  await assert.rejects(qualify(payload, io({ badReceipt: true })));
  await assert.rejects(qualify(payload, io({ badArtifact: true })));
});
test('per-build manifest merges preserve stable catalog and never regress latest beta', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-manifest-'));
  try {
    fs.mkdirSync(path.join(root, 'manifests'), { recursive: true });
    const stable = path.join(root, 'manifests/catalog.json'); fs.writeFileSync(stable, 'unchanged-stable');
    const outputs = mergeFiles(root, contract, [receipt], '2026-11-14T00:00:00Z');
    assert.deepEqual(outputs, ['manifests/jashva/beta/1.0.0-beta.12345.json', 'manifests/jashva/beta/latest.json']);
    const newer = c.identity('jashva', '1.0.0', '12346', 'Tanvrit/app-jashva', 'c'.repeat(40));
    const newerReceipt = { ...newer, schema: 1, artifacts: [{ ...row, filename: c.filename(newer, 'macos-arm64') }] };
    mergeFiles(root, newer, [newerReceipt], '2026-11-14T01:00:00Z');
    assert.equal(mergeFiles(root, contract, [receipt], '2026-11-14T02:00:00Z').length, 1);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'manifests/jashva/beta/latest.json'))).build, 12346);
    assert.equal(fs.readFileSync(stable, 'utf8'), 'unchanged-stable');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});


test('Mac then Mac+Windows cannot regress to a Windows-only scan or partially rewrite metadata', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-sequence-'));
  const directory = path.join(root, 'manifests/jashva/beta');
  const win = { ...contract, schema: 1, artifacts: [{ ...row, platform: 'windows-x64', filename: c.filename(contract, 'windows-x64') }] };
  try {
    mergeFiles(root, contract, [receipt], '2026-11-14T00:00:00Z');
    mergeFiles(root, contract, [receipt, win], '2026-11-14T01:00:00Z');
    const exact = path.join(directory, contract.release_key + '.json'), latest = path.join(directory, 'latest.json');
    const exactBytes = fs.readFileSync(exact), latestBytes = fs.readFileSync(latest);
    assert.deepEqual(Object.keys(JSON.parse(exactBytes).platforms), ['macos-arm64', 'windows-x64']);
    assert.throws(() => mergeFiles(root, contract, [win], '2026-11-14T02:00:00Z'), /previously qualified platform/);
    assert.ok(fs.readFileSync(exact).equals(exactBytes));
    assert.ok(fs.readFileSync(latest).equals(latestBytes));
    // An interrupted/deleted exact metadata file cannot bypass an intact same-build latest receipt.
    fs.unlinkSync(exact);
    assert.throws(() => mergeFiles(root, contract, [win], '2026-11-14T03:00:00Z'), /previously qualified platform/);
    assert.ok(!fs.existsSync(exact));
    assert.ok(fs.readFileSync(latest).equals(latestBytes));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
