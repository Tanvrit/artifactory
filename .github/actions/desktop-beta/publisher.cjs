'use strict';
const crypto = require('node:crypto');
const { validateReceipt, validateRelease, sourceMarker } = require('./contract.cjs');
function digest(bytes) { return crypto.createHash('sha256').update(bytes).digest('hex'); }
function requireValue(condition, message) { if (!condition) throw new Error(message); }
function memoryFile(name, bytes) { return { name, source: bytes, sha256: digest(bytes), size_bytes: bytes.length }; }
function publicationFiles(contract, receipt, artifacts) {
  validateReceipt(receipt, contract);
  const result = [];
  for (const row of receipt.artifacts) {
    const file = artifacts.get(row.filename);
    requireValue(file && file.size_bytes === row.size_bytes && file.sha256 === row.sha256,
      'Installer metadata differs from qualified receipt');
    result.push({ ...file, name: row.filename });
    result.push(memoryFile(`${row.filename}.sha256`, Buffer.from(`${row.sha256}  ${row.filename}\n`)));
  }
  requireValue(artifacts.size === receipt.artifacts.length, 'Unexpected files in publication request');
  const primary = receipt.artifacts[0].platform;
  result.push(memoryFile(`jashva-${contract.release_key}-${primary}.release.json`,
    Buffer.from(JSON.stringify(receipt, null, 2) + '\n')));
  return result;
}
function matches(fingerprint, file) {
  return fingerprint && fingerprint.sha256 === file.sha256 && fingerprint.size_bytes === file.size_bytes;
}

// Importing/testing this orchestration has no external effects. Native adapters stream
// installers from regular files and fingerprint readbacks without retaining them in RAM.
async function publish(contract, receipt, artifacts, io) {
  const files = publicationFiles(contract, receipt, artifacts);
  for (const file of files) requireValue(matches(await io.localFingerprint(file), file), 'Local artifact bytes changed');
  let release = await io.getRelease(contract.tag);
  if (release) validateRelease(release, contract);
  const known = new Map();
  const receiptFile = files.at(-1);
  let canReuse = false;
  if (release) {
    const priorReceipt = await io.findAsset(release, receiptFile.name);
    if (priorReceipt) {
      const key = `releases/jashva/${contract.release_key}/${receiptFile.name}`;
      requireValue(matches(await io.assetFingerprint(priorReceipt), receiptFile) &&
        matches(await io.objectFingerprint(key), receiptFile),
      'Existing platform receipt source/build/bytes differ');
      canReuse = true;
    }
  }
  for (const file of files) {
    const asset = release && await io.findAsset(release, file.name);
    const key = `releases/jashva/${contract.release_key}/${file.name}`;
    const status = await io.objectStatus(key);
    requireValue(status === 200 || status === 404, 'R2 absence or ownership is uncertain');
    if (asset || status === 200) {
      requireValue(canReuse, 'Existing bytes have no matching source/build/platform receipt');
      if (asset) requireValue(matches(await io.assetFingerprint(asset), file), 'Existing GitHub artifact differs');
      if (status === 200) requireValue(matches(await io.objectFingerprint(key), file), 'Existing R2 artifact differs');
    }
    known.set(file.name, { asset, objectExists: status === 200 });
  }
  if (!release) {
    release = await io.createRelease({ tag_name: contract.tag, name: `Jashva ${contract.version} beta ${contract.build}`,
      prerelease: true, draft: false, body: sourceMarker(contract) });
    if (!release) release = await io.getRelease(contract.tag);
    requireValue(release, 'Could not establish shared beta release');
    validateRelease(release, contract);
  }
  for (const file of files) {
    requireValue(matches(await io.localFingerprint(file), file), 'Local artifact changed before upload');
    const prior = known.get(file.name);
    if (!prior.asset) {
      const asset = await io.uploadAsset(release, file);
      requireValue(asset.name === file.name && asset.size === file.size_bytes, 'GitHub upload metadata differs');
      requireValue(matches(await io.assetFingerprint(asset), file), 'GitHub full artifact readback differs');
    }
    if (!prior.objectExists) {
      const key = `releases/jashva/${contract.release_key}/${file.name}`;
      const status = await io.createObject(key, file, { ifNoneMatch: '*' });
      requireValue(status === 200 || status === 201, 'Conditional immutable R2 creation failed');
      requireValue(matches(await io.objectFingerprint(key), file), 'R2 full artifact readback differs');
    }
  }
  await io.dispatch({ product: contract.product, version: contract.version, build: contract.build,
    release_channel: 'beta', source_commit: contract.source_commit,
    source_repository: contract.source_repository, release_key: contract.release_key,
    receipt: receiptFile.name });
  return { state: 'ARTIFACTS_VERIFIED_MANIFEST_REQUESTED', ...contract,
    artifacts: files.map(({ name, sha256, size_bytes }) => ({ name, sha256, size_bytes })) };
}
module.exports = { digest, publicationFiles, publish };
