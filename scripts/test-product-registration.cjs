'use strict';

// Synthetic release lookup only. No network, manifests or deployed state are changed.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

const root = resolve(__dirname, '..');
function loadGeneratorWithNoArtifacts() {
  const source = readFileSync(resolve(__dirname, 'update-manifest.js'), 'utf8');
  const missing = (_url, _options, callback) => {
    const request = new EventEmitter();
    request.end = () => {};
    request.destroy = () => {};
    queueMicrotask(() => {
      const response = new EventEmitter();
      response.statusCode = 404;
      response.headers = {};
      response.resume = () => {};
      callback(response);
      queueMicrotask(() => response.emit('end'));
    });
    return request;
  };
  const module = { exports: {} };
  function localRequire(name) {
    if (name === 'https') return { get: missing, request: missing };
    return require(name);
  }
  localRequire.main = {};
  vm.runInNewContext(source, { require: localRequire, module, __dirname,
    process: { stdout: { write() {} } }, console, queueMicrotask }, { filename: 'update-manifest.js' });
  return module.exports;
}

for (const file of ['worker/src/index.js', 'functions/_shared/constants.js', 'functions/_middleware.js']) {
  test(`${file} registers Jashva and preserves Wedding`, () => {
    const source = readFileSync(resolve(root, file), 'utf8');
    const list = source.match(/const VALID_PRODUCTS = (\[[\s\S]*?\]);/)[1];
    const products = vm.runInNewContext(list);
    assert.equal(products.filter(value => value === 'jashva').length, 1);
    assert.ok(products.includes('wedding'));
  });
}

test('Jashva generator uses actual brand and app web target without claiming unavailable installers', async () => {
  const { PRODUCT_META, PLATFORM_CONFIGS, PLATFORM_EXT, buildManifest } = loadGeneratorWithNoArtifacts();
  assert.equal(PRODUCT_META.jashva.display_name, 'Jashva');
  assert.deepEqual([...PLATFORM_CONFIGS.jashva], [...PLATFORM_CONFIGS.wedding]);
  const manifest = await buildManifest('jashva', '1.0.0', 12345, '2026-11-14T00:00:00Z', '', '', false);
  assert.equal(manifest.product, 'jashva');
  assert.equal(manifest.version, '1.0.0');
  assert.equal(manifest.build, 12345);
  assert.equal(manifest.platforms.web.url, 'https://jashva.com/app/');
  for (const [platform, entry] of Object.entries(manifest.platforms)) {
    if (!PLATFORM_EXT[platform]) continue;
    assert.equal(entry.available, false, platform);
    assert.equal(entry.sha256, '', platform);
    assert.equal(entry.size_bytes, 0, platform);
  }
});

test('both edge implementations accept the actual AppImage manifest key and preserve the alias', () => {
  for (const file of ['worker/src/index.js', 'functions/_shared/constants.js']) {
    const source = readFileSync(resolve(root, file), 'utf8');
    assert.match(source, /'linux-x64-app':\s*\{ ext: 'AppImage'/);
    assert.match(source, /'linux-x64-appimage':\s*\{ ext: 'AppImage'/);
  }
});

test('Jashva missing manifest is an API 404 rather than a portal page', async () => {
  const source = readFileSync(resolve(root, 'worker/src/index.js'), 'utf8');
  const worker = (await import(require('node:url').pathToFileURL(resolve(root, 'worker/src/index.js')).href)).default;
  const keys = [];
  const response = await worker.fetch(new Request('https://artifacts.tanvrit.com/jashva/latest.json'),
    { ARTIFACTS: { async get(key) { keys.push(key); return null; } } }, {});
  assert.equal(response.status, 404);
  assert.match(response.headers.get('content-type'), /application\/json/);
  assert.deepEqual(keys, ['manifests/jashva/latest.json']);
});
