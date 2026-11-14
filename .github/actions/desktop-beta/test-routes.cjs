'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const c = require('./contract.cjs');
const contract = c.identity('jashva', '1.0.0', '12345', 'Tanvrit/app-jashva', 'a'.repeat(40));
const row = { platform: 'macos-arm64', filename: c.filename(contract, 'macos-arm64'), sha256: 'b'.repeat(64), size_bytes: 100 };
const manifest = c.mergeManifest(null, { ...contract, schema: 1, artifacts: [row] }, '2026-11-14T00:00:00Z');
async function request(path, value = manifest, method = 'GET') {
  const { routeBeta } = await import('../../../functions/_shared/beta-routes.js');
  const keys = [];
  const response = await routeBeta(new Request('https://artifacts.tanvrit.com' + path, { method }), {
    ARTIFACTS: { async get(key) { keys.push(key); return value ? { async json() { return value; } } : null; } },
  });
  return { response, keys };
}
test('stable and other product routes are untouched', async () => {
  for (const path of ['/jashva/latest.json', '/wedding/latest/macos-arm64', '/friendly/beta/latest.json']) {
    assert.equal((await request(path)).response, null);
  }
});
test('latest and exact beta route read only their beta manifests', async () => {
  const latest = await request('/jashva/beta/latest.json');
  assert.equal(latest.response.status, 200);
  assert.deepEqual(latest.keys, ['manifests/jashva/beta/latest.json']);
  const exact = await request('/jashva/beta/1.0.0-beta.12345/latest.json');
  assert.equal(exact.response.status, 200);
  assert.deepEqual(exact.keys, ['manifests/jashva/beta/1.0.0-beta.12345.json']);
  assert.equal((await request('/jashva/beta/latest.json', manifest, 'HEAD')).response.body, null);
});
test('download only redirects to exact qualified R2 artifact', async () => {
  const good = await request('/jashva/beta/latest/macos-arm64');
  assert.equal(good.response.status, 302);
  assert.equal(good.response.headers.get('Location'), manifest.platforms['macos-arm64'].r2_url);
  const malicious = structuredClone(manifest); malicious.platforms['macos-arm64'].r2_url = 'https://example.com/installer';
  assert.equal((await request('/jashva/beta/latest/macos-arm64', malicious)).response.status, 503);
  assert.equal((await request('/jashva/beta/latest/windows-x64')).response.status, 503);
  assert.equal((await request('/jashva/beta/latest/macos-x64')).response.status, 400);
});
test('missing or mismatched source metadata never advertises a beta', async () => {
  assert.equal((await request('/jashva/beta/latest.json', null)).response.status, 404);
  for (const delta of [{ channel: 'stable' }, { build: 1 }, { source_commit: '' }, { version: '2.0.0' }]) {
    assert.equal((await request('/jashva/beta/latest.json', { ...manifest, ...delta })).response.status, 503);
  }
  assert.equal((await request('/jashva/beta/1.0.0-beta.12346/latest.json')).response.status, 503);
});
