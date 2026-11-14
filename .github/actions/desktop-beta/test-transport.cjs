'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const c = require('./contract.cjs');
const { digest, publish } = require('./publisher.cjs');
const { transport, fingerprint, configValue } = require('./index.cjs');

test('actual transport adapter streams only synthetic files through fake GitHub and curl providers', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'synthetic-beta-transports-'));
  const bin = path.join(directory, 'bin'); fs.mkdirSync(bin);
  const storage = path.join(directory, 'r2'); fs.mkdirSync(storage);
  const curl = `#!/usr/bin/env node
const fs=require('node:fs'),path=require('node:path');
const args=process.argv.slice(2),config=fs.readFileSync(0,'utf8');
if(!args.includes('--disable')||!config.includes('user = "synthetic-id:synthetic-secret"'))process.exit(2);
const key=config.match(/tanvrit-artifacts\\/(.+)"/)[1];
const target=path.join(process.env.SYNTHETIC_R2_ROOT,key),output=args[args.indexOf('--output')+1];
let status;
if(args.includes('--upload-file')){
 if(!config.includes('If-None-Match: *'))process.exit(3);
 if(fs.existsSync(target))status=412;
 else{fs.mkdirSync(path.dirname(target),{recursive:true});fs.copyFileSync(args[args.indexOf('--upload-file')+1],target);status=200;}
 fs.writeFileSync(output,'');
}else if(args.includes('--head')){status=fs.existsSync(target)?200:404;fs.writeFileSync(output,'');}
else{status=fs.existsSync(target)?200:404;if(status===200)fs.copyFileSync(target,output);else fs.writeFileSync(output,'');}
process.stdout.write(String(status));
`;
  fs.writeFileSync(path.join(bin, 'curl'), curl, { mode: 0o700 });
  const originals = Object.fromEntries(['PATH', 'ARTIFACTS_REPO_TOKEN', 'CF_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'SYNTHETIC_R2_ROOT'].map(key => [key, process.env[key]]));
  const originalFetch = global.fetch;
  const contract = c.identity('jashva', '1.0.0', '12345', 'Tanvrit/app-jashva', 'a'.repeat(40));
  const filename = c.filename(contract, 'macos-arm64');
  const bytes = Buffer.from('synthetic native installer bytes');
  const source = path.join(directory, filename); fs.writeFileSync(source, bytes);
  const row = { platform: 'macos-arm64', filename, sha256: digest(bytes), size_bytes: bytes.length };
  const receipt = { ...contract, schema: 1, artifacts: [row] };
  const files = new Map([[filename, { source, sha256: row.sha256, size_bytes: row.size_bytes }]]);
  let release = null, uploads = 0, dispatches = 0;
  const assets = new Map();
  global.fetch = async (url, options = {}) => {
    if (url.startsWith('https://api.github.com/repos/Tanvrit/artifactory')) {
      assert.equal(options.headers.Authorization, 'Bearer synthetic-token');
      if (url.includes('/releases/tags/')) return new Response(JSON.stringify(release || {}), { status: release ? 200 : 404 });
      if (url.endsWith('/releases')) {
        release = { ...JSON.parse(options.body), id: 1 };
        return new Response(JSON.stringify(release), { status: 201 });
      }
      if (url.includes('/assets?')) return new Response(JSON.stringify([...assets.values()].map(row => row.metadata)));
      if (url.endsWith('/dispatches')) { dispatches++; return new Response(null, { status: 204 }); }
    }
    if (url.startsWith('https://uploads.github.com/repos/Tanvrit/artifactory/')) {
      const name = new URL(url).searchParams.get('name');
      assert.ok(!assets.has(name), 'No GitHub asset replacement');
      const chunks = []; for await (const chunk of options.body) chunks.push(chunk);
      const bytes = Buffer.concat(chunks);
      const metadata = { name, size: bytes.length, browser_download_url: `https://github.com/Tanvrit/artifactory/releases/download/${contract.tag}/${name}` };
      assets.set(name, { bytes, metadata }); uploads++;
      return new Response(JSON.stringify(metadata), { status: 201 });
    }
    if (url.startsWith(`https://github.com/Tanvrit/artifactory/releases/download/${contract.tag}/`)) {
      return new Response(assets.get(url.split('/').at(-1)).bytes);
    }
    throw Error('Unexpected synthetic provider request');
  };
  Object.assign(process.env, { PATH: bin + path.delimiter + process.env.PATH, ARTIFACTS_REPO_TOKEN: 'synthetic-token',
    CF_ACCOUNT_ID: 'a'.repeat(32), R2_ACCESS_KEY_ID: 'synthetic-id', R2_SECRET_ACCESS_KEY: 'synthetic-secret', SYNTHETIC_R2_ROOT: storage });
  try {
    assert.deepEqual(await fingerprint(source), { sha256: row.sha256, size_bytes: row.size_bytes });
    await publish(contract, receipt, files, transport(contract, directory));
    assert.equal(uploads, 3); assert.equal(dispatches, 1);
    await publish(contract, receipt, files, transport(contract, directory));
    assert.equal(uploads, 3, 'Identical source/build/platform reuse never overwrites'); assert.equal(dispatches, 2);
    assert.throws(() => configValue('line\nbreak'));
  } finally {
    global.fetch = originalFetch;
    for (const [key, value] of Object.entries(originals)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
