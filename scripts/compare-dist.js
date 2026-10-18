#!/usr/bin/env node
/**
 * Report how the regenerated branding/icons/dist differs from the committed one:
 * byte changes versus visible pixel changes. Informational only; always exits 0.
 *
 * A regeneration on a different machine (Linux X64 instead of the macmini, a sharp
 * or Inkscape upgrade) rewrites many PNG/WebP files whose pixels barely move, and
 * a byte-level `git status` cannot tell that apart from a real icon change. This
 * decodes each changed raster in both versions and reports the largest per-channel
 * difference, ignoring colour under fully transparent pixels.
 *
 * Usage (repo root, after generate-icons.js): node scripts/compare-dist.js [--ref HEAD]
 */

'use strict';

const sharp = require('sharp');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const DIST = 'branding/icons/dist';
const refArg = process.argv.indexOf('--ref');
const REF = refArg >= 0 ? process.argv[refArg + 1] : 'HEAD';
const RASTER = new Set(['.png', '.webp']);

function git(args, opts = {}) {
  return execFileSync('git', args, { cwd: ROOT, maxBuffer: 256 * 1024 * 1024, ...opts });
}

async function rgba(input) {
  const { data, info } = await sharp(input).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height };
}

async function maxPixelDiff(file) {
  const before = await rgba(git(['show', `${REF}:${file}`]));
  const after = await rgba(path.join(ROOT, file));
  if (before.width !== after.width || before.height !== after.height) {
    return { max: Infinity, over: after.width * after.height, pixels: after.width * after.height };
  }
  let max = 0;
  let over = 0; // pixels where some channel moved by more than 2/255
  for (let i = 0; i < after.data.length; i += 4) {
    let px = Math.abs(after.data[i + 3] - before.data[i + 3]);
    if (after.data[i + 3] !== 0 || before.data[i + 3] !== 0) {
      for (let c = 0; c < 3; c++) px = Math.max(px, Math.abs(after.data[i + c] - before.data[i + c]));
    }
    if (px > max) max = px;
    if (px > 2) over++;
  }
  return { max, over, pixels: after.width * after.height };
}

async function main() {
  const lines = git(['status', '--porcelain', '--', DIST], { encoding: 'utf8' })
    .split('\n').filter(Boolean);
  const modified = lines.filter((l) => l.startsWith(' M ')).map((l) => l.slice(3));
  const other = lines.filter((l) => !l.startsWith(' M '));

  const buckets = new Map(); // max diff -> count
  const visible = [];
  const nonRaster = {};
  for (const file of modified) {
    const ext = path.extname(file).toLowerCase();
    if (!RASTER.has(ext)) {
      nonRaster[ext] = (nonRaster[ext] || 0) + 1;
      continue;
    }
    let diff;
    let detail;
    try {
      const r = await maxPixelDiff(file);
      diff = r.max;
      detail = `max ${r.max}/255, ${r.over} of ${r.pixels} px moved by more than 2`;
    } catch (e) {
      diff = 'unreadable';
      detail = e.message;
    }
    buckets.set(diff, (buckets.get(diff) || 0) + 1);
    if (typeof diff !== 'number' || diff > 2) visible.push(`${file}: ${detail}`);
  }

  const out = [];
  out.push(`Compared against ${REF}: ${modified.length} modified, ${other.length} added/removed.`);
  const sorted = [...buckets.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0]), 'en', { numeric: true }));
  for (const [diff, count] of sorted) out.push(`  raster files with max channel diff ${diff}: ${count}`);
  for (const [ext, count] of Object.entries(nonRaster)) out.push(`  ${ext || '(no ext)'} files rewritten (not decoded): ${count}`);
  if (visible.length) {
    out.push(`  rasters changed by more than 2/255 (${visible.length}):`);
    for (const v of visible.slice(0, 60)) out.push(`    ${v}`);
  }
  for (const l of other.slice(0, 20)) out.push(`  ${l}`);
  console.log(out.join('\n'));
}

main().catch((err) => {
  console.log(`compare-dist: could not compare (${err.message})`);
});
