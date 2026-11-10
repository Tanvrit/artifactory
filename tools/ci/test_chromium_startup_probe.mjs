import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { collectStartupProbe, headlessShellExecutable, missingLddSonames, missingSonames, probeHeadlessShell, safeReport, LAUNCH_TIMEOUT_MS, PLAYWRIGHT_VERSION } from './chromium_startup_probe.mjs';

const PRIVATE = 'private-path-token-url-never-report';
const base = { os: 'linux', arch: 'arm64', nodeVersion: '24.15.0', playwrightVersion: PLAYWRIGHT_VERSION, coreVersion: PLAYWRIGHT_VERSION };

async function withFakePayload(body) {
  const temporary = mkdtempSync(join(tmpdir(), 'chromium-probe-test-'));
  const root = join(temporary, 'browsers');
  const directory = join(root, 'headless-pinned-revision');
  const executable = join(directory, 'headless-shell');
  mkdirSync(directory, { recursive: true });
  const header = Buffer.alloc(20);
  header.write('7f454c46', 0, 'hex'); header[4] = 2; header[5] = 1;
  header.writeUInt16LE(3, 16); header.writeUInt16LE(183, 18);
  writeFileSync(executable, header);
  const entry = { name: 'chromium-headless-shell', browserName: 'chromium', _isHermeticInstallation: true,
    directory, executablePath: () => executable };
  const registry = { findExecutable: name => { assert.equal(name, 'chromium-headless-shell'); return entry; } };
  try { return await body({ temporary, root, directory, executable, entry, registry }); }
  finally { rmSync(temporary, { recursive: true, force: true }); }
}

test('direct loader errors yield sorted unique SONAMEs, never paths or raw text', () => {
  const report = missingSonames(`error while loading shared libraries: libX11.so.6: cannot open shared object file: ${PRIVATE}\n` +
    'error while loading shared libraries: libEGL.so.1: cannot open shared object file\n' +
    'error while loading shared libraries: libX11.so.6: cannot open shared object file');
  assert.deepEqual(report, ['libEGL.so.1', 'libX11.so.6']);
  assert.ok(!JSON.stringify(report).includes(PRIVATE));
});

test('the pinned Playwright missing-library box is parsed without arbitrary lines', () => {
  assert.deepEqual(missingSonames(`╔══════════╗\n║ Host system is missing dependencies to run browsers. ║\n║ Missing libraries: ║\n║   libatk-1.0.so.0  ║\n║   libgbm.so.1 ║\n║ ${PRIVATE} ║\n╚══════════╝`), ['libatk-1.0.so.0', 'libgbm.so.1']);
});

test('generic install suggestions and unrecognized errors do not invent a missing library', () => {
  for (const message of [`${PRIVATE} libSecret.so.1`, 'sudo apt-get install libatk1.0-0',
    'error while loading shared libraries: /private/libSecret.so.1: cannot open shared object file',
    'Missing libraries:\nhttps://private/libSecret.so.1', undefined]) assert.deepEqual(missingSonames(message), []);
});

test('actual platform and both exact module versions are required before launch', async () => {
  for (const override of [{ os: 'darwin' }, { arch: 'x64' }, { nodeVersion: '22.16.0' }, { nodeVersion: PRIVATE }, { playwrightVersion: '1.64.2' }, { coreVersion: '1.63.0' }]) {
    let launched = false;
    const result = await collectStartupProbe({ ...base, ...override, launch: async () => { launched = true; } });
    assert.equal(launched, false);
    assert.equal(result.browserLaunchPassed, false);
    assert.ok(['unsupported_platform', 'node_version_mismatch', 'version_mismatch'].includes(result.failureEnum));
  }
});

test('success uses the real launch options, opens no page and always closes the owned browser', async () => {
  let closes = 0;
  const result = await collectStartupProbe({ ...base, launch: async options => {
    assert.deepEqual(options, { headless: true, timeout: LAUNCH_TIMEOUT_MS });
    return { close: async () => { closes++; }, newPage: () => { assert.fail('No page may be opened'); } };
  } });
  assert.equal(closes, 1);
  assert.deepEqual(result, { os: 'linux', arch: 'arm64', nodeVersion: '24.15.0', playwrightVersion: '1.64.1', coreVersion: '1.64.1', browserLaunchPassed: true, missingSonames: [], headlessShellLddStatus: 'not_run', failureEnum: null });
});

test('launch rejection is a classified diagnostic and never serializes raw errors', async () => {
  const result = await collectStartupProbe({ ...base, launch: async () => {
    throw new Error(`error while loading shared libraries: libgbm.so.1: cannot open shared object file: ${PRIVATE}`);
  } });
  assert.equal(result.browserLaunchPassed, false);
  assert.equal(result.failureEnum, 'missing_native_dependencies');
  assert.deepEqual(result.missingSonames, ['libgbm.so.1']);
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test('unclassified launch rejection remains honestly unclassified', async () => {
  const result = await collectStartupProbe({ ...base, launch: async () => { throw new Error(PRIVATE); } });
  assert.equal(result.failureEnum, 'launch_failed_unclassified');
  assert.deepEqual(result.missingSonames, []);
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test('a close failure is reported without raw error text or a false overall success', async () => {
  let closes = 0;
  const result = await collectStartupProbe({ ...base, launch: async () => ({ close: async () => { closes++; throw new Error(PRIVATE); } }) });
  assert.equal(closes, 1);
  assert.equal(result.browserLaunchPassed, true);
  assert.equal(result.failureEnum, 'browser_close_failed');
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test('safe output admits only enums, validated versions and library basenames', () => {
  const report = safeReport({ os: PRIVATE, arch: PRIVATE, nodeVersion: PRIVATE, playwrightVersion: PRIVATE, coreVersion: PRIVATE,
    missing: ['/private/libFoo.so.1', 'libgbm.so.1', 'libgbm.so.1'], headlessShellLddStatus: PRIVATE, failureEnum: PRIVATE });
  assert.deepEqual(report, { os: 'unknown', arch: 'unknown', nodeVersion: null, playwrightVersion: null, coreVersion: null,
    browserLaunchPassed: false, missingSonames: ['libgbm.so.1'], headlessShellLddStatus: 'failed', failureEnum: 'probe_failed' });
});

test('ldd diagnostics admit only anchored missing SONAME rows', () => {
  assert.deepEqual(missingLddSonames(`  libgbm.so.1 => not found\n libEGL.so.1 => not found\n` +
    `libgbm.so.1 => not found\nlibX11.so.6 => /private/${PRIVATE} (0x123)\n` +
    `/private/libSecret.so.1 => not found\n${PRIVATE} libSecret.so.1 => not found\n`), ['libEGL.so.1', 'libgbm.so.1']);
});

test('exact registry headless payload is confined and verified before read-only ldd', async () => {
  await withFakePayload(async ({ registry, root, executable, directory }) => {
    let calls = 0;
    assert.deepEqual(await probeHeadlessShell(registry, root, async (command, args, options) => {
      calls++;
      assert.equal(command, '/usr/bin/ldd');
      assert.deepEqual(args, [headlessShellExecutable(registry, root)]);
      assert.equal(options.cwd, headlessShellExecutable(registry, root).slice(0, -'/headless-shell'.length));
      assert.equal(options.timeout, 10_000); assert.equal(options.maxBuffer, 256 * 1024);
      assert.equal(options.encoding, 'utf8'); assert.equal(options.shell, undefined);
      return { stdout: 'libgbm.so.1 => not found\n' };
    }), ['libgbm.so.1']);
    assert.equal(calls, 1);
  });
});

test('wrong registry identity, outside payload, symlink root and non-AArch64 ELF refuse before ldd', async () => {
  await withFakePayload(async ({ root, entry, registry, temporary, executable }) => {
    for (const mutation of [{ name: 'chromium' }, { browserName: 'webkit' }, { _isHermeticInstallation: false },
      { directory: temporary }, { executablePath: () => join(temporary, 'outside') }]) {
      writeFileSync(join(temporary, 'outside'), Buffer.alloc(20));
      assert.throws(() => headlessShellExecutable({ findExecutable: () => ({ ...entry, ...mutation }) }, root));
    }
    const link = join(temporary, 'linked-browsers'); symlinkSync(root, link);
    assert.throws(() => headlessShellExecutable(registry, link));
    writeFileSync(executable, '#!/bin/sh\n');
    assert.throws(() => headlessShellExecutable(registry, root));
  });
});

test('generic Ubuntu validation rejection gains exact dependency evidence without changing launch result', async () => {
  const result = await collectStartupProbe({ ...base, launch: async () => { throw new Error(`sudo apt-get install unknown ${PRIVATE}`); },
    dependencyProbe: async () => ['libgbm.so.1'] });
  assert.equal(result.browserLaunchPassed, false);
  assert.equal(result.failureEnum, 'missing_native_dependencies');
  assert.equal(result.headlessShellLddStatus, 'complete');
  assert.deepEqual(result.missingSonames, ['libgbm.so.1']);
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
});

test('failed or clean ldd never invents dependencies or conceals a failed launch', async () => {
  for (const dependencyProbe of [async () => [], async () => { throw new Error(PRIVATE); }]) {
    const result = await collectStartupProbe({ ...base, launch: async () => { throw new Error(PRIVATE); }, dependencyProbe });
    assert.equal(result.browserLaunchPassed, false); assert.equal(result.failureEnum, 'launch_failed_unclassified');
    assert.deepEqual(result.missingSonames, []); assert.ok(!JSON.stringify(result).includes(PRIVATE));
  }
});

test('ldd nonzero missing rows are usable but timeout or unrelated failures are refused', async () => {
  await withFakePayload(async ({ registry, root }) => {
    assert.deepEqual(await probeHeadlessShell(registry, root, async () => { throw { code: 1, stdout: 'libgbm.so.1 => not found\n' }; }), ['libgbm.so.1']);
    for (const error of [{ code: 1, stdout: PRIVATE }, { code: 1, killed: true, stdout: 'libgbm.so.1 => not found' }, { code: 'ENOENT' }])
      await assert.rejects(probeHeadlessShell(registry, root, async () => { throw error; }));
  });
});

test('workflow remains optional, isolated, version-bound and never installs host packages or bypasses validation', () => {
  const workflow = readFileSync(new URL('../../.github/workflows/diagnose-linux-chromium-startup.yml', import.meta.url), 'utf8');
  assert.match(workflow, /workflow_dispatch:/);
  assert.doesNotMatch(workflow, /^  (?:push|pull_request|schedule):/m);
  assert.match(workflow, /labels: \[self-hosted, Linux, ARM64\]/);
  assert.match(workflow, /node-version: '24'/);
  assert.match(workflow, /--prefix="\$probe_home"/);
  assert.match(workflow, /playwright@1\.64\.1/);
  assert.match(workflow, /install chromium --only-shell/);
  assert.match(workflow, /unset PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS/);
  assert.doesNotMatch(workflow, /\b(?:sudo|apt-get|install-deps)\b|--with-deps|PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=/);
});
