#!/usr/bin/env node
/**
 * Update a product's release manifest.
 * Fetches SHA256 and file sizes from GitHub Releases automatically.
 *
 * Usage (local):
 *   node update-manifest.js --product friendly --version 1.2.0 --build 120
 *
 * Usage (CI — skip fetch, pass pre-computed values):
 *   node update-manifest.js --product friendly --version 1.2.0 --build 120 \
 *     --release-notes "Bug fixes" --skip-fetch
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const https = require('https');

const ROOT         = path.join(__dirname, '..');
const MANIFESTS    = path.join(ROOT, 'manifests');
const BASE_URL     = 'https://artifacts.tanvrit.com';
const GH_RELEASES  = 'https://github.com/tanvrit/artifactory/releases';
const GH_DOWNLOAD  = `${GH_RELEASES}/download`;

// ── Product metadata ──────────────────────────────────────────────────────────
const PRODUCT_META = {
  friendly:        { display_name: 'Friendly',      tagline: 'POS & Retail for Bharat',          min_version_bump: 'minor' },
  desipops:        { display_name: 'DesiPops',      tagline: 'Entertainment for Bharat',          min_version_bump: 'minor' },
  mandee:          { display_name: 'Mandee',        tagline: 'Business Management, Simplified',   min_version_bump: 'minor' },
  swyft:           { display_name: 'Swyft',         tagline: 'Logistics for Bharat',              min_version_bump: 'minor' },
  'bharat-bandhu': { display_name: 'Bharat Bandhu', tagline: 'Communities for Bharat',            min_version_bump: 'patch' },
  school:          { display_name: 'School',        tagline: 'Education Management for Bharat',   min_version_bump: 'minor' },
  wedding:         { display_name: 'Wedding',       tagline: 'Weddings, Made Magical',            min_version_bump: 'minor' },
  control:         { display_name: 'Control',       tagline: 'Mission Control for Tanvrit',       min_version_bump: 'patch' },
};

// ── Per-product platform lists ────────────────────────────────────────────────
const PLATFORM_CONFIGS = {
  friendly:        ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  desipops:        ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  mandee:          ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  swyft:           ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  school:          ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  wedding:         ['macos-arm64', 'web'],
  'bharat-bandhu': ['android'],
  control:         ['macos-universal', 'macos-arm64'],
};

const PLATFORM_EXT = {
  'macos-arm64':    'dmg',
  'macos-x64':      'dmg',
  'macos-universal':'dmg',
  'windows-x64':    'msi',
  'linux-x64':      'deb',
  'android':        'apk',
  'web':            null,
};

const WEB_URLS = {
  wedding: 'https://friendly.wedding',
};

// ── HTTP helpers ──────────────────────────────────────────────────────────────
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const follow = (u) => https.get(u, { timeout: 8000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return follow(res.headers.location);
      }
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    }).on('error', reject).on('timeout', () => reject(new Error('timeout')));
    follow(url);
  });
}

function httpHead(url) {
  return new Promise((resolve, reject) => {
    const follow = (u) => {
      const mod = u.startsWith('https') ? https : require('http');
      mod.request(u, { method: 'HEAD', timeout: 8000 }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          return follow(res.headers.location);
        }
        resolve({ status: res.statusCode, headers: res.headers });
      }).on('error', reject).on('timeout', () => reject(new Error('timeout'))).end();
    };
    follow(url);
  });
}

async function fetchSha256(tagName, filename) {
  try {
    const { status, body } = await httpGet(`${GH_DOWNLOAD}/${tagName}/${filename}.sha256`);
    if (status !== 200) return '';
    return body.trim().split(/\s+/)[0] || '';
  } catch { return ''; }
}

async function fetchSize(tagName, filename) {
  try {
    const { status, headers } = await httpHead(`${GH_DOWNLOAD}/${tagName}/${filename}`);
    if (status !== 200) return 0;
    return parseInt(headers['content-length'] || '0', 10) || 0;
  } catch { return 0; }
}

// ── Manifest builder ──────────────────────────────────────────────────────────
function minRequiredVersion(version, bump) {
  const [major, minor, patch] = version.split('.').map(Number);
  if (bump === 'major') return `${major}.0.0`;
  if (bump === 'minor') return `${major}.${minor}.0`;
  return `${major}.${minor}.${patch}`;
}

async function buildManifest(product, version, build, releasedAt, releaseNotes, releaseNotesHi, skipFetch) {
  const meta    = PRODUCT_META[product] || { display_name: product, tagline: '', min_version_bump: 'minor' };
  const tagName = `${product}-v${version}`;
  const platforms = {};

  for (const platform of (PLATFORM_CONFIGS[product] || [])) {
    const ext = PLATFORM_EXT[platform];

    if (!ext) {
      // Web platform
      const webUrl = WEB_URLS[product] || `${BASE_URL}/${product}`;
      platforms[platform] = { url: webUrl, direct_url: webUrl, sha256: '', size_bytes: 0, format: 'web', available: true };
      continue;
    }

    const filename = `${product}-${version}-${platform}.${ext}`;
    let sha256 = '', size_bytes = 0;

    if (!skipFetch) {
      process.stdout.write(`    fetching ${platform} metadata…`);
      [sha256, size_bytes] = await Promise.all([
        fetchSha256(tagName, filename),
        fetchSize(tagName, filename),
      ]);
      process.stdout.write(sha256 ? ` ✓ sha256 ok, ${(size_bytes/1024/1024).toFixed(1)} MB\n` : ` – not found\n`);
    }

    platforms[platform] = {
      url:        `${BASE_URL}/${product}/${version}/${platform}`,
      direct_url: `${GH_DOWNLOAD}/${tagName}/${filename}`,
      sha256,
      size_bytes,
      format:     ext,
      available:  !!sha256 || skipFetch,   // only available if we confirmed the file exists
    };
  }

  const notes    = releaseNotes   || `See ${GH_RELEASES}/tag/${tagName}`;
  const notes_hi = releaseNotesHi || '';

  return {
    product,
    display_name:          meta.display_name,
    tagline:               meta.tagline,
    version,
    build,
    released_at:           releasedAt,
    release_notes:         notes,
    release_notes_hi:      notes_hi,
    min_required_version:  minRequiredVersion(version, meta.min_version_bump),
    platforms,
  };
}

function updateCatalog(product, version, build, releasedAt) {
  const catalogPath = path.join(MANIFESTS, 'catalog.json');
  const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
  catalog.updated_at = new Date().toISOString();
  catalog.products[product] = {
    version,
    build,
    released_at:  releasedAt,
    manifest_url: `${BASE_URL}/${product}/latest.json`,
  };
  fs.writeFileSync(catalogPath, JSON.stringify(catalog, null, 2) + '\n');
  console.log(`  ✓ catalog.json updated`);
}

// ── CLI ───────────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  const get  = (flag) => { const i = args.indexOf(flag); return i >= 0 ? args[i + 1] : null; };
  const has  = (flag) => args.includes(flag);

  const product       = get('--product');
  const version       = get('--version');
  const build         = parseInt(get('--build') || '0', 10);
  const releasedAt    = get('--released-at') || new Date().toISOString();
  const releaseNotes  = get('--release-notes') || '';
  const releaseNotesHi= get('--release-notes-hi') || '';
  const skipFetch     = has('--skip-fetch');

  const VALID = Object.keys(PLATFORM_CONFIGS);

  if (!product || !version) {
    console.log('Usage: node update-manifest.js --product <slug> --version <x.y.z> --build <n> [options]');
    console.log('Options: --released-at <ISO> --release-notes <text> --release-notes-hi <text> --skip-fetch');
    process.exit(1);
  }
  if (!VALID.includes(product)) {
    console.error(`Unknown product: ${product}. Valid: ${VALID.join(', ')}`);
    process.exit(1);
  }

  console.log(`\nUpdating manifest: ${product} v${version} (build ${build})`);
  if (!skipFetch) console.log('  Fetching SHA256 + sizes from GitHub Releases…');

  const manifest = await buildManifest(product, version, build, releasedAt, releaseNotes, releaseNotesHi, skipFetch);

  const productDir = path.join(MANIFESTS, product);
  fs.mkdirSync(productDir, { recursive: true });

  const latestPath    = path.join(productDir, 'latest.json');
  const versionedPath = path.join(productDir, `${version}.json`);

  fs.writeFileSync(latestPath,    JSON.stringify(manifest, null, 2) + '\n');
  fs.writeFileSync(versionedPath, JSON.stringify(manifest, null, 2) + '\n');

  console.log(`  ✓ manifests/${product}/latest.json`);
  console.log(`  ✓ manifests/${product}/${version}.json`);

  updateCatalog(product, version, build, releasedAt);

  const available = Object.values(manifest.platforms).filter(p => p.available).length;
  const total     = Object.keys(manifest.platforms).length;
  console.log(`\n✓ Done. ${available}/${total} platforms available. Commit and push manifests/\n`);
}

main().catch((err) => { console.error(err); process.exit(1); });
