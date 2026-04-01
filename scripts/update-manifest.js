#!/usr/bin/env node
/**
 * Update a product's release manifest locally.
 * Use this when doing a local release without GitHub Actions.
 *
 * Usage:
 *   node update-manifest.js --product friendly --version 1.2.0 --build 120
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const MANIFESTS = path.join(ROOT, 'manifests');

const PRODUCTS = ['friendly','desipops','mandee','swyft','bharat-bandhu','school','wedding','control'];
const BASE_URL = 'https://artifacts.tanvrit.com';
const GH_RELEASE_BASE = 'https://github.com/tanvrit/artifactory/releases/download';

const PLATFORM_CONFIGS = {
  friendly:      ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  desipops:      ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  mandee:        ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  swyft:         ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  school:        ['macos-arm64', 'macos-x64', 'windows-x64', 'linux-x64'],
  wedding:       ['macos-arm64', 'web'],
  'bharat-bandhu': ['android'],
  control:       ['macos-universal', 'macos-arm64'],
};

const PLATFORM_EXT = {
  'macos-arm64': 'dmg',
  'macos-x64': 'dmg',
  'macos-universal': 'dmg',
  'windows-x64': 'msi',
  'linux-x64': 'deb',
  'android': 'apk',
  'web': null,
};

function buildManifest(product, version, build, releasedAt) {
  const platforms = {};
  for (const platform of (PLATFORM_CONFIGS[product] || [])) {
    const ext = PLATFORM_EXT[platform];
    if (!ext) {
      // Web platform — direct URL
      const webUrls = { wedding: 'https://friendly.wedding' };
      platforms[platform] = {
        url: webUrls[product] || `${BASE_URL}/${product}`,
        direct_url: webUrls[product] || '',
        sha256: '',
        size_bytes: 0,
        format: 'web',
        available: true,
      };
      continue;
    }

    const filename = `${product}-${version}-${platform}.${ext}`;
    const tagName = `${product}-v${version}`;

    platforms[platform] = {
      url: `${BASE_URL}/${product}/${version}/${platform}`,
      direct_url: `${GH_RELEASE_BASE}/${tagName}/${filename}`,
      sha256: '',
      size_bytes: 0,
      format: ext,
      available: true,
    };
  }

  return {
    product,
    display_name: product.charAt(0).toUpperCase() + product.slice(1).replace(/-/g, ' '),
    version,
    build,
    released_at: releasedAt,
    release_notes: `See ${GH_RELEASE_BASE.replace('/download','')}/tag/${product}-v${version}`,
    min_required_version: version.split('.').slice(0,2).join('.') + '.0',
    platforms,
  };
}

function updateCatalog(product, version, build, releasedAt) {
  const catalogPath = path.join(MANIFESTS, 'catalog.json');
  const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));
  catalog.updated_at = new Date().toISOString();
  catalog.products[product] = { version, build, released_at: releasedAt, manifest_url: `${BASE_URL}/${product}/latest.json` };
  fs.writeFileSync(catalogPath, JSON.stringify(catalog, null, 2) + '\n');
  console.log(`  ✓ catalog.json updated`);
}

function main() {
  const args = process.argv.slice(2);
  const get = (flag) => {
    const idx = args.indexOf(flag);
    return idx >= 0 ? args[idx + 1] : null;
  };

  const product  = get('--product');
  const version  = get('--version');
  const build    = parseInt(get('--build') || '0', 10);
  const releasedAt = get('--released-at') || new Date().toISOString();

  if (!product || !version) {
    console.log('Usage: node update-manifest.js --product friendly --version 1.2.0 --build 120');
    process.exit(1);
  }
  if (!PRODUCTS.includes(product)) {
    console.error(`Unknown product: ${product}. Valid: ${PRODUCTS.join(', ')}`);
    process.exit(1);
  }

  console.log(`\nUpdating manifest: ${product} v${version} (build ${build})`);

  const manifest = buildManifest(product, version, build, releasedAt);
  const productDir = path.join(MANIFESTS, product);
  fs.mkdirSync(productDir, { recursive: true });

  // Write latest.json
  const latestPath = path.join(productDir, 'latest.json');
  fs.writeFileSync(latestPath, JSON.stringify(manifest, null, 2) + '\n');
  console.log(`  ✓ manifests/${product}/latest.json`);

  // Write versioned manifest
  const versionedPath = path.join(productDir, `${version}.json`);
  fs.writeFileSync(versionedPath, JSON.stringify(manifest, null, 2) + '\n');
  console.log(`  ✓ manifests/${product}/${version}.json`);

  // Update catalog
  updateCatalog(product, version, build, releasedAt);

  console.log(`\n✓ Done. Don't forget to commit and push manifests/\n`);
}

main();
