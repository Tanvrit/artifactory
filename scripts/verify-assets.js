#!/usr/bin/env node
/**
 * Verify that all expected icon exports exist after generation.
 * Exit code 0 = all good, 1 = missing files.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const DIST = path.join(ROOT, 'branding/icons/dist');

const CHECKS = [
  // Tanvrit umbrella — all platforms
  { product: 'tanvrit', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'tanvrit', platform: 'ios', file: 'AppIcon.appiconset/tanvrit-icon-1024.png' },
  { product: 'tanvrit', platform: 'macos', file: 'tanvrit.icns' },
  { product: 'tanvrit', platform: 'windows', file: 'tanvrit.ico' },
  { product: 'tanvrit', platform: 'linux', file: 'tanvrit-256.png' },
  { product: 'tanvrit', platform: 'web', file: 'favicon.ico' },
  { product: 'tanvrit', platform: 'web', file: 'icon.svg' },
  { product: 'tanvrit', platform: 'web', file: 'apple-touch-icon.png' },
  { product: 'tanvrit', platform: 'web', file: 'icon-512-maskable.png' },

  // Friendly
  { product: 'friendly', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'friendly', platform: 'ios', file: 'AppIcon.appiconset/friendly-icon-1024.png' },
  { product: 'friendly', platform: 'macos', file: 'friendly.icns' },
  { product: 'friendly', platform: 'windows', file: 'friendly.ico' },
  { product: 'friendly', platform: 'linux', file: 'friendly-256.png' },
  { product: 'friendly', platform: 'web', file: 'favicon.ico' },

  // DesiPops
  { product: 'desipops', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'desipops', platform: 'macos', file: 'desipops.icns' },
  { product: 'desipops', platform: 'windows', file: 'desipops.ico' },

  // Mandee
  { product: 'mandee', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'mandee', platform: 'macos', file: 'mandee.icns' },
  { product: 'mandee', platform: 'windows', file: 'mandee.ico' },

  // Swyft
  { product: 'swyft', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'swyft', platform: 'macos', file: 'swyft.icns' },

  // School
  { product: 'school', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'school', platform: 'macos', file: 'school.icns' },

  // Wedding
  { product: 'wedding', platform: 'android', file: 'mipmap-xxxhdpi/ic_launcher.webp' },
  { product: 'wedding', platform: 'web', file: 'favicon.ico' },

  // Control
  { product: 'control', platform: 'macos', file: 'control.icns' },
];

// The .icns entries generate-icons.js writes (ICNS_ENTRIES there). A file that
// exists but lacks one is as broken as a missing file.
const REQUIRED_ICNS_TYPES = ['ic04', 'ic05', 'ic07', 'ic08', 'ic09', 'ic10', 'ic11', 'ic12', 'ic13', 'ic14'];

// Returns a problem description, or null when the container is well formed.
function icnsProblem(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.length < 8 || buf.toString('latin1', 0, 4) !== 'icns') return 'no icns magic';
  if (buf.readUInt32BE(4) !== buf.length) return `header says ${buf.readUInt32BE(4)} bytes, file is ${buf.length}`;
  const seen = new Set();
  for (let off = 8; off < buf.length;) {
    if (off + 8 > buf.length) return `truncated entry header at ${off}`;
    const len = buf.readUInt32BE(off + 4);
    if (len < 8 || off + len > buf.length) return `entry ${buf.toString('latin1', off, off + 4)} overruns the file`;
    seen.add(buf.toString('latin1', off, off + 4));
    off += len;
  }
  const missing = REQUIRED_ICNS_TYPES.filter((t) => !seen.has(t));
  return missing.length ? `missing entries ${missing.join(', ')}` : null;
}

let errors = 0;

for (const check of CHECKS) {
  const filePath = path.join(DIST, check.product, check.platform, check.file);
  // .icns used to be a warning off macOS, because only iconutil could write it.
  // generate-icons.js now writes it on every OS, so a missing one is an error
  // everywhere; the downgrade is what let a Linux run go green without them.
  if (!fs.existsSync(filePath)) {
    process.stderr.write(`  ✗ MISSING: ${check.product}/${check.platform}/${check.file}\n`);
    errors++;
  } else {
    const stat = fs.statSync(filePath);
    if (stat.size === 0) {
      process.stderr.write(`  ✗ EMPTY: ${check.product}/${check.platform}/${check.file}\n`);
      errors++;
    } else if (check.file.endsWith('.icns')) {
      const problem = icnsProblem(filePath);
      if (problem) {
        process.stderr.write(`  ✗ BAD ICNS: ${check.product}/${check.platform}/${check.file} — ${problem}\n`);
        errors++;
      }
    }
  }
}

if (errors === 0) {
  process.stdout.write(`\n✓ All ${CHECKS.length} asset checks passed\n\n`);
  process.exit(0);
} else {
  process.stderr.write(`\n✗ ${errors} missing or malformed assets. Run: node generate-icons.js --all\n\n`);
  process.exit(1);
}
