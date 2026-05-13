#!/usr/bin/env node
/**
 * Bulk upload to R2 bucket tanvrit-artifacts
 * Uploads manifests, branding source SVGs, and branding dist icons.
 *
 * Usage:
 *   node upload-to-r2.js                          # upload everything
 *   node upload-to-r2.js --products foo,bar        # upload specific dist products only
 *   node upload-to-r2.js --dry-run
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const BUCKET = 'tanvrit-artifacts';
const CONCURRENCY = 6;
const DRY_RUN = process.argv.includes('--dry-run');

const productArg = process.argv.find(a => a.startsWith('--products='))?.split('=')[1]
  || (process.argv.indexOf('--products') >= 0 ? process.argv[process.argv.indexOf('--products') + 1] : null);
const FILTER_PRODUCTS = productArg ? productArg.split(',').map(p => p.trim()) : null;

const MIME_MAP = {
  '.json':  'application/json',
  '.svg':   'image/svg+xml',
  '.png':   'image/png',
  '.webp':  'image/webp',
  '.ico':   'image/x-icon',
  '.icns':  'image/x-icns',
  '.xml':   'application/xml',
  '.zip':   'application/zip',
  '.txt':   'text/plain',
};

function getMime(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return MIME_MAP[ext] || 'application/octet-stream';
}

function collectFiles(dir, r2Prefix) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const localPath = path.join(dir, entry.name);
    const r2Key = `${r2Prefix}/${entry.name}`;
    if (entry.isDirectory()) {
      results.push(...collectFiles(localPath, r2Key));
    } else {
      results.push({ localPath, r2Key });
    }
  }
  return results;
}

function upload({ localPath, r2Key }) {
  const mime = getMime(localPath);
  const dest = `${BUCKET}/${r2Key}`;
  if (DRY_RUN) {
    console.log(`[DRY] ${r2Key}  (${mime})`);
    return { ok: true, r2Key };
  }
  const result = spawnSync('wrangler', [
    'r2', 'object', 'put', dest,
    '--file', localPath,
    '--content-type', mime,
    '--remote',
  ], { stdio: 'pipe', encoding: 'utf8' });

  if (result.status !== 0) {
    console.error(`  ✗ ${r2Key}: ${(result.stderr || '').trim().split('\n').pop()}`);
    return { ok: false, r2Key };
  }
  console.log(`  ✓ ${r2Key}`);
  return { ok: true, r2Key };
}

async function uploadBatch(files) {
  const results = [];
  for (let i = 0; i < files.length; i += CONCURRENCY) {
    const batch = files.slice(i, i + CONCURRENCY);
    const batchResults = await Promise.all(
      batch.map(f => Promise.resolve(upload(f)))
    );
    results.push(...batchResults);
  }
  return results;
}

async function main() {
  let distProducts;
  if (FILTER_PRODUCTS) {
    distProducts = FILTER_PRODUCTS;
    console.log(`\nR2 Upload: ${BUCKET} (products: ${FILTER_PRODUCTS.join(', ')})`);
  } else {
    distProducts = fs.existsSync(path.join(ROOT, 'branding/icons/dist'))
      ? fs.readdirSync(path.join(ROOT, 'branding/icons/dist'))
      : [];
    console.log(`\nR2 Upload: ${BUCKET} (all)`);
  }

  const files = [
    // Always include manifests and source SVGs when doing full upload
    ...(FILTER_PRODUCTS ? [] : [
      ...collectFiles(path.join(ROOT, 'manifests'), 'manifests'),
      ...collectFiles(path.join(ROOT, 'branding/icons/src'), 'branding/icons/src'),
    ]),
    // Dist icons — either filtered products or all
    ...distProducts.flatMap(p =>
      collectFiles(
        path.join(ROOT, 'branding/icons/dist', p),
        `branding/icons/dist/${p}`
      )
    ),
  ];

  console.log(`Files to upload: ${files.length}${DRY_RUN ? ' [DRY RUN]' : ''}\n`);

  const results = await uploadBatch(files);

  const ok  = results.filter(r => r.ok).length;
  const err = results.filter(r => !r.ok).length;
  console.log(`\n${DRY_RUN ? '[DRY RUN] ' : ''}Done: ${ok} uploaded${err > 0 ? `, ${err} failed` : ''}\n`);
  if (err > 0) process.exit(1);
}

main().catch(err => {
  console.error('Upload failed:', err);
  process.exit(1);
});
