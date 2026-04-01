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

let errors = 0;
let warnings = 0;

for (const check of CHECKS) {
  const filePath = path.join(DIST, check.product, check.platform, check.file);
  if (!fs.existsSync(filePath)) {
    // macOS .icns files are macOS-only — treat as warning on non-macOS
    if (check.file.endsWith('.icns') && process.platform !== 'darwin') {
      process.stderr.write(`  ⚠ (expected on macOS only): ${check.product}/${check.platform}/${check.file}\n`);
      warnings++;
    } else {
      process.stderr.write(`  ✗ MISSING: ${check.product}/${check.platform}/${check.file}\n`);
      errors++;
    }
  } else {
    const stat = fs.statSync(filePath);
    if (stat.size === 0) {
      process.stderr.write(`  ✗ EMPTY: ${check.product}/${check.platform}/${check.file}\n`);
      errors++;
    }
  }
}

if (errors === 0) {
  process.stdout.write(`\n✓ All ${CHECKS.length} asset checks passed (${warnings} macOS warnings)\n\n`);
  process.exit(0);
} else {
  process.stderr.write(`\n✗ ${errors} missing assets. Run: node generate-icons.js --all\n\n`);
  process.exit(1);
}
