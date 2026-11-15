'use strict';
// Private same-caller custody only. This module never publishes a release or manifest.
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const c = require('./contract.cjs');
const REPOSITORY = 'Tanvrit/app-jashva';
const MAX_DEB = 2147483648;
const STAGES = Object.freeze(['context', 'transport', 'local-verification', 'draft-preflight', 'repository-before-create',
  'source-reference', 'draft-create', 'repository-before-upload', 'asset-upload', 'draft-readback', 'repository-final']);
const REASONS = Object.freeze(['context-invalid', 'contract-invalid', 'file-size-or-type', 'file-changed',
  'checksum-mismatch', 'receipt-mismatch', 'request-failed', 'http-refused', 'response-invalid',
  'repository-not-private', 'source-reference-mismatch', 'draft-already-exists', 'draft-binding', 'asset-metadata', 'release-list-limit', 'invalid-data-or-request']);
class CustodyError extends Error {
  constructor(stage, reason, httpStatus) {
    stage = STAGES.includes(stage) ? stage : 'context';
    reason = REASONS.includes(reason) ? reason : 'invalid-data-or-request';
    super(`Private Linux custody refused: ${stage}/${reason}`);
    this.name = 'CustodyError'; this.stage = stage; this.reason = reason;
    if (Number.isInteger(httpStatus) && httpStatus >= 100 && httpStatus <= 599) this.httpStatus = httpStatus;
  }
}
const requireValue = (condition, reason) => { if (!condition) throw new CustodyError('local-verification', reason); };
function sameStat(a, b) {
  return ['dev', 'ino', 'size', 'mtimeNs', 'ctimeNs'].every(key => a[key] === b[key]);
}
async function fingerprint(file) {
  const before = await file.handle.stat({ bigint: true });
  requireValue(before.isFile() && before.size > 0n && before.size <= BigInt(file.limit), 'file-size-or-type');
  const hash = crypto.createHash('sha256'); let count = 0;
  for await (const chunk of file.handle.createReadStream({ start: 0, autoClose: false })) {
    count += chunk.length;
    requireValue(count <= file.limit, 'file-size-or-type'); hash.update(chunk);
  }
  const after = await file.handle.stat({ bigint: true });
  requireValue(sameStat(before, after) && BigInt(count) === before.size, 'file-changed');
  return { sha256: hash.digest('hex'), size_bytes: count };
}
function sameProof(a, b) { return a.sha256 === b.sha256 && a.size_bytes === b.size_bytes; }
async function openFile(directory, name, limit) {
  const filename = path.join(directory, name);
  const lstat = await fsp.lstat(filename, { bigint: true });
  requireValue(lstat.isFile() && !lstat.isSymbolicLink(), 'file-size-or-type');
  const handle = await fsp.open(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat({ bigint: true });
    requireValue(sameStat(lstat, stat), 'file-changed');
    const file = { name, handle, limit };
    Object.assign(file, await fingerprint(file));
    return file;
  } catch (error) { await handle.close(); throw error; }
}
async function smallText(file) {
  const buffer = Buffer.alloc(file.size_bytes);
  const { bytesRead } = await file.handle.read(buffer, 0, buffer.length, 0);
  requireValue(bytesRead === buffer.length, 'file-changed');
  requireValue(sameProof(file, await fingerprint(file)), 'file-changed');
  return buffer.toString('utf8');
}
function safeSourceRef(ref) {
  return ref === 'refs/heads/main' || (typeof ref === 'string' &&
    /^refs\/tags\/[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/.test(ref) && !ref.includes('..') &&
    !ref.includes('//') && !/[/.]$/.test(ref) && !ref.split('/').some(segment => segment.startsWith('.') || segment.endsWith('.lock')));
}
function validId(value) { return Number.isSafeInteger(value) && value > 0; }
function releaseMatches(value, id, tag, source, marker) {
  return value && validId(value.id) && (id === undefined || value.id === id) &&
    value.tag_name === tag && value.target_commitish === source && value.draft === true &&
    value.prerelease === true && value.name === tag && value.body === marker && Array.isArray(value.assets);
}
// Default transport uses the ephemeral caller github.token; it never invokes a subprocess.
function createTransport({ token = process.env.GITHUB_TOKEN, fetchImpl = globalThis.fetch } = {}) {
  if (typeof token !== 'string' || !token || /[\r\n\0]/.test(token) || typeof fetchImpl !== 'function') {
    throw new CustodyError('transport', 'context-invalid');
  }
  async function request(host, endpoint, { method = 'GET', json, body, length, absent = false, collection = false } = {}) {
    const headers = { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28' };
    if (json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
    if (length !== undefined) { headers['Content-Type'] = 'application/octet-stream'; headers['Content-Length'] = String(length); }
    let response;
    try {
      response = await fetchImpl(`https://${host}${endpoint}`, { method, headers, body,
        ...(length === undefined ? {} : { duplex: 'half' }), redirect: 'error',
        signal: AbortSignal.timeout(length === undefined ? 30000 : 300000) });
    } catch { throw new CustodyError('transport', 'request-failed'); }
    if (response.status === 404 && absent) { await response.body?.cancel(); return null; }
    if (!response.ok || (method === 'POST' && response.status !== 201)) {
      await response.body?.cancel(); throw new CustodyError('transport', 'http-refused', response.status);
    }
    try {
      let size = 0; const chunks = [];
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > 1048576) { await response.body.cancel().catch(() => {}); throw new Error(); }
        chunks.push(Buffer.from(chunk));
      }
      const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (collection ? !Array.isArray(value) : (!value || typeof value !== 'object' || Array.isArray(value))) throw new Error();
      return value;
    } catch { throw new CustodyError('transport', 'response-invalid'); }
  }
  const base = `/repos/${REPOSITORY}`;
  return {
    repository: () => request('api.github.com', base),
    sourceReference(ref) {
      if (!safeSourceRef(ref)) throw new CustodyError('source-reference', 'context-invalid');
      return request('api.github.com', `${base}/git/ref/${ref.slice(5)}`, { absent: true });
    },
    async existingTag(tag) {
      // Never attach a draft to a pre-existing tag, published release or earlier attempt.
      const release = await request('api.github.com', `${base}/releases/tags/${tag}`, { absent: true });
      const ref = await request('api.github.com', `${base}/git/ref/tags/${tag}`, { absent: true });
      if (release !== null || ref !== null) return true;
      // By-tag GET excludes drafts. Authenticated release lists include them, even
      // when creating a draft did not create a git ref. Bound every page and stop
      // without writes when the complete inventory cannot be established.
      for (let page = 1; page <= 10; page++) {
        const rows = await request('api.github.com', `${base}/releases?per_page=100&page=${page}`, { collection: true });
        if (rows.length > 100 || rows.some(row => !row || !validId(row.id) || typeof row.tag_name !== 'string' ||
          typeof row.draft !== 'boolean')) throw new CustodyError('draft-preflight', 'response-invalid');
        if (rows.some(row => row.tag_name === tag)) return true;
        if (rows.length < 100) return false;
      }
      throw new CustodyError('draft-preflight', 'release-list-limit');
    },
    createDraft: payload => request('api.github.com', `${base}/releases`, { method: 'POST', json: payload }),
    uploadAsset: (id, file) => request('uploads.github.com', `${base}/releases/${id}/assets?name=${encodeURIComponent(file.name)}`,
      { method: 'POST', body: file.handle.createReadStream({ start: 0, autoClose: false }), length: file.size_bytes }),
    readDraft: id => request('api.github.com', `${base}/releases/${id}`),
  };
}
async function retainPrivate(contract, options = {}) {
  let stage = 'context'; const files = [];
  try {
    const expected = c.identity(contract.product, contract.version, contract.build,
      contract.source_repository, contract.source_commit);
    requireValue(Object.keys(contract).length === Object.keys(expected).length &&
      Object.entries(expected).every(([key, value]) => contract[key] === value), 'contract-invalid');
    const repository = options.repository ?? process.env.GITHUB_REPOSITORY;
    const source = options.sourceSha ?? process.env.GITHUB_SHA;
    const sourceRef = options.sourceRef ?? process.env.GITHUB_REF;
    const run = String(options.runId ?? process.env.GITHUB_RUN_ID ?? '');
    const attempt = String(options.runAttempt ?? process.env.GITHUB_RUN_ATTEMPT ?? '');
    requireValue(repository === REPOSITORY && source === expected.source_commit && safeSourceRef(sourceRef) && options.publishArtifacts === false &&
      /^[1-9][0-9]{0,19}$/.test(run) && /^[1-9][0-9]{0,19}$/.test(attempt), 'context-invalid');
    const directory = path.resolve(options.directory ?? process.cwd());
    const deb = c.filename(expected, 'linux-arm64');
    const receiptName = `${expected.product}-${expected.release_key}-linux-arm64.release.json`;
    stage = 'local-verification';
    for (const [name, limit] of [[deb, MAX_DEB], [`${deb}.sha256`, 1024], [receiptName, 65536]]) {
      files.push(await openFile(directory, name, limit));
    }
    const declared = /^([a-fA-F0-9]{64}) [ *]([^\r\n]+)\r?\n?$/.exec(await smallText(files[1]));
    requireValue(declared && declared[2] === deb && declared[1].toLowerCase() === files[0].sha256, 'checksum-mismatch');
    const receiptText = await smallText(files[2]);
    const receipt = JSON.parse(receiptText);
    requireValue(receiptText === JSON.stringify(receipt, null, 2) + '\n', 'receipt-mismatch');
    c.validateReceipt(receipt, expected);
    requireValue(Object.keys(receipt).length === Object.keys(expected).length + 2 &&
      Object.keys(receipt).every(key => Object.hasOwn(expected, key) || ['schema', 'artifacts'].includes(key)) &&
      receipt.artifacts.every(row => Object.keys(row).length === 4 &&
        Object.keys(row).every(key => ['platform', 'filename', 'sha256', 'size_bytes'].includes(key))), 'receipt-mismatch');
    requireValue(receipt.artifacts.length === 1 && receipt.artifacts[0].platform === 'linux-arm64' &&
      sameProof(receipt.artifacts[0], files[0]), 'receipt-mismatch');
    const io = options.transport ?? createTransport();
    async function privateCheck(nextStage) {
      stage = nextStage;
      const current = await io.repository();
      if (!current || current.full_name !== REPOSITORY || current.private !== true ||
        (current.visibility !== undefined && current.visibility !== 'private')) throw new CustodyError(stage, 'repository-not-private');
    }
    const tag = `ci-linux-${run}-${attempt}`;
    const marker = `Private Linux ARM64 CI custody\n${JSON.stringify({ ...expected, run_id: run, run_attempt: attempt })}`;
    stage = 'draft-preflight';
    if (await io.existingTag(tag) !== false) throw new CustodyError(stage, 'draft-already-exists');
    await privateCheck('repository-before-create');
    stage = 'source-reference';
    const reference = await io.sourceReference(sourceRef);
    if (!reference || reference.ref !== sourceRef || reference.object?.type !== 'commit' || reference.object.sha !== source) {
      throw new CustodyError(stage, 'source-reference-mismatch');
    }
    stage = 'draft-create';
    const release = await io.createDraft({ tag_name: tag, target_commitish: source, name: tag, body: marker,
      draft: true, prerelease: true, generate_release_notes: false, make_latest: 'false' });
    if (!releaseMatches(release, undefined, tag, source, marker) || release.assets.length !== 0) {
      throw new CustodyError(stage, 'draft-binding');
    }
    for (const file of files) {
      await privateCheck('repository-before-upload');
      stage = 'asset-upload';
      requireValue(sameProof(file, await fingerprint(file)), 'file-changed');
      const asset = await io.uploadAsset(release.id, file);
      if (!asset || !validId(asset.id) || asset.name !== file.name || asset.state !== 'uploaded' ||
        asset.size !== file.size_bytes || asset.digest !== `sha256:${file.sha256}`) throw new CustodyError(stage, 'asset-metadata');
    }
    stage = 'draft-readback';
    const stored = await io.readDraft(release.id);
    if (!releaseMatches(stored, release.id, tag, source, marker) || stored.assets.length !== 3 ||
      new Set(stored.assets.map(asset => asset.name)).size !== 3 || new Set(stored.assets.map(asset => asset.id)).size !== 3) {
      throw new CustodyError(stage, 'draft-binding');
    }
    const assets = [];
    for (const file of files) {
      const asset = stored.assets.find(row => row.name === file.name);
      if (!asset || !validId(asset.id) || asset.state !== 'uploaded' || asset.size !== file.size_bytes ||
        asset.digest !== `sha256:${file.sha256}`) throw new CustodyError(stage, 'asset-metadata');
      requireValue(sameProof(file, await fingerprint(file)), 'file-changed');
      assets.push({ id: asset.id, name: file.name, size_bytes: file.size_bytes, sha256: file.sha256 });
    }
    await privateCheck('repository-final');
    return { private_ci_draft: { repository: REPOSITORY, release_id: release.id, tag, source_sha: source,
      version: expected.version, build: expected.build, run_id: run, run_attempt: attempt, draft: true, assets } };
  } catch (error) {
    // Do not expose arbitrary DI/HTTP/filesystem errors, bodies, token, paths or argv.
    if (error instanceof CustodyError) throw error;
    throw new CustodyError(stage, 'invalid-data-or-request');
  } finally {
    for (const file of files) await file.handle.close().catch(() => {});
  }
}
module.exports = { retainPrivate, createTransport, CustodyError, STAGES, REASONS };
