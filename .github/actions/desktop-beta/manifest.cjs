'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const c = require('./contract.cjs');
const requireValue = (value, message) => { if (!value) throw new Error(message); };
const RELEASES = 'https://dl.tanvrit.com/releases/jashva';
const GITHUB = 'https://github.com/Tanvrit/artifactory/releases/download';
async function jsonAt(url, optional = false) {
  const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
  if (optional && response.status === 404) return null;
  requireValue(response.status === 200, 'Published beta receipt is not readable');
  const chunks = []; let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length; requireValue(size <= 1048576, 'Beta receipt exceeds size bound'); chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks);
  return { value: JSON.parse(bytes.toString('utf8')), bytes };
}
async function fingerprintAt(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(1800000) });
  requireValue(response.status === 200, 'Beta installer readback is unavailable');
  const hash = crypto.createHash('sha256'); let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length; requireValue(size <= 2147483648, 'Installer exceeds size bound'); hash.update(chunk);
  }
  return { sha256: hash.digest('hex'), size_bytes: size };
}
function mergeFiles(root, contract, receipts, timestamp) {
  const directory = path.join(root, 'manifests/jashva/beta');
  const exact = path.join(directory, contract.release_key + '.json');
  const latest = path.join(directory, 'latest.json');
  const read = filename => fs.existsSync(filename) ? JSON.parse(fs.readFileSync(filename, 'utf8')) : null;
  const priorExact = read(exact);
  const previous = read(latest);
  const prior = [priorExact, previous?.build === contract.build ? previous : null].filter(Boolean);
  let manifest = null;
  for (const receipt of receipts) {
    for (const earlier of prior) c.mergeManifest(earlier, receipt, timestamp); // Detect an immutable record replacement.
    manifest = c.mergeManifest(manifest, receipt, priorExact?.released_at || timestamp);
  }
  requireValue(manifest, 'No independently verified beta platforms');
  for (const earlier of prior) {
    requireValue(Object.keys(earlier.platforms).every(platform => Object.hasOwn(manifest.platforms, platform)),
      'A previously qualified platform is absent from current independent verification');
  }
  if (previous && previous.build === contract.build) {
    requireValue(previous.source_commit === contract.source_commit && previous.release_key === contract.release_key,
      'Latest beta build custody conflicts');
  }
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(exact, JSON.stringify(manifest, null, 2) + '\n');
  const output = [path.relative(root, exact)];
  if (!previous || previous.build <= contract.build) {
    fs.writeFileSync(latest, JSON.stringify(manifest, null, 2) + '\n'); output.push(path.relative(root, latest));
  }
  return output;
}
async function qualify(payload, io = { jsonAt, fingerprintAt }) {
  requireValue(payload.release_channel === 'beta', 'Beta selection must be explicit');
  const contract = c.identity(payload.product, payload.version, String(payload.build), payload.source_repository, payload.source_commit);
  requireValue(payload.release_key === contract.release_key, 'Beta release key differs');
  const release = (await io.jsonAt(`https://api.github.com/repos/Tanvrit/artifactory/releases/tags/${contract.tag}`)).value;
  c.validateRelease(release, contract);
  const receiptNames = ['macos-arm64', 'windows-x64', 'linux-x64'].map(platform => `jashva-${contract.release_key}-${platform}.release.json`);
  requireValue(receiptNames.includes(payload.receipt), 'Unexpected requested platform receipt');
  const receipts = [];
  for (const name of receiptNames) {
    const r2 = await io.jsonAt(`${RELEASES}/${contract.release_key}/${name}`, name !== payload.receipt);
    if (!r2) continue; // Absent platforms stay unavailable, including cancelled pending dispatches.
    const github = await io.jsonAt(`${GITHUB}/${contract.tag}/${name}`);
    requireValue(r2.bytes.equals(github.bytes), 'Beta platform receipts differ between providers');
    const receipt = c.validateReceipt(r2.value, contract);
    const primary = receipt.artifacts[0].platform;
    requireValue(name === `jashva-${contract.release_key}-${primary}.release.json`, 'Receipt platform differs from its filename');
    for (const artifact of receipt.artifacts) {
      for (const url of [`${RELEASES}/${contract.release_key}/${artifact.filename}`, `${GITHUB}/${contract.tag}/${artifact.filename}`]) {
        const actual = await io.fingerprintAt(url);
        requireValue(actual.sha256 === artifact.sha256 && actual.size_bytes === artifact.size_bytes,
          'Beta platform bytes differ from their qualified receipt');
      }
    }
    receipts.push(receipt);
  }
  return { contract, receipts };
}
async function main() {
  const event = JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'));
  const { contract, receipts } = await qualify(event.client_payload || {});
  const files = mergeFiles(process.cwd(), contract, receipts, new Date().toISOString());
  requireValue(process.env.BETA_MANIFEST_OUTPUT, 'Manifest output receipt is required');
  fs.writeFileSync(process.env.BETA_MANIFEST_OUTPUT, JSON.stringify({ contract, files }, null, 2) + '\n');
  console.log(`Qualified ${receipts.length} beta platform receipt(s); stable catalog untouched.`);
}
module.exports = { qualify, mergeFiles };
if (require.main === module) main().catch(() => {
  console.error('::error::Beta manifest qualification failed; no available installer may be advertised without matching source/build receipts and complete bytes.');
  process.exitCode = 1;
});
