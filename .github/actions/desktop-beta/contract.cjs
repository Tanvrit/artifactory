'use strict';

const FORMATS = Object.freeze({
  'macos-arm64': 'dmg',
  'windows-x64': 'msi',
  'linux-x64': 'deb',
  'linux-x64-app': 'AppImage',
});
const SHA = /^[a-f0-9]{40}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const BUILD = /^[1-9][0-9]{0,9}$/;

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}
function parseProperties(text) {
  const result = {};
  for (const key of ['VERSION_NAME', 'VERSION_CODE']) {
    const lines = text.split(/\r?\n/).filter(line => new RegExp(`^${key}=`).test(line));
    requireValue(lines.length === 1, `Expected exactly one ${key}`);
    result[key] = lines[0].slice(key.length + 1).trim();
  }
  return result;
}
function identity(product, version, build, sourceRepository, sourceCommit) {
  requireValue(product === 'jashva', 'Public beta is explicitly scoped to Jashva');
  requireValue(version === '1.0.0', 'Jashva prelaunch public beta version must remain 1.0.0');
  requireValue(BUILD.test(String(build)) && Number(build) <= 2100000000, 'Invalid native build code');
  requireValue(sourceRepository === 'Tanvrit/app-jashva', 'Unexpected beta source repository');
  requireValue(SHA.test(sourceCommit), 'Source must be a full lowercase commit SHA');
  const releaseKey = `${version}-beta.${build}`;
  return { product, version, build: Number(build), channel: 'beta',
    source_repository: sourceRepository, source_commit: sourceCommit,
    release_key: releaseKey, tag: `${product}-v${releaseKey}` };
}
function verifySource(input) {
  const expected = identity(input.product, input.version, input.expectedBuildCode,
    input.sourceRepository, input.sourceCommit);
  requireValue(input.eventName === 'workflow_dispatch' && (input.ref === 'refs/heads/main' || input.ref.startsWith('refs/tags/')),
    'Beta requires an explicitly selected main or immutable candidate tag');
  requireValue(input.mainReachable === true, 'Beta source must be reachable from approved main');
  requireValue(input.callerCommit === expected.source_commit && input.headCommit === expected.source_commit,
    'Caller, checkout and approved beta commit must match');
  const properties = parseProperties(input.properties);
  requireValue(properties.VERSION_NAME === expected.version, 'VERSION_NAME differs from requested public version');
  requireValue(properties.VERSION_CODE === String(expected.build), 'VERSION_CODE differs from expected native build code');
  return expected;
}
function filename(contract, platform) {
  requireValue(Object.hasOwn(FORMATS, platform), 'Unsupported beta installer platform');
  const stem = platform === 'linux-x64-app' ? 'linux-x64' : platform;
  return `${contract.product}-${contract.release_key}-${stem}.${FORMATS[platform]}`;
}
function canonical(contract) {
  return identity(contract.product, contract.version, contract.build,
    contract.source_repository, contract.source_commit);
}
function sourceMarker(contract) {
  return `<!-- tanvrit-desktop-beta-source:${JSON.stringify(canonical(contract))} -->`;
}
function validateRelease(release, contract) {
  requireValue(release.tag_name === contract.tag && release.prerelease === true && release.draft === false,
    'Existing release is not this public beta');
  const markers = (release.body || '').match(/<!-- tanvrit-desktop-beta-source:.*? -->/g) || [];
  requireValue(markers.length === 1 && markers[0] === sourceMarker(contract), 'Existing release source/build custody differs');
}
function validateReceipt(receipt, contract) {
  for (const [key, value] of Object.entries(canonical(contract))) {
    requireValue(receipt[key] === value, `Receipt ${key} differs from release custody`);
  }
  requireValue(receipt.schema === 1 && Array.isArray(receipt.artifacts) && receipt.artifacts.length >= 1 &&
    receipt.artifacts.length <= 2, 'Malformed beta receipt');
  const seen = new Set();
  for (const artifact of receipt.artifacts) {
    requireValue(!seen.has(artifact.platform), 'Duplicate platform in beta receipt');
    seen.add(artifact.platform);
    requireValue(artifact.filename === filename(contract, artifact.platform), 'Unexpected beta artifact filename');
    requireValue(DIGEST.test(artifact.sha256), 'Invalid artifact digest');
    requireValue(Number.isSafeInteger(artifact.size_bytes) && artifact.size_bytes > 0 && artifact.size_bytes <= 2147483648,
      'Invalid installer size');
  }
  const keys = [...seen];
  requireValue(keys.length === 1 || keys.every(key => key === 'linux-x64' || key === 'linux-x64-app'),
    'A release job can only qualify its own native platform');
  return receipt;
}
function mergeManifest(existing, receipt, releasedAt) {
  const contract = canonical(receipt);
  validateReceipt(receipt, contract);
  if (existing) {
    for (const [key, value] of Object.entries(contract)) {
      requireValue(existing[key] === value, `Existing beta manifest ${key} differs`);
    }
  }
  const manifest = existing ? structuredClone(existing) : { ...contract, display_name: 'Jashva',
    tagline: 'Gather your people', released_at: releasedAt, platforms: {} };
  for (const artifact of receipt.artifacts) {
    const key = `releases/jashva/${contract.release_key}/${artifact.filename}`;
    const entry = { filename: artifact.filename, available: true, sha256: artifact.sha256,
      size_bytes: artifact.size_bytes, format: FORMATS[artifact.platform],
      url: `https://artifacts.tanvrit.com/jashva/beta/${contract.release_key}/${artifact.platform}`,
      direct_url: `https://github.com/Tanvrit/artifactory/releases/download/${contract.tag}/${artifact.filename}`,
      r2_url: `https://dl.tanvrit.com/${key}` };
    requireValue(!manifest.platforms[artifact.platform] ||
      JSON.stringify(manifest.platforms[artifact.platform]) === JSON.stringify(entry),
    'An already qualified beta platform cannot be replaced');
    manifest.platforms[artifact.platform] = entry;
  }
  return manifest;
}
module.exports = { FORMATS, identity, verifySource, parseProperties, filename, sourceMarker,
  validateRelease, validateReceipt, mergeManifest };
