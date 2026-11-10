#!/usr/bin/env node
/** Launch-only diagnosis: no page, navigation, account, or raw error output. */
import { closeSync, lstatSync, openSync, readFileSync, readSync, realpathSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { promisify } from 'node:util';
import { pathToFileURL } from 'node:url';

export const PLAYWRIGHT_VERSION = '1.64.1';
export const LAUNCH_TIMEOUT_MS = 10_000;
const execFileAsync = promisify(execFile);
const SONAME = '(?:lib[A-Za-z0-9_+.-]{1,80}|ld-linux-aarch64)\\.so(?:\\.[0-9]{1,4}){0,4}';
const FAILURES = new Set(['unsupported_platform', 'node_version_mismatch', 'version_mismatch', 'tooling_unavailable',
  'tooling_install_failed', 'browser_install_failed', 'missing_native_dependencies',
  'launch_failed_unclassified', 'browser_close_failed', 'probe_failed']);

export function missingSonames(message) {
  if (typeof message !== 'string') return [];
  const found = new Set();
  let missingBlock = false;
  for (const raw of message.slice(0, 128 * 1024).split(/\r?\n/)) {
    const line = raw.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/[║│]/g, '').trim();
    const direct = line.match(new RegExp('error while loading shared libraries:\\s*(' + SONAME + '):\\s*cannot open shared object file'));
    if (direct) found.add(direct[1]);
    if (/^Missing libraries:\s*$/.test(line)) { missingBlock = true; continue; }
    if (missingBlock) {
      if (new RegExp('^' + SONAME + '$').test(line)) found.add(line);
      else if (line) missingBlock = false;
    }
  }
  return [...found].sort();
}

export function missingLddSonames(output) {
  if (typeof output !== 'string') return [];
  const matcher = new RegExp('^\\s*(' + SONAME + ')\\s+=>\\s+not found\\s*$');
  return [...new Set(output.slice(0, 256 * 1024).split(/\r?\n/).map(line => line.match(matcher)?.[1]).filter(Boolean))].sort();
}

function confinedFile(parent, child) {
  const suffix = relative(parent, child);
  return suffix !== '' && suffix !== '..' && !suffix.startsWith(`..${sep}`) && !isAbsolute(suffix);
}

/** Exact PW1.64.1 default: Chromium.getExecutableName({headless:true}) uses this registry entry. */
export function headlessShellExecutable(registry, browserRoot) {
  const root = realpathSync(browserRoot);
  if (lstatSync(browserRoot).isSymbolicLink()) throw new Error('Invalid owned browser root');
  const entry = registry.findExecutable('chromium-headless-shell');
  if (entry?.name !== 'chromium-headless-shell' || entry.browserName !== 'chromium' || entry._isHermeticInstallation !== true)
    throw new Error('Unexpected registry entry');
  const directory = realpathSync(entry.directory);
  const executable = realpathSync(entry.executablePath());
  if (!confinedFile(root, directory) || !confinedFile(directory, executable) || !lstatSync(executable).isFile())
    throw new Error('Executable is outside owned browser payload');
  // ldd is applied only to the actual downloaded AArch64 ELF, never a shell/script or arbitrary path.
  const header = Buffer.alloc(20);
  const fd = openSync(executable, 'r');
  try {
    if (readSync(fd, header, 0, header.length, 0) !== header.length ||
        header.subarray(0, 4).toString('hex') !== '7f454c46' || header[4] !== 2 || header[5] !== 1 ||
        ![2, 3].includes(header.readUInt16LE(16)) || header.readUInt16LE(18) !== 183)
      throw new Error('Unexpected executable format');
  } finally { closeSync(fd); }
  return executable;
}

export async function probeHeadlessShell(registry, browserRoot, execute = execFileAsync) {
  const executable = headlessShellExecutable(registry, browserRoot);
  let stdout;
  try {
    ({ stdout } = await execute('/usr/bin/ldd', [executable], {
      cwd: dirname(executable), timeout: 10_000, maxBuffer: 256 * 1024, encoding: 'utf8',
    }));
  } catch (error) {
    // Some ldd variants return 1 for missing libraries. Only anchored missing-library rows are usable.
    if (error?.code !== 1 || error.killed || missingLddSonames(error.stdout).length === 0) throw new Error('Dependency probe failed');
    stdout = error.stdout;
  }
  return missingLddSonames(stdout);
}

const safeVersion = value => typeof value === 'string' && /^\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(value) ? value : null;

export function safeReport({ os, arch, nodeVersion, playwrightVersion, coreVersion, browserLaunchPassed = false, missing = [], headlessShellLddStatus = 'not_run', failureEnum = null }) {
  return {
    os: ['linux', 'darwin', 'win32'].includes(os) ? os : 'unknown',
    arch: ['arm64', 'x64', 'ia32', 'arm'].includes(arch) ? arch : 'unknown',
    nodeVersion: safeVersion(nodeVersion),
    playwrightVersion: safeVersion(playwrightVersion),
    coreVersion: safeVersion(coreVersion),
    browserLaunchPassed: browserLaunchPassed === true,
    missingSonames: [...new Set(missing.filter(name => typeof name === 'string' && new RegExp('^' + SONAME + '$').test(name)))].sort(),
    headlessShellLddStatus: ['not_run', 'complete', 'failed'].includes(headlessShellLddStatus) ? headlessShellLddStatus : 'failed',
    failureEnum: failureEnum === null ? null : FAILURES.has(failureEnum) ? failureEnum : 'probe_failed',
  };
}

export async function collectStartupProbe({ os, arch, nodeVersion, playwrightVersion, coreVersion, launch, dependencyProbe }) {
  const base = { os, arch, nodeVersion, playwrightVersion, coreVersion };
  if (os !== 'linux' || arch !== 'arm64') return safeReport({ ...base, failureEnum: 'unsupported_platform' });
  if (!safeVersion(nodeVersion)?.startsWith('24.')) return safeReport({ ...base, failureEnum: 'node_version_mismatch' });
  if (playwrightVersion !== PLAYWRIGHT_VERSION || coreVersion !== PLAYWRIGHT_VERSION)
    return safeReport({ ...base, failureEnum: 'version_mismatch' });
  let browser;
  let report;
  try {
    browser = await launch({ headless: true, timeout: LAUNCH_TIMEOUT_MS });
    report = safeReport({ ...base, browserLaunchPassed: true });
  } catch (error) {
    let missing = missingSonames(typeof error?.message === 'string' ? error.message : '');
    let headlessShellLddStatus = 'not_run';
    if (dependencyProbe) {
      try { missing = [...missing, ...await dependencyProbe()]; headlessShellLddStatus = 'complete'; }
      catch { headlessShellLddStatus = 'failed'; }
    }
    report = safeReport({ ...base, missing, headlessShellLddStatus, failureEnum: missing.length ? 'missing_native_dependencies' : 'launch_failed_unclassified' });
  } finally {
    if (browser) {
      try { await browser.close(); }
      catch { report = safeReport({ ...base, browserLaunchPassed: true, failureEnum: 'browser_close_failed' }); }
    }
  }
  return report;
}

export async function main(args = process.argv.slice(2)) {
  const base = { os: process.platform, arch: process.arch, nodeVersion: process.versions.node, playwrightVersion: null, coreVersion: null };
  let report;
  try {
    if (args[0] === '--preparation-failed') {
      const failureEnum = ['tooling_install_failed', 'browser_install_failed'].includes(args[1]) ? args[1] : 'probe_failed';
      report = safeReport({ ...base, failureEnum });
    } else if (base.os !== 'linux' || base.arch !== 'arm64') {
      report = safeReport({ ...base, failureEnum: 'unsupported_platform' });
    } else if (!safeVersion(base.nodeVersion)?.startsWith('24.')) {
      report = safeReport({ ...base, failureEnum: 'node_version_mismatch' });
    } else {
      if (args.length !== 1 || !isAbsolute(args[0])) throw new Error('Invalid private tooling directory');
      const tooling = realpathSync(args[0]);
      const version = JSON.parse(readFileSync(join(tooling, 'node_modules/playwright/package.json'), 'utf8')).version;
      const coreVersion = JSON.parse(readFileSync(join(tooling, 'node_modules/playwright-core/package.json'), 'utf8')).version;
      if (version !== PLAYWRIGHT_VERSION || coreVersion !== PLAYWRIGHT_VERSION) {
        report = safeReport({ ...base, playwrightVersion: version, coreVersion, failureEnum: 'version_mismatch' });
      } else {
        const require = createRequire(import.meta.url);
        const { chromium } = require(join(tooling, 'node_modules/playwright'));
        const { registry } = require(join(tooling, 'node_modules/playwright-core/lib/coreBundle.js')).registry;
        const browserRoot = join(tooling, 'browsers');
        if (process.env.PLAYWRIGHT_BROWSERS_PATH !== browserRoot) throw new Error('Unexpected browser directory');
        report = await collectStartupProbe({ ...base, playwrightVersion: version, coreVersion,
          launch: options => chromium.launch(options), dependencyProbe: () => probeHeadlessShell(registry, browserRoot) });
      }
    }
  } catch {
    report = safeReport({ ...base, failureEnum: 'tooling_unavailable' });
  }
  process.stdout.write(`${JSON.stringify(report)}\n`);
  return report.failureEnum === null ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href)
  process.exitCode = await main();
