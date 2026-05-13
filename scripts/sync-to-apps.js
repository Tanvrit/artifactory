#!/usr/bin/env node
/**
 * Tanvrit Icon Sync — artifactory → product repos
 *
 * Copies generated icons from branding/icons/dist/ into each product's
 * expected resource directory in the Tanvrit monorepo.
 *
 * Usage:
 *   node sync-to-apps.js --all
 *   node sync-to-apps.js --product friendly
 *   node sync-to-apps.js --dry-run --all
 *
 * Set TANVRIT_ROOT env var if the monorepo is not at the default location.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const TANVRIT_ROOT = process.env.TANVRIT_ROOT || '/Users/viveksingh/Developer/tanvrit';
const ARTIFACTORY_ROOT = path.join(__dirname, '..');
const DIST = path.join(ARTIFACTORY_ROOT, 'branding/icons/dist');

// ── Sync Map ─────────────────────────────────────────────────
// Each entry: { from: relative to DIST, to: relative to TANVRIT_ROOT }
const SYNC_MAP = {
  friendly: [
    // Android mipmaps
    { from: 'friendly/android/mipmap-mdpi',    to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'friendly/android/mipmap-hdpi',    to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'friendly/android/mipmap-xhdpi',   to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'friendly/android/mipmap-xxhdpi',  to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'friendly/android/mipmap-xxxhdpi', to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'friendly/android/mipmap-anydpi-v26', to: 'platforms/friendly/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    // iOS
    { from: 'friendly/ios/AppIcon.appiconset', to: 'platforms/friendly/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    // Desktop (for compose.desktop packaging)
    { from: 'friendly/macos/friendly.icns',    to: 'platforms/friendly/composeApp/src/desktopMain/resources/friendly.icns', isFile: true },
    { from: 'friendly/windows/friendly.ico',   to: 'platforms/friendly/composeApp/src/desktopMain/resources/friendly.ico',  isFile: true },
    { from: 'friendly/linux/friendly-256.png', to: 'platforms/friendly/composeApp/src/desktopMain/resources/friendly-256.png', isFile: true },
  ],
  desipops: [
    { from: 'desipops/android/mipmap-mdpi',    to: 'client/desipops/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'desipops/android/mipmap-hdpi',    to: 'client/desipops/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'desipops/android/mipmap-xhdpi',   to: 'client/desipops/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'desipops/android/mipmap-xxhdpi',  to: 'client/desipops/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'desipops/android/mipmap-xxxhdpi', to: 'client/desipops/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'desipops/android/mipmap-anydpi-v26', to: 'client/desipops/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'desipops/ios/AppIcon.appiconset', to: 'client/desipops/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'desipops/macos/desipops.icns',    to: 'client/desipops/composeApp/src/desktopMain/resources/desipops.icns', isFile: true },
    { from: 'desipops/windows/desipops.ico',   to: 'client/desipops/composeApp/src/desktopMain/resources/desipops.ico',  isFile: true },
    { from: 'desipops/linux/desipops-256.png', to: 'client/desipops/composeApp/src/desktopMain/resources/desipops-256.png', isFile: true },
  ],
  mandee: [
    { from: 'mandee/android/mipmap-mdpi',    to: 'platforms/mandee/androidApp/src/main/res/mipmap-mdpi' },
    { from: 'mandee/android/mipmap-hdpi',    to: 'platforms/mandee/androidApp/src/main/res/mipmap-hdpi' },
    { from: 'mandee/android/mipmap-xhdpi',   to: 'platforms/mandee/androidApp/src/main/res/mipmap-xhdpi' },
    { from: 'mandee/android/mipmap-xxhdpi',  to: 'platforms/mandee/androidApp/src/main/res/mipmap-xxhdpi' },
    { from: 'mandee/android/mipmap-xxxhdpi', to: 'platforms/mandee/androidApp/src/main/res/mipmap-xxxhdpi' },
    { from: 'mandee/android/mipmap-anydpi-v26', to: 'platforms/mandee/androidApp/src/main/res/mipmap-anydpi-v26' },
    { from: 'mandee/ios/AppIcon.appiconset', to: 'platforms/mandee/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'mandee/macos/mandee.icns',      to: 'platforms/mandee/composeApp/src/desktopMain/resources/mandee.icns', isFile: true },
    { from: 'mandee/windows/mandee.ico',     to: 'platforms/mandee/composeApp/src/desktopMain/resources/mandee.ico',  isFile: true },
    { from: 'mandee/linux/mandee-256.png',   to: 'platforms/mandee/composeApp/src/desktopMain/resources/mandee-256.png', isFile: true },
  ],
  swyft: [
    { from: 'swyft/android/mipmap-mdpi',    to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'swyft/android/mipmap-hdpi',    to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'swyft/android/mipmap-xhdpi',   to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'swyft/android/mipmap-xxhdpi',  to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'swyft/android/mipmap-xxxhdpi', to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'swyft/android/mipmap-anydpi-v26', to: 'platforms/swyft/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'swyft/ios/AppIcon.appiconset', to: 'platforms/swyft/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'swyft/macos/swyft.icns',       to: 'platforms/swyft/composeApp/src/desktopMain/resources/swyft.icns', isFile: true },
    { from: 'swyft/windows/swyft.ico',      to: 'platforms/swyft/composeApp/src/desktopMain/resources/swyft.ico',  isFile: true },
    { from: 'swyft/linux/swyft-256.png',    to: 'platforms/swyft/composeApp/src/desktopMain/resources/swyft-256.png', isFile: true },
  ],
  school: [
    { from: 'school/android/mipmap-mdpi',    to: 'platforms/school/app/src/main/res/mipmap-mdpi' },
    { from: 'school/android/mipmap-hdpi',    to: 'platforms/school/app/src/main/res/mipmap-hdpi' },
    { from: 'school/android/mipmap-xhdpi',   to: 'platforms/school/app/src/main/res/mipmap-xhdpi' },
    { from: 'school/android/mipmap-xxhdpi',  to: 'platforms/school/app/src/main/res/mipmap-xxhdpi' },
    { from: 'school/android/mipmap-xxxhdpi', to: 'platforms/school/app/src/main/res/mipmap-xxxhdpi' },
    { from: 'school/android/mipmap-anydpi-v26', to: 'platforms/school/app/src/main/res/mipmap-anydpi-v26' },
    { from: 'school/ios/AppIcon.appiconset', to: 'platforms/school/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'school/macos/school.icns',      to: 'platforms/school/app/src/desktopMain/resources/school.icns', isFile: true },
    { from: 'school/windows/school.ico',     to: 'platforms/school/app/src/desktopMain/resources/school.ico',  isFile: true },
    { from: 'school/linux/school-256.png',   to: 'platforms/school/app/src/desktopMain/resources/school-256.png', isFile: true },
  ],
  wedding: [
    { from: 'wedding/android/mipmap-mdpi',    to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'wedding/android/mipmap-hdpi',    to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'wedding/android/mipmap-xhdpi',   to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'wedding/android/mipmap-xxhdpi',  to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'wedding/android/mipmap-xxxhdpi', to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'wedding/android/mipmap-anydpi-v26', to: 'platforms/wedding/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'wedding/ios/AppIcon.appiconset', to: 'platforms/wedding/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
  ],
  control: [
    { from: 'control/macos/control.icns', to: 'control/app/src/main/resources/control.icns', isFile: true },
  ],
  ai: [
    { from: 'ai/android/mipmap-mdpi',    to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'ai/android/mipmap-hdpi',    to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'ai/android/mipmap-xhdpi',   to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'ai/android/mipmap-xxhdpi',  to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'ai/android/mipmap-xxxhdpi', to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'ai/android/mipmap-anydpi-v26', to: 'platforms/ai/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'ai/ios/AppIcon.appiconset', to: 'platforms/ai/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'ai/macos/ai.icns',          to: 'platforms/ai/composeApp/src/desktopMain/resources/ai.icns', isFile: true },
    { from: 'ai/windows/ai.ico',         to: 'platforms/ai/composeApp/src/desktopMain/resources/ai.ico', isFile: true },
    { from: 'ai/linux/ai-256.png',       to: 'platforms/ai/composeApp/src/desktopMain/resources/ai-256.png', isFile: true },
  ],
  auditor: [
    { from: 'auditor/android/mipmap-mdpi',    to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'auditor/android/mipmap-hdpi',    to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'auditor/android/mipmap-xhdpi',   to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'auditor/android/mipmap-xxhdpi',  to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'auditor/android/mipmap-xxxhdpi', to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'auditor/android/mipmap-anydpi-v26', to: 'platforms/auditor/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'auditor/ios/AppIcon.appiconset', to: 'platforms/auditor/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'auditor/macos/auditor.icns',     to: 'platforms/auditor/composeApp/src/desktopMain/resources/auditor.icns', isFile: true },
    { from: 'auditor/windows/auditor.ico',    to: 'platforms/auditor/composeApp/src/desktopMain/resources/auditor.ico', isFile: true },
    { from: 'auditor/linux/auditor-256.png',  to: 'platforms/auditor/composeApp/src/desktopMain/resources/auditor-256.png', isFile: true },
  ],
  automator: [
    { from: 'automator/android/mipmap-mdpi',    to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-mdpi' },
    { from: 'automator/android/mipmap-hdpi',    to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-hdpi' },
    { from: 'automator/android/mipmap-xhdpi',   to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-xhdpi' },
    { from: 'automator/android/mipmap-xxhdpi',  to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'automator/android/mipmap-xxxhdpi', to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'automator/android/mipmap-anydpi-v26', to: 'platforms/automator/composeApp/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'automator/ios/AppIcon.appiconset', to: 'platforms/automator/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'automator/windows/automator.ico',  to: 'platforms/automator/composeApp/icons/automator.ico', isFile: true },
    // NOTE: automator.icns + automator-256.png already exist in composeApp/icons/ from prior manual work;
    // don't overwrite via sync. If you want a Tanvrit-mark-derived icon, delete the existing files first.
  ],
  market: [
    { from: 'market/android/mipmap-mdpi',    to: 'platforms/market/ui/ui-android/src/main/res/mipmap-mdpi' },
    { from: 'market/android/mipmap-hdpi',    to: 'platforms/market/ui/ui-android/src/main/res/mipmap-hdpi' },
    { from: 'market/android/mipmap-xhdpi',   to: 'platforms/market/ui/ui-android/src/main/res/mipmap-xhdpi' },
    { from: 'market/android/mipmap-xxhdpi',  to: 'platforms/market/ui/ui-android/src/main/res/mipmap-xxhdpi' },
    { from: 'market/android/mipmap-xxxhdpi', to: 'platforms/market/ui/ui-android/src/main/res/mipmap-xxxhdpi' },
    { from: 'market/android/mipmap-anydpi-v26', to: 'platforms/market/ui/ui-android/src/main/res/mipmap-anydpi-v26' },
    // market's compose.desktop config in ui/ui-desktop/build.gradle.kts references
    // src/desktopMain/resources/icon.icns + icon.png (generic names, not <product>.icns).
    { from: 'market/macos/market.icns',     to: 'platforms/market/ui/ui-desktop/src/desktopMain/resources/icon.icns', isFile: true },
    { from: 'market/linux/market-256.png',  to: 'platforms/market/ui/ui-desktop/src/desktopMain/resources/icon.png', isFile: true },
  ],
  admin: [
    { from: 'admin/android/mipmap-mdpi',    to: 'platforms/admin/app/src/androidMain/res/mipmap-mdpi' },
    { from: 'admin/android/mipmap-hdpi',    to: 'platforms/admin/app/src/androidMain/res/mipmap-hdpi' },
    { from: 'admin/android/mipmap-xhdpi',   to: 'platforms/admin/app/src/androidMain/res/mipmap-xhdpi' },
    { from: 'admin/android/mipmap-xxhdpi',  to: 'platforms/admin/app/src/androidMain/res/mipmap-xxhdpi' },
    { from: 'admin/android/mipmap-xxxhdpi', to: 'platforms/admin/app/src/androidMain/res/mipmap-xxxhdpi' },
    { from: 'admin/android/mipmap-anydpi-v26', to: 'platforms/admin/app/src/androidMain/res/mipmap-anydpi-v26' },
    { from: 'admin/ios/AppIcon.appiconset', to: 'platforms/admin/iosApp/iosApp/Assets.xcassets/AppIcon.appiconset' },
    { from: 'admin/macos/admin.icns',       to: 'platforms/admin/app/icons/admin.icns', isFile: true },
    { from: 'admin/windows/admin.ico',      to: 'platforms/admin/app/icons/admin.ico', isFile: true },
    { from: 'admin/linux/admin-256.png',    to: 'platforms/admin/app/icons/admin-256.png', isFile: true },
  ],
};

// ── Copy helpers ─────────────────────────────────────────────

function copyDir(src, dest, dryRun) {
  if (!fs.existsSync(src)) {
    process.stderr.write(`  ⚠ Source not found: ${src}\n`);
    return 0;
  }
  fs.mkdirSync(dest, { recursive: true });
  let count = 0;
  for (const item of fs.readdirSync(src)) {
    const srcItem = path.join(src, item);
    const destItem = path.join(dest, item);
    if (fs.statSync(srcItem).isDirectory()) {
      count += copyDir(srcItem, destItem, dryRun);
    } else {
      if (!dryRun) fs.copyFileSync(srcItem, destItem);
      process.stdout.write(`  ${dryRun ? '[dry] ' : ''}→ ${destItem}\n`);
      count++;
    }
  }
  return count;
}

function copyFile(src, dest, dryRun) {
  if (!fs.existsSync(src)) {
    process.stderr.write(`  ⚠ Source not found: ${src}\n`);
    return 0;
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  if (!dryRun) fs.copyFileSync(src, dest);
  process.stdout.write(`  ${dryRun ? '[dry] ' : ''}→ ${dest}\n`);
  return 1;
}

// ── Main ─────────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const allFlag = args.includes('--all');
  const dryRun = args.includes('--dry-run');
  const productArg = args.find(a => a.startsWith('--product='))?.split('=')[1]
    || (args.indexOf('--product') >= 0 ? args[args.indexOf('--product') + 1] : null);

  if (!allFlag && !productArg) {
    console.log('Usage: node sync-to-apps.js --all [--dry-run]');
    console.log('       node sync-to-apps.js --product friendly [--dry-run]');
    process.exit(0);
  }

  if (dryRun) {
    console.log('DRY RUN — no files will be written\n');
  }

  const products = allFlag ? Object.keys(SYNC_MAP) : [productArg];
  let totalFiles = 0;

  for (const product of products) {
    const entries = SYNC_MAP[product];
    if (!entries) {
      process.stderr.write(`Unknown product: ${product}\n`);
      continue;
    }

    console.log(`\n▶ Syncing ${product}...`);

    for (const entry of entries) {
      const srcAbs  = path.join(DIST, entry.from);
      const destAbs = path.join(TANVRIT_ROOT, entry.to);

      if (entry.isFile) {
        totalFiles += copyFile(srcAbs, destAbs, dryRun);
      } else {
        totalFiles += copyDir(srcAbs, destAbs, dryRun);
      }
    }
  }

  console.log(`\n✓ Synced ${totalFiles} file(s) to Tanvrit monorepo\n`);
  if (dryRun) {
    console.log('(dry run — run without --dry-run to apply)\n');
  }
}

main().catch(err => {
  console.error('Sync failed:', err);
  process.exit(1);
});
