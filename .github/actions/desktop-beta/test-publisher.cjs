'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const c = require('./contract.cjs');
const { digest, publicationFiles, publish } = require('./publisher.cjs');
const contract = c.identity('jashva', '1.0.0', '12345', 'Tanvrit/app-jashva', 'a'.repeat(40));
const name = c.filename(contract, 'macos-arm64'), bytes = Buffer.from('synthetic installer');
const file = { source: bytes, sha256: digest(bytes), size_bytes: bytes.length };
const artifacts = new Map([[name, file]]);
const receipt = { ...contract, schema: 1, artifacts: [{ platform: 'macos-arm64', filename: name, sha256: file.sha256, size_bytes: file.size_bytes }] };
const fp = source => ({ sha256: digest(source), size_bytes: source.length });
function fake(options = {}) {
  const trace = [], assets = new Map(), objects = new Map();
  const release = { id: 1, tag_name: contract.tag, prerelease: true, draft: false, body: c.sourceMarker(contract) };
  if (options.existing) {
    for (const candidate of publicationFiles(contract, receipt, artifacts)) {
      assets.set(candidate.name, candidate.source);
      objects.set(`releases/jashva/${contract.release_key}/${candidate.name}`, candidate.source);
    }
  }
  if (options.orphan) assets.set(name, bytes);
  const io = {
    async localFingerprint(candidate) { trace.push('local'); return fp(candidate.source); },
    async getRelease() { trace.push('get'); return options.absent ? null : release; },
    async findAsset(_release, name) { trace.push('find'); const source = assets.get(name); return source ? { name, size: source.length } : null; },
    async objectStatus(key) { trace.push('head'); return options.objectStatus || (objects.has(key) ? 200 : 404); },
    async createRelease(value) { trace.push('create'); return { ...value, id: 1 }; },
    async uploadAsset(_r, candidate) { trace.push('upload'); assets.set(candidate.name, candidate.source); return { name: candidate.name, size: candidate.size_bytes }; },
    async assetFingerprint(asset) { trace.push('read-github'); return options.badGithub ? fp(Buffer.from('bad')) : fp(assets.get(asset.name)); },
    async createObject(key, candidate, conditional) {
      trace.push('put'); assert.deepEqual(conditional, { ifNoneMatch: '*' });
      if (options.race) return 412;
      assert.ok(!objects.has(key)); objects.set(key, candidate.source); return 200;
    },
    async objectFingerprint(key) { trace.push('read-r2'); return options.badR2 ? fp(Buffer.from('bad')) : fp(objects.get(key)); },
    async dispatch(payload) { trace.push('dispatch'); assert.equal(payload.version, '1.0.0'); assert.equal(payload.build, 12345); },
  };
  return { io, trace, release };
}
test('publisher allowlist excludes arbitrary files and validates qualified installer metadata', () => {
  assert.equal(publicationFiles(contract, receipt, artifacts).length, 3);
  assert.throws(() => publicationFiles(contract, receipt, new Map([[name, { ...file, sha256: 'c'.repeat(64) }]])));
  assert.throws(() => publicationFiles(contract, receipt, new Map([...artifacts, ['secret.json', file]])));
});
test('new publication verifies both providers before requesting manifest update', async () => {
  const { io, trace } = fake({ absent: true });
  const result = await publish(contract, receipt, artifacts, io);
  assert.equal(result.state, 'ARTIFACTS_VERIFIED_MANIFEST_REQUESTED');
  assert.equal(trace.at(-1), 'dispatch');
  assert.equal(trace.filter(value => value === 'read-r2').length, 3);
  assert.ok(trace.indexOf('create') > trace.lastIndexOf('head'));
});
test('idempotent reuse requires exact remote bytes and matching source/build/platform receipt', async () => {
  const { io, trace } = fake({ existing: true });
  await publish(contract, receipt, artifacts, io);
  assert.ok(!trace.includes('upload')); assert.ok(!trace.includes('put')); assert.ok(trace.includes('dispatch'));
  const orphan = fake({ orphan: true });
  await assert.rejects(publish(contract, receipt, artifacts, orphan.io));
  assert.ok(!orphan.trace.includes('upload')); assert.ok(!orphan.trace.includes('put'));
});
for (const options of [{ objectStatus: 403 }, { objectStatus: 500 }, { badGithub: true }, { badR2: true }, { race: true },
  { existing: true, badGithub: true }, { existing: true, badR2: true }]) {
  test('uncertainty, replacement or readback failure cannot advertise: ' + JSON.stringify(options), async () => {
    const { io, trace } = fake(options);
    await assert.rejects(publish(contract, receipt, artifacts, io)); assert.ok(!trace.includes('dispatch'));
  });
}
test('same tag with different source custody is refused before writes', async () => {
  const { io, trace, release } = fake(); release.body = c.sourceMarker({ ...contract, source_commit: 'c'.repeat(40) });
  await assert.rejects(publish(contract, receipt, artifacts, io)); assert.ok(!trace.includes('upload'));
});
