'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const c = require('./contract.cjs');
const { publish } = require('./publisher.cjs');
const { verifyNative } = require('./native.cjs');

const input = name => process.env['INPUT_' + name.toUpperCase()] || '';
const requireValue = (value, message) => { if (!value) throw new Error(message); };
function command(program, args) {
  return execFileSync(program, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 1048576 }).trim();
}
async function fingerprint(source) {
  const hash = crypto.createHash('sha256'); let size = 0;
  if (Buffer.isBuffer(source)) { hash.update(source); size = source.length; }
  else {
    const stat = fs.lstatSync(source);
    requireValue(stat.isFile() && !stat.isSymbolicLink(), 'Artifact must be a regular non-symlink file');
    for await (const chunk of fs.createReadStream(source)) { hash.update(chunk); size += chunk.length; }
  }
  return { sha256: hash.digest('hex'), size_bytes: size };
}
function sourceContract({ fetchMain = true } = {}) {
  if (fetchMain) command('git', ['fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main']);
  const main = command('git', ['rev-parse', '--verify', 'refs/remotes/origin/main^{commit}']);
  let mainReachable = false;
  try { command('git', ['merge-base', '--is-ancestor', 'HEAD', main]); mainReachable = true; } catch {}
  requireValue(command('git', ['status', '--porcelain', '--untracked-files=no']) === '', 'Tracked source changed from qualified commit');
  const stat = fs.lstatSync('gradle.properties');
  requireValue(stat.isFile() && !stat.isSymbolicLink(), 'Release metadata must be a regular source file');
  return c.verifySource({ product: input('product'), version: input('version'),
    expectedBuildCode: input('expected_build_code'), sourceRepository: process.env.GITHUB_REPOSITORY,
    sourceCommit: input('source_commit'), callerCommit: process.env.GITHUB_SHA,
    headCommit: command('git', ['rev-parse', '--verify', 'HEAD^{commit}']),
    eventName: process.env.GITHUB_EVENT_NAME, ref: process.env.GITHUB_REF || '', mainReachable,
    properties: fs.readFileSync('gradle.properties', 'utf8') });
}
function verifyHost(platform) {
  const expected = { 'macos-arm64': ['darwin', 'arm64'], 'windows-x64': ['win32', 'x64'], 'linux-x64': ['linux', 'x64'] }[platform];
  requireValue(expected && os.platform() === expected[0] && os.arch() === expected[1], 'Native installer host/architecture differs');
}
function cleanNativeOutputs() {
  const relative = 'composeApp/build/compose/binaries';
  let current = process.cwd();
  for (const segment of relative.split('/')) {
    current = path.join(current, segment);
    if (fs.existsSync(current)) requireValue(!fs.lstatSync(current).isSymbolicLink(), 'Native build directory cannot be a symlink');
  }
  fs.rmSync(current, { recursive: true, force: true });
}
function configValue(value) {
  requireValue(!/[\r\n\0]/.test(value), 'Invalid transport configuration');
  return '"' + value.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}
function r2Transport(directory) {
  const account = process.env.CF_ACCOUNT_ID || process.env.CLOUDFLARE_ACCOUNT_ID || '';
  const id = process.env.R2_ACCESS_KEY_ID || '', secret = process.env.R2_SECRET_ACCESS_KEY || '';
  requireValue(/^[a-f0-9]{32}$/.test(account) && id && secret, 'R2 publication credentials are not configured');
  function request(key, method, file) {
    requireValue(/^releases\/jashva\/[A-Za-z0-9.\-]+\/[A-Za-z0-9.\-]+$/.test(key), 'Unexpected R2 publication key');
    const output = path.join(directory, crypto.randomUUID() + '.readback');
    let config = `url = ${configValue(`https://${account}.r2.cloudflarestorage.com/tanvrit-artifacts/${key}`)}\n` +
      `aws-sigv4 = "aws:amz:auto:s3"\nuser = ${configValue(id + ':' + secret)}\n`;
    const args = ['--disable', '--config', '-', '--silent', '--show-error', '--connect-timeout', '30',
      '--max-time', '1800', '--max-filesize', '2147483648', '--output', output, '--write-out', '%{http_code}'];
    if (method === 'HEAD') args.push('--head');
    if (method === 'PUT') {
      args.push('--upload-file', file);
      config += 'header = "If-None-Match: *"\nheader = "Content-Type: application/octet-stream"\n';
    }
    const status = Number(execFileSync('curl', args, { input: config, encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 65536 }).trim());
    return { status, output };
  }
  return { request };
}
function transport(contract, directory) {
  const token = process.env.ARTIFACTS_REPO_TOKEN || '';
  requireValue(token, 'ARTIFACTS_REPO_TOKEN is required for beta publication');
  const r2 = r2Transport(directory);
  const apiRoot = 'https://api.github.com/repos/Tanvrit/artifactory';
  async function api(endpoint, options = {}) {
    const response = await fetch(apiRoot + endpoint, { ...options, redirect: 'error',
      signal: AbortSignal.timeout(1800000), headers: { Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', ...options.headers } });
    return response;
  }
  async function json(response, allowed = [200]) {
    requireValue(allowed.includes(response.status), `GitHub request failed with HTTP ${response.status}`);
    return response.json();
  }
  function materialize(file) {
    if (typeof file.source === 'string') return file.source;
    const target = path.join(directory, file.name);
    fs.writeFileSync(target, file.source, { flag: 'wx', mode: 0o600 });
    return target;
  }
  const materialized = new Map();
  function local(file) {
    if (!materialized.has(file.name)) materialized.set(file.name, materialize(file));
    return materialized.get(file.name);
  }
  return {
    localFingerprint: file => fingerprint(file.source),
    async getRelease(tag) {
      const response = await api('/releases/tags/' + encodeURIComponent(tag));
      if (response.status === 404) return null;
      return json(response);
    },
    async findAsset(release, name) {
      for (let page = 1; page <= 10; page++) {
        const rows = await json(await api(`/releases/${release.id}/assets?per_page=100&page=${page}`));
        const found = rows.find(row => row.name === name);
        if (found) return found;
        if (rows.length < 100) return null;
      }
      throw new Error('Unexpectedly large beta asset listing');
    },
    async createRelease(body) {
      const response = await api('/releases', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } });
      if (response.status === 422) return null; // Distinct platform job may have created the same tag.
      return json(response, [201]);
    },
    async uploadAsset(release, file) {
      const uploadUrl = `https://uploads.github.com/repos/Tanvrit/artifactory/releases/${release.id}/assets?name=${encodeURIComponent(file.name)}`;
      const response = await fetch(uploadUrl, { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(1800000),
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/octet-stream', 'Content-Length': String(file.size_bytes) },
        body: fs.createReadStream(local(file)), duplex: 'half' });
      return json(response, [201]);
    },
    async assetFingerprint(asset) {
      const expected = `https://github.com/Tanvrit/artifactory/releases/download/${contract.tag}/`;
      requireValue(typeof asset.browser_download_url === 'string' && asset.browser_download_url.toLowerCase().startsWith(expected.toLowerCase()),
        'Unexpected GitHub asset download origin');
      const response = await fetch(asset.browser_download_url, { signal: AbortSignal.timeout(1800000) });
      requireValue(response.status === 200, `GitHub artifact readback HTTP ${response.status}`);
      const hash = crypto.createHash('sha256'); let size = 0;
      for await (const chunk of response.body) {
        size += chunk.length; requireValue(size <= 2147483648, 'Artifact readback exceeds release bound'); hash.update(chunk);
      }
      return { sha256: hash.digest('hex'), size_bytes: size };
    },
    async objectStatus(key) { const r = r2.request(key, 'HEAD'); fs.rmSync(r.output, { force: true }); return r.status; },
    async objectFingerprint(key) {
      const r = r2.request(key, 'GET');
      try { requireValue(r.status === 200, `R2 artifact readback HTTP ${r.status}`); return await fingerprint(r.output); }
      finally { fs.rmSync(r.output, { force: true }); }
    },
    async createObject(key, file, conditional) {
      requireValue(conditional.ifNoneMatch === '*', 'R2 writes require conditional creation');
      const r = r2.request(key, 'PUT', local(file)); fs.rmSync(r.output, { force: true }); return r.status;
    },
    async dispatch(payload) {
      const response = await api('/dispatches', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_type: 'update-manifest', client_payload: payload }) });
      requireValue(response.status === 204, `Manifest dispatch failed with HTTP ${response.status}`);
    },
  };
}
async function main() {
  if (input('phase') === 'verify-windows-signature') {
    requireValue(input('product') === 'jashva', 'Strict signature qualification is scoped to Jashva');
    verifyHost('windows-x64');
    verifyNative({}, 'windows-x64', path.resolve(input('artifact')));
    return;
  }
  const contract = sourceContract();
  const platform = input('platform'); verifyHost(platform);
  if (input('phase') === 'verify') {
    if (platform === 'windows-x64') requireValue(process.env.WINDOWS_SIGNING_CERT && process.env.WINDOWS_SIGNING_PASSWORD,
      'Jashva beta requires existing Windows signing credentials');
    cleanNativeOutputs();
    fs.appendFileSync(process.env.GITHUB_OUTPUT, `release_key=${contract.release_key}\n`);
    return;
  }
  requireValue(input('phase') === 'publish', 'Unknown beta action phase');
  const platforms = [platform];
  if (platform === 'linux-x64' && input('appimage_built') === 'true') platforms.push('linux-x64-app');
  const artifacts = new Map(), rows = [];
  for (const current of platforms) {
    const filename = c.filename(contract, current);
    const source = path.resolve(filename);
    verifyNative(contract, current, source);
    const proof = await fingerprint(source);
    artifacts.set(filename, { source, ...proof }); rows.push({ platform: current, filename, ...proof });
  }
  const receipt = { ...contract, schema: 1, artifacts: rows };
  c.validateReceipt(receipt, contract);
  const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP || os.tmpdir(), 'jashva-desktop-beta-'));
  try {
    const result = await publish(contract, receipt, artifacts, transport(contract, directory));
    console.log(JSON.stringify(result));
    console.log('Manifest update requested; verify its separate workflow and public bytes before reporting completion.');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
}
module.exports = { fingerprint, sourceContract, verifyHost, cleanNativeOutputs, configValue, transport };
if (require.main === module) main().catch(() => {
  // Never echo transport stderr, response bodies, URLs containing credentials or command arguments.
  console.error('::error::Qualified Jashva beta action failed. Check source/build custody, native verification and configured publication credentials.');
  process.exitCode = 1;
});
