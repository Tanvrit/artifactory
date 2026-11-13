'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { sourceContract, cleanNativeOutputs, fingerprint } = require('./index.cjs');
const { verifyNative } = require('./native.cjs');

test('real source adapter requires exact event/head/build plus reachable main and refuses dirty source', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-source-'));
  const bin = path.join(root, 'bin'); fs.mkdirSync(bin);
  const script = `#!/usr/bin/env node
const args=process.argv.slice(2).join(' ');
if(args.startsWith('fetch '))process.exit(0);
if(args==='rev-parse --verify refs/remotes/origin/main^{commit}')console.log('b'.repeat(40));
else if(args==='rev-parse --verify HEAD^{commit}')console.log(process.env.SYNTHETIC_HEAD);
else if(args.startsWith('merge-base '))process.exit(process.env.SYNTHETIC_REACHABLE==='true'?0:1);
else if(args.startsWith('status '))console.log(process.env.SYNTHETIC_DIRTY||'');
`;
  fs.writeFileSync(path.join(bin, 'git'), script, { mode: 0o700 });
  fs.writeFileSync(path.join(root, 'gradle.properties'), 'VERSION_NAME=1.0.0\nVERSION_CODE=12345\n');
  const keys = ['PATH', 'INPUT_PRODUCT', 'INPUT_VERSION', 'INPUT_EXPECTED_BUILD_CODE', 'INPUT_SOURCE_COMMIT',
    'GITHUB_REPOSITORY', 'GITHUB_SHA', 'GITHUB_EVENT_NAME', 'GITHUB_REF', 'SYNTHETIC_HEAD', 'SYNTHETIC_REACHABLE', 'SYNTHETIC_DIRTY'];
  const previous = Object.fromEntries(keys.map(key => [key, process.env[key]])); const cwd = process.cwd();
  Object.assign(process.env, { PATH: bin + path.delimiter + process.env.PATH, INPUT_PRODUCT: 'jashva', INPUT_VERSION: '1.0.0',
    INPUT_EXPECTED_BUILD_CODE: '12345', INPUT_SOURCE_COMMIT: 'a'.repeat(40), GITHUB_REPOSITORY: 'Tanvrit/app-jashva',
    GITHUB_SHA: 'a'.repeat(40), GITHUB_EVENT_NAME: 'workflow_dispatch', GITHUB_REF: 'refs/tags/immutable-candidate',
    SYNTHETIC_HEAD: 'a'.repeat(40), SYNTHETIC_REACHABLE: 'true', SYNTHETIC_DIRTY: '' });
  process.chdir(root);
  try {
    assert.equal(sourceContract().build, 12345);
    process.env.SYNTHETIC_REACHABLE = 'false'; assert.throws(() => sourceContract()); process.env.SYNTHETIC_REACHABLE = 'true';
    process.env.SYNTHETIC_HEAD = 'c'.repeat(40); assert.throws(() => sourceContract()); process.env.SYNTHETIC_HEAD = 'a'.repeat(40);
    process.env.SYNTHETIC_DIRTY = ' M source.kt'; assert.throws(() => sourceContract()); process.env.SYNTHETIC_DIRTY = '';
    process.env.INPUT_EXPECTED_BUILD_CODE = '12346'; assert.throws(() => sourceContract());
  } finally {
    process.chdir(cwd); for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    fs.rmSync(root, { recursive: true, force: true });
  }
});
test('native output cleanup is bounded and artifact hashing refuses symlinks', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-native-')); const cwd = process.cwd();
  try {
    const binaries = path.join(root, 'composeApp/build/compose/binaries'); fs.mkdirSync(binaries, { recursive: true });
    fs.writeFileSync(path.join(binaries, 'stale-installer'), 'old');
    process.chdir(root); cleanNativeOutputs(); assert.ok(!fs.existsSync(binaries));
    const outside = path.join(root, 'outside'); fs.writeFileSync(outside, 'keep');
    fs.symlinkSync(outside, path.join(root, 'alias')); await assert.rejects(fingerprint(path.join(root, 'alias')));
    fs.symlinkSync(path.dirname(outside), binaries); assert.throws(() => cleanNativeOutputs()); assert.equal(fs.readFileSync(outside, 'utf8'), 'keep');
  } finally { process.chdir(cwd); fs.rmSync(root, { recursive: true, force: true }); }
});
test('AppImage native header qualification rejects ARM and truncated executables', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-appimage-'));
  try {
    const file = path.join(root, 'fixture.AppImage'), header = Buffer.alloc(20);
    Buffer.from([0x7f, 0x45, 0x4c, 0x46, 2, 1]).copy(header); header.writeUInt16LE(62, 18); fs.writeFileSync(file, header);
    verifyNative({}, 'linux-x64-app', file);
    header.writeUInt16LE(183, 18); fs.writeFileSync(file, header); assert.throws(() => verifyNative({}, 'linux-x64-app', file));
    fs.writeFileSync(file, 'short'); assert.throws(() => verifyNative({}, 'linux-x64-app', file));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
