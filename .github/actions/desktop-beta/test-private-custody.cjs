'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const c = require('./contract.cjs');
const { retainPrivate, createTransport, CustodyError, STAGES, REASONS } = require('./private-custody.cjs');
const contract = c.identity('jashva', '1.0.0', '10138', 'Tanvrit/app-jashva', 'a'.repeat(40));
const deb = c.filename(contract, 'linux-arm64');
const receiptName = `${contract.product}-${contract.release_key}-linux-arm64.release.json`;
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'custody-synthetic-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const bytes = Buffer.from('synthetic qualified DEB; not a real native installer');
  const receipt = { ...contract, schema: 1, artifacts: [{ platform: 'linux-arm64', filename: deb,
    sha256: sha(bytes), size_bytes: bytes.length }] };
  fs.writeFileSync(path.join(directory, deb), bytes);
  fs.writeFileSync(path.join(directory, `${deb}.sha256`), `${sha(bytes)}  ${deb}\n`);
  fs.writeFileSync(path.join(directory, receiptName), JSON.stringify(receipt, null, 2) + '\n');
  const options = { directory, repository: 'Tanvrit/app-jashva', sourceSha: contract.source_commit,
    runId: '123456789', runAttempt: '2', sourceRef: 'refs/tags/linux-arm64-private-10138', publishArtifacts: false };
  return { directory, bytes, receipt, options,
    writeReceipt: () => fs.writeFileSync(path.join(directory, receiptName), JSON.stringify(receipt, null, 2) + '\n') };
}
function fake(overrides = {}) {
  const trace = [], assets = []; let release;
  const io = {
    async repository() { trace.push('private'); return { full_name: 'Tanvrit/app-jashva', private: true, visibility: 'private' }; },
    async sourceReference(ref) { trace.push('source-ref'); return { ref, object: { type: 'commit', sha: contract.source_commit } }; },
    async existingTag(tag) { trace.push('absent'); assert.equal(tag, 'ci-linux-123456789-2'); return false; },
    async createDraft(payload) { trace.push('create'); release = { ...payload, id: 91, assets: [] }; return structuredClone(release); },
    async uploadAsset(id, file) {
      trace.push('upload'); assert.equal(id, 91);
      const chunks = [];
      for await (const chunk of file.handle.createReadStream({ start: 0, autoClose: false })) chunks.push(chunk);
      const bytes = Buffer.concat(chunks);
      const asset = { id: 100 + assets.length, name: file.name, state: 'uploaded', size: bytes.length, digest: `sha256:${sha(bytes)}` };
      assets.push(asset); return structuredClone(asset);
    },
    async readDraft(id) { trace.push('read'); assert.equal(id, 91); return { ...release, assets: structuredClone(assets) }; },
    ...overrides,
  };
  return { io, trace, assets, release: () => release };
}
async function refused(promise) {
  await assert.rejects(promise, error => error instanceof CustodyError && STAGES.includes(error.stage) &&
    REASONS.includes(error.reason) && !error.message.includes('SUPER_SECRET'));
}
test('exact three-file private draft custody binds source, build, hashes and each fresh private authority', async t => {
  const f = fixture(t), x = fake();
  const result = await retainPrivate(contract, { ...f.options, transport: x.io });
  assert.deepEqual(x.trace, ['absent', 'private', 'source-ref', 'create', 'private', 'upload', 'private', 'upload', 'private', 'upload', 'read', 'private']);
  assert.equal(result.private_ci_draft.tag, 'ci-linux-123456789-2');
  assert.equal(result.private_ci_draft.source_sha, contract.source_commit);
  assert.deepEqual(result.private_ci_draft.assets.map(row => row.name), [deb, `${deb}.sha256`, receiptName]);
  assert.equal(result.private_ci_draft.assets[0].sha256, sha(f.bytes));
  assert.equal(JSON.stringify(result).includes(f.directory), false);
});
test('rewritten canonical fixture reaches retention before semantic corruption cases', async t => {
  const f = fixture(t), x = fake(); f.writeReceipt();
  const result = await retainPrivate(contract, { ...f.options, transport: x.io });
  assert.equal(result.private_ci_draft.source_sha, contract.source_commit);
  assert.equal(x.assets.length, 3);
});
for (const [key, value] of [['publishArtifacts', undefined], ['publishArtifacts', true], ['publishArtifacts', 'false'],
  ['repository', 'Tanvrit/artifactory'], ['sourceSha', 'b'.repeat(40)], ['runId', '0'], ['runAttempt', '../2']]) {
  test(`invalid explicit context ${key}=${String(value)} refuses before transport`, async t => {
    const f = fixture(t), x = fake();
    await refused(retainPrivate(contract, { ...f.options, [key]: value, transport: x.io })); assert.deepEqual(x.trace, []);
  });
}
for (const mutate of [row => row.source_commit = 'b'.repeat(40), row => row.build = 10139,
  row => row.artifacts[0].platform = 'linux-x64', row => row.artifacts[0].size_bytes++,
  row => row.artifacts[0].sha256 = 'b'.repeat(64), row => row.artifacts.push({ ...row.artifacts[0] }),
  row => row.extra = 'private payload', row => row.artifacts[0].extra = 'private payload']) {
  test('receipt source/platform/hash/size/schema mismatch refuses before network: ' + mutate.toString(), async t => {
    const f = fixture(t), x = fake(); mutate(f.receipt); f.writeReceipt();
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
  });
}
for (const name of [deb, `${deb}.sha256`, receiptName]) {
  test('symlink denied for ' + name, async t => {
    const f = fixture(t), x = fake(), filename = path.join(f.directory, name);
    const outside = path.join(f.directory, 'unqualified'); fs.renameSync(filename, outside); fs.symlinkSync(outside, filename);
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
  });
  test('empty file denied for ' + name, async t => {
    const f = fixture(t), x = fake(); fs.writeFileSync(path.join(f.directory, name), '');
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
  });
}
for (const [name, content] of [[`${deb}.sha256`, 'x'.repeat(1025)], [receiptName, 'x'.repeat(65537)],
  [`${deb}.sha256`, `${'b'.repeat(64)}  ${deb}\n`], [`${deb}.sha256`, `${'a'.repeat(64)}  secret.key\n`],
  [`${deb}.sha256`, `${'a'.repeat(64)}  ${deb}\nextra\n`], [receiptName, 'not JSON']]) {
  test('malformed or oversized sidecar refuses ' + name + '/' + content.length, async t => {
    const f = fixture(t), x = fake(); fs.writeFileSync(path.join(f.directory, name), content);
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
  });
}
test('oversized DEB is rejected before reading (sparse synthetic file)', async t => {
  const f = fixture(t), x = fake(); fs.truncateSync(path.join(f.directory, deb), 2147483649);
  await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
});
for (const at of [1, 2, 3, 4, 5]) {
  test('fresh privacy loss at authority check ' + at + ' immediately stops further writes', async t => {
    const f = fixture(t); let reads = 0;
    const x = fake({ async repository() { reads++; return { full_name: 'Tanvrit/app-jashva', private: reads !== at }; } });
    await refused(retainPrivate(contract, { ...f.options, transport: x.io }));
    assert.equal(x.trace.filter(value => value === 'create').length, at === 1 ? 0 : 1);
    assert.equal(x.trace.filter(value => value === 'upload').length, Math.max(0, at - 2));
  });
}
test('repository identity mismatch and visibility contradiction refuse creation', async t => {
  const f = fixture(t);
  for (const current of [{ full_name: 'Tanvrit/artifactory', private: true },
    { full_name: 'Tanvrit/app-jashva', private: true, visibility: 'public' }]) {
    const x = fake({ repository: async () => current });
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.ok(!x.trace.includes('create'));
  }
});
test('existing draft/tag never reused or overwritten', async t => {
  const f = fixture(t), x = fake({ existingTag: async () => true });
  await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.ok(!x.trace.includes('create'));
});
for (const property of ['draft', 'prerelease', 'target_commitish', 'tag_name', 'body', 'assets']) {
  test('create response binding mismatch: ' + property, async t => {
    const f = fixture(t); const x = fake({ async createDraft(payload) {
      return { ...payload, id: 91, assets: [], [property]: property === 'assets' ? [{ name: 'orphan' }] : false };
    } });
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.ok(!x.trace.includes('upload'));
  });
}
for (const kind of ['digest', 'size', 'state', 'missing', 'extra', 'duplicate', 'source']) {
  test('final remote asset set/readback corruption refuses: ' + kind, async t => {
    const f = fixture(t), x = fake(); const original = x.io.readDraft;
    x.io.readDraft = async id => {
      const stored = await original(id);
      if (kind === 'digest') stored.assets[0].digest = `sha256:${'b'.repeat(64)}`;
      if (kind === 'size') stored.assets[0].size++;
      if (kind === 'state') stored.assets[0].state = 'starter';
      if (kind === 'missing') stored.assets.pop();
      if (kind === 'extra') stored.assets.push({ ...stored.assets[0], name: 'secret' });
      if (kind === 'duplicate') stored.assets[1] = { ...stored.assets[0] };
      if (kind === 'source') stored.target_commitish = 'b'.repeat(40);
      return stored;
    };
    await refused(retainPrivate(contract, { ...f.options, transport: x.io }));
  });
}
test('local file changed between verification and upload is refused', async t => {
  const f = fixture(t), x = fake(); const original = x.io.createDraft;
  x.io.createDraft = async payload => { fs.writeFileSync(path.join(f.directory, deb), 'changed'); return original(payload); };
  await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.ok(!x.trace.includes('upload'));
});
test('unknown create/upload outcome stops without retry, cleanup, reuse or raw error disclosure', async t => {
  const f = fixture(t);
  for (const method of ['createDraft', 'uploadAsset', 'readDraft']) {
    let count = 0; const x = fake({ async [method]() { count++; throw new Error('SUPER_SECRET response/body/token'); } });
    await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.equal(count, 1);
  }
  const e = new CustodyError('SUPER_SECRET', 'SUPER_SECRET'); assert.ok(!e.message.includes('SUPER_SECRET'));
});
test('default transport fences hosts, uses redirect error/timeouts, streams with exact content length', async t => {
  const f = fixture(t), requests = []; let release; const assets = [];
  const transport = createTransport({ token: 'synthetic_ephemeral', async fetchImpl(url, opts) {
    requests.push({ url, method: opts.method });
    assert.equal(opts.redirect, 'error'); assert.ok(opts.signal instanceof AbortSignal);
    assert.equal(opts.headers.Authorization, 'Bearer synthetic_ephemeral');
    assert.ok(url.startsWith('https://api.github.com/repos/Tanvrit/app-jashva') ||
      url.startsWith('https://uploads.github.com/repos/Tanvrit/app-jashva/releases/91/assets?name='));
    const response = (value, status = 200) => new Response(JSON.stringify(value), { status });
    if (url.endsWith('/git/ref/tags/linux-arm64-private-10138')) return response({ ref: f.options.sourceRef, object: { type: 'commit', sha: contract.source_commit } });
    if (url.includes('/releases/tags/') || url.includes('/git/ref/tags/')) return response({}, 404);
    if (url.includes('/releases?per_page=')) return response([]);
    if (url.endsWith('/Tanvrit/app-jashva')) return response({ full_name: 'Tanvrit/app-jashva', private: true });
    if (url.endsWith('/releases') && opts.method === 'POST') {
      release = { ...JSON.parse(opts.body), id: 91, assets: [] }; return response(release, 201);
    }
    if (url.startsWith('https://uploads.github.com/')) {
      assert.equal(opts.duplex, 'half'); assert.equal(Buffer.isBuffer(opts.body), false);
      const chunks = []; for await (const chunk of opts.body) chunks.push(chunk);
      const bytes = Buffer.concat(chunks); assert.equal(opts.headers['Content-Length'], String(bytes.length));
      const asset = { id: 100 + assets.length, name: new URL(url).searchParams.get('name'), state: 'uploaded',
        size: bytes.length, digest: `sha256:${sha(bytes)}` }; assets.push(asset); return response(asset, 201);
    }
    return response({ ...release, assets });
  } });
  await retainPrivate(contract, { ...f.options, transport });
  assert.equal(requests.filter(row => row.method === 'POST').length, 4);
  assert.ok(requests.every(row => !row.url.includes('synthetic_ephemeral')));
});
for (const kind of ['redirect', 'http', 'invalid-json', 'oversized-json', 'network']) {
  test('transport failure is closed and body is never disclosed: ' + kind, async () => {
    let calls = 0;
    const transport = createTransport({ token: 'synthetic', async fetchImpl() {
      calls++;
      if (kind === 'network') throw new Error('SUPER_SECRET');
      if (kind === 'redirect') return new Response('SUPER_SECRET', { status: 302 });
      if (kind === 'http') return new Response('SUPER_SECRET', { status: 403 });
      return new Response(kind === 'oversized-json' ? 'x'.repeat(1048577) : 'SUPER_SECRET', { status: 200 });
    } });
    await refused(transport.repository()); assert.equal(calls, 1);
  });
}

test('receipt duplicate JSON keys and hidden noncanonical payload refuse before transport', async t => {
  const f = fixture(t), x = fake();
  const text = fs.readFileSync(path.join(f.directory, receiptName), 'utf8');
  fs.writeFileSync(path.join(f.directory, receiptName), text.replace('{', '{\n  "source_commit": "hidden payload",'));
  await refused(retainPrivate(contract, { ...f.options, transport: x.io })); assert.deepEqual(x.trace, []);
});
test('unrelated local files are never uploaded', async t => {
  const f = fixture(t), x = fake(); fs.writeFileSync(path.join(f.directory, 'unrelated.key'), 'synthetic private data');
  await retainPrivate(contract, { ...f.options, transport: x.io });
  assert.equal(x.assets.length, 3); assert.ok(!x.assets.some(row => row.name === 'unrelated.key'));
});
test('upload acknowledgment with wrong digest stops before the next upload', async t => {
  const f = fixture(t), x = fake(); const original = x.io.uploadAsset;
  x.io.uploadAsset = async (...args) => ({ ...await original(...args), digest: `sha256:${'b'.repeat(64)}` });
  await refused(retainPrivate(contract, { ...f.options, transport: x.io }));
  assert.equal(x.trace.filter(row => row === 'upload').length, 1); assert.ok(!x.trace.includes('read'));
});

test('transport HTTP refusal exposes only bounded numeric status, preserving 403', async () => {
  const transport = createTransport({ token: 'synthetic', fetchImpl: async () => new Response('SUPER_SECRET', { status: 403 }) });
  await assert.rejects(transport.repository(), error => error instanceof CustodyError && error.stage === 'transport' &&
    error.reason === 'http-refused' && error.httpStatus === 403 && !error.message.includes('SUPER_SECRET'));
  for (const status of [undefined, null, '403', 'SUPER_SECRET', 99, 600, -1, NaN, Infinity, 403.1, {}, []]) {
    assert.equal(Object.hasOwn(new CustodyError('transport', 'http-refused', status), 'httpStatus'), false);
  }
  for (const status of [100, 403, 599]) assert.equal(new CustodyError('transport', 'http-refused', status).httpStatus, status);
});

test('matching existing authenticated draft refuses creation despite by-tag/ref 404', async t => {
  const f = fixture(t), requests = [];
  const transport = createTransport({ token: 'synthetic', async fetchImpl(url, opts) {
    requests.push({ url, method: opts.method });
    if (url.includes('/releases/tags/') || url.includes('/git/ref/tags/')) return new Response('{}', { status: 404 });
    if (url.endsWith('/releases?per_page=100&page=1')) return new Response(JSON.stringify([
      { id: 91, tag_name: 'ci-linux-123456789-2', draft: true },
    ]), { status: 200 });
    throw new Error('unexpected request');
  } });
  await assert.rejects(retainPrivate(contract, { ...f.options, transport }), error => error.reason === 'draft-already-exists');
  assert.equal(requests.length, 3); assert.ok(requests.every(row => row.method === 'GET'));
});
test('release-list pagination scans draft inventory before allowing fresh custody', async () => {
  const pages = [];
  const transport = createTransport({ token: 'synthetic', async fetchImpl(url) {
    if (url.includes('/releases/tags/') || url.includes('/git/ref/tags/')) return new Response('{}', { status: 404 });
    const page = Number(new URL(url).searchParams.get('page')); pages.push(page);
    return new Response(JSON.stringify(page === 1 ? Array.from({ length: 100 }, (_, i) => ({
      id: i + 1, tag_name: `other-${i}`, draft: true,
    })) : []), { status: 200 });
  } });
  assert.equal(await transport.existingTag('ci-linux-123456789-2'), false); assert.deepEqual(pages, [1, 2]);
});
for (const kind of ['cap', 'object', 'oversized-page', 'malformed-entry', 'http']) {
  test('incomplete or unknown release inventory is refused without writes: ' + kind, async () => {
    let lists = 0, writes = 0;
    const transport = createTransport({ token: 'synthetic', async fetchImpl(url, opts) {
      if (opts.method !== 'GET') writes++;
      if (url.includes('/releases/tags/') || url.includes('/git/ref/tags/')) return new Response('{}', { status: 404 });
      lists++;
      if (kind === 'http') return new Response('SUPER_SECRET', { status: 403 });
      const rows = Array.from({ length: kind === 'oversized-page' ? 101 : 100 }, (_, i) => ({
        id: i + 1, tag_name: `other-${i}`, draft: true,
      }));
      return new Response(JSON.stringify(kind === 'object' ? {} : kind === 'malformed-entry' ? [null] : rows), { status: 200 });
    } });
    await refused(transport.existingTag('ci-linux-123456789-2'));
    assert.equal(lists, kind === 'cap' ? 10 : 1); assert.equal(writes, 0);
  });
}

for (const [name, reference] of [['moved', { ref: 'refs/heads/main', object: { type: 'commit', sha: 'b'.repeat(40) } }],
  ['absent', null], ['annotated', { ref: 'refs/heads/main', object: { type: 'tag', sha: contract.source_commit } }],
  ['unexpected-ref', { ref: 'refs/tags/other', object: { type: 'commit', sha: contract.source_commit } }]]) {
  test('selected source reference refuses ' + name + ' immediately before draft creation', async t => {
    const f = fixture(t), x = fake({ sourceReference: async () => reference });
    await assert.rejects(retainPrivate(contract, { ...f.options, sourceRef: 'refs/heads/main', transport: x.io }),
      error => error.stage === 'source-reference' && error.reason === 'source-reference-mismatch');
    assert.ok(!x.trace.includes('create')); assert.ok(!x.trace.includes('upload'));
  });
}
test('moved main refuses while frozen direct candidate tag at exact source succeeds', async t => {
  const f = fixture(t); const x = fake({ sourceReference: async ref => ({ ref, object: {
    type: 'commit', sha: ref === 'refs/heads/main' ? 'b'.repeat(40) : contract.source_commit,
  } }) });
  await refused(retainPrivate(contract, { ...f.options, sourceRef: 'refs/heads/main', transport: x.io }));
  const result = await retainPrivate(contract, { ...f.options, transport: x.io });
  assert.equal(result.private_ci_draft.tag, 'ci-linux-123456789-2');
  assert.equal(result.private_ci_draft.source_sha, contract.source_commit);
});
for (const sourceRef of ['refs/heads/feature', 'refs/tags/../main', 'refs/tags/a//b', 'refs/tags/a.lock',
  'refs/tags/.hidden', 'refs/tags/a/.hidden', 'refs/tags/a.', 'refs/tags/a/', 'refs/tags/a?x', 'refs/tags/a#x', '', 'main']) {
  test('unsafe or unexpected source ref rejected before network: ' + sourceRef, async t => {
    const f = fixture(t), x = fake();
    await refused(retainPrivate(contract, { ...f.options, sourceRef, transport: x.io })); assert.deepEqual(x.trace, []);
  });
}
test('default transport reads exact source ref with absence handling and no ref writes', async () => {
  const urls = [];
  const transport = createTransport({ token: 'synthetic', async fetchImpl(url, opts) {
    urls.push(url); assert.equal(opts.method, 'GET');
    return new Response('{}', { status: 404 });
  } });
  assert.equal(await transport.sourceReference('refs/heads/main'), null);
  assert.equal(await transport.sourceReference('refs/tags/frozen-1.0.0'), null);
  assert.deepEqual(urls, ['https://api.github.com/repos/Tanvrit/app-jashva/git/ref/heads/main',
    'https://api.github.com/repos/Tanvrit/app-jashva/git/ref/tags/frozen-1.0.0']);
  assert.throws(() => transport.sourceReference('refs/tags/../bad'), CustodyError);
});
