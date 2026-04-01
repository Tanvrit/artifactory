#!/usr/bin/env node
/**
 * Tanvrit Icon Generation Pipeline
 *
 * Usage:
 *   node generate-icons.js --all
 *   node generate-icons.js --product friendly
 *   node generate-icons.js --product friendly --platforms macos,windows
 *
 * Requirements:
 *   npm install sharp png-to-ico
 *   brew install inkscape  (for Wedding icon — SVG filter rendering)
 *   macOS only: iconutil (built-in, for .icns)
 *
 * Pipeline:
 *   SVG source → 1024px PNG (sharp/inkscape) → platform-specific exports
 */

'use strict';

const sharp = require('sharp');
const fs = require('fs');
const path = require('path');
const { execSync, spawnSync } = require('child_process');
const pngToIco = require('png-to-ico');

const ROOT = path.join(__dirname, '..');
const ICONS_SRC = path.join(ROOT, 'branding/icons/src');
const ICONS_DIST = path.join(ROOT, 'branding/icons/dist');

// ── Product Configuration ────────────────────────────────────
const PRODUCTS = [
  {
    id: 'tanvrit',
    src: 'tanvrit/tanvrit-mark.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web', 'social'],
  },
  {
    id: 'friendly',
    src: 'friendly/friendly-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'desipops',
    src: 'desipops/desipops-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'mandee',
    src: 'mandee/mandee-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'swyft',
    src: 'swyft/swyft-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'bharat-bandhu',
    src: 'bharat-bandhu/bharat-bandhu-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'web'],
  },
  {
    id: 'school',
    src: 'school/school-icon.svg',
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'wedding',
    src: 'wedding/wedding-icon.svg',
    renderer: 'inkscape',  // SVG filters require Inkscape
    platforms: ['android', 'ios', 'web'],
  },
  {
    id: 'control',
    src: 'control/control-icon.svg',
    renderer: 'sharp',
    platforms: ['macos'],
  },
];

// ── Platform Export Specs ────────────────────────────────────
const ANDROID_SIZES = {
  'mipmap-mdpi':    48,
  'mipmap-hdpi':    72,
  'mipmap-xhdpi':   96,
  'mipmap-xxhdpi':  144,
  'mipmap-xxxhdpi': 192,
};

const ANDROID_ADAPTIVE_FG = {
  'mipmap-mdpi':    108,
  'mipmap-hdpi':    162,
  'mipmap-xhdpi':   216,
  'mipmap-xxhdpi':  324,
  'mipmap-xxxhdpi': 432,
};

const IOS_SIZES = [20, 29, 40, 58, 60, 76, 80, 87, 120, 152, 167, 180, 1024];

const MACOS_ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024];

const LINUX_SIZES = [16, 32, 48, 64, 96, 128, 256, 512];

const WEB_SIZES = [16, 32, 48, 64, 96, 120, 128, 180, 192, 256, 512, 1024];

// ── Helpers ──────────────────────────────────────────────────

function mkdir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

function logOk(msg) {
  process.stdout.write(`  ✓ ${msg}\n`);
}

function logErr(msg) {
  process.stderr.write(`  ✗ ${msg}\n`);
}

async function renderSvgToPng(svgPath, outPath, size, renderer = 'sharp') {
  if (renderer === 'inkscape') {
    // Inkscape properly renders SVG filters (used for Wedding icon)
    const result = spawnSync('inkscape', [
      svgPath,
      `--export-filename=${outPath}`,
      `--export-width=${size}`,
      `--export-height=${size}`,
      '--export-background-opacity=0',
    ], { stdio: 'pipe' });
    if (result.error) {
      // Fallback to sharp if inkscape not installed
      logErr(`Inkscape not available, falling back to sharp for ${path.basename(svgPath)}`);
      await sharp(svgPath).resize(size, size).png({ quality: 100 }).toFile(outPath);
    }
  } else {
    await sharp(svgPath)
      .resize(size, size, { kernel: sharp.kernel.lanczos3 })
      .png({ quality: 100, compressionLevel: 9 })
      .toFile(outPath);
  }
}

async function pngToWebP(pngPath, webpPath, size) {
  await sharp(pngPath)
    .resize(size, size, { kernel: sharp.kernel.lanczos3 })
    .webp({ quality: 95, lossless: false })
    .toFile(webpPath);
}

// ── Platform Generators ──────────────────────────────────────

async function generateAndroid(product, srcSvg, outDir) {
  log(`  Android...`);
  const renderer = product.renderer;

  // Standard launcher icons (WebP)
  for (const [density, size] of Object.entries(ANDROID_SIZES)) {
    const densityDir = path.join(outDir, 'android', density);
    mkdir(densityDir);
    const tmpPng = path.join(densityDir, 'ic_launcher_tmp.png');
    const webpOut = path.join(densityDir, 'ic_launcher.webp');
    const webpRound = path.join(densityDir, 'ic_launcher_round.webp');
    await renderSvgToPng(srcSvg, tmpPng, size, renderer);
    await pngToWebP(tmpPng, webpOut, size);
    await pngToWebP(tmpPng, webpRound, size);
    fs.unlinkSync(tmpPng);
    logOk(`${density} → ${size}px WebP`);
  }

  // Foreground for adaptive icon (anydpi-v26)
  const adaptiveDir = path.join(outDir, 'android', 'mipmap-anydpi-v26');
  mkdir(adaptiveDir);

  // Write adaptive icon XML referencing foreground
  const adaptiveXml = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
</adaptive-icon>`;
  fs.writeFileSync(path.join(adaptiveDir, 'ic_launcher.xml'), adaptiveXml);
  fs.writeFileSync(path.join(adaptiveDir, 'ic_launcher_round.xml'), adaptiveXml);
  logOk('anydpi-v26 adaptive icon XML');

  // Play Store icon (512px PNG)
  const playstoreDir = path.join(outDir, 'android');
  await renderSvgToPng(srcSvg, path.join(playstoreDir, 'playstore.png'), 512, renderer);
  logOk('playstore.png 512×512');
}

async function generateIos(product, srcSvg, outDir) {
  log(`  iOS...`);
  const iosDir = path.join(outDir, 'ios', 'AppIcon.appiconset');
  mkdir(iosDir);
  const renderer = product.renderer;

  const images = [];

  // Light mode icons
  for (const size of IOS_SIZES) {
    const filename = `${product.id}-icon-${size}.png`;
    await renderSvgToPng(srcSvg, path.join(iosDir, filename), size, renderer);
    if (size === 1024) {
      images.push({ filename, idiom: 'universal', platform: 'ios', size: '1024x1024' });
    }
    logOk(`iOS ${size}px`);
  }

  // Dark mode (1024px only — Xcode generates the rest)
  const darkFilename = `${product.id}-icon-1024-dark.png`;
  // For dark mode, we use a slightly different treatment — darker background
  // For now, output a copy (designer will customize)
  await renderSvgToPng(srcSvg, path.join(iosDir, darkFilename), 1024, renderer);
  images.push({
    appearances: [{ appearance: 'luminosity', value: 'dark' }],
    filename: darkFilename,
    idiom: 'universal',
    platform: 'ios',
    size: '1024x1024',
  });
  logOk('iOS 1024px dark variant');

  // Tinted (1024px only)
  const tintedFilename = `${product.id}-icon-1024-tinted.png`;
  await sharp(path.join(iosDir, `${product.id}-icon-1024.png`))
    .grayscale()
    .toFile(path.join(iosDir, tintedFilename));
  images.push({
    appearances: [{ appearance: 'luminosity', value: 'tinted' }],
    filename: tintedFilename,
    idiom: 'universal',
    platform: 'ios',
    size: '1024x1024',
  });
  logOk('iOS 1024px tinted variant');

  // Write Contents.json
  const contents = { images, info: { author: 'xcode', version: 1 } };
  fs.writeFileSync(path.join(iosDir, 'Contents.json'), JSON.stringify(contents, null, 2));
  logOk('Contents.json');
}

async function generateMacos(product, srcSvg, outDir) {
  log(`  macOS...`);
  const macosDir = path.join(outDir, 'macos');
  const iconsetDir = path.join(macosDir, `${product.id}.iconset`);
  mkdir(iconsetDir);
  const renderer = product.renderer;

  // Generate iconset sizes
  for (const size of MACOS_ICONSET_SIZES) {
    await renderSvgToPng(srcSvg, path.join(iconsetDir, `icon_${size}x${size}.png`), size, renderer);
    // @2x = size × 2 (but named at 1x)
    const half = Math.round(size / 2);
    if (half >= 16) {
      await renderSvgToPng(srcSvg, path.join(iconsetDir, `icon_${half}x${half}@2x.png`), size, renderer);
    }
    logOk(`macOS ${size}px`);
  }

  // Convert iconset to .icns using iconutil (macOS only)
  if (process.platform === 'darwin') {
    try {
      execSync(`iconutil -c icns "${iconsetDir}" -o "${path.join(macosDir, `${product.id}.icns`)}"`, { stdio: 'pipe' });
      logOk(`${product.id}.icns generated`);
    } catch (e) {
      logErr(`iconutil failed: ${e.message} — iconset is still available at ${iconsetDir}`);
    }
  } else {
    // On Linux CI: use makeicns (npm package) if available
    try {
      const makeicns = require('makeicns');
      await makeicns({
        in: iconsetDir,
        out: path.join(macosDir, `${product.id}.icns`),
      });
      logOk(`${product.id}.icns generated via makeicns`);
    } catch (e) {
      logErr(`makeicns not available. Install: npm install makeicns. Iconset is available.`);
    }
  }
}

async function generateWindows(product, srcSvg, outDir) {
  log(`  Windows...`);
  const winDir = path.join(outDir, 'windows');
  mkdir(winDir);
  const renderer = product.renderer;

  const icoPngPaths = [];
  for (const size of [16, 32, 48, 256]) {
    const tmpPath = path.join(winDir, `icon_${size}.png`);
    await renderSvgToPng(srcSvg, tmpPath, size, renderer);
    icoPngPaths.push(tmpPath);
    logOk(`Windows ${size}px`);
  }

  // Convert PNGs to .ico
  const icoBuffer = await pngToIco(icoPngPaths);
  fs.writeFileSync(path.join(winDir, `${product.id}.ico`), icoBuffer);
  logOk(`${product.id}.ico (16,32,48,256)`);

  // Clean up temp PNGs
  icoPngPaths.forEach(p => fs.unlinkSync(p));
}

async function generateLinux(product, srcSvg, outDir) {
  log(`  Linux...`);
  const linuxDir = path.join(outDir, 'linux');
  mkdir(linuxDir);
  const renderer = product.renderer;

  for (const size of LINUX_SIZES) {
    await renderSvgToPng(srcSvg, path.join(linuxDir, `${size}.png`), size, renderer);
    logOk(`Linux ${size}px`);
  }

  // Symlink 256.png as the primary icon (used by Compose Desktop packageDeb)
  const primaryLink = path.join(linuxDir, `${product.id}-256.png`);
  if (!fs.existsSync(primaryLink)) {
    fs.copyFileSync(path.join(linuxDir, '256.png'), primaryLink);
  }
  logOk(`${product.id}-256.png (primary)`);
}

async function generateWeb(product, srcSvg, outDir) {
  log(`  Web...`);
  const webDir = path.join(outDir, 'web');
  mkdir(webDir);
  const renderer = product.renderer;

  // Standard PNG sizes
  for (const size of WEB_SIZES) {
    await renderSvgToPng(srcSvg, path.join(webDir, `icon-${size}.png`), size, renderer);
    logOk(`Web ${size}px`);
  }

  // Favicon ICO (16+32+48 embedded)
  const faviconPngs = [16, 32, 48].map(s => path.join(webDir, `icon-${s}.png`));
  const icoBuffer = await pngToIco(faviconPngs);
  fs.writeFileSync(path.join(webDir, 'favicon.ico'), icoBuffer);
  logOk('favicon.ico (16+32+48)');

  // Copy SVG source directly
  fs.copyFileSync(srcSvg, path.join(webDir, 'icon.svg'));
  logOk('icon.svg (scalable)');

  // Named aliases for common use cases
  fs.copyFileSync(path.join(webDir, 'icon-180.png'), path.join(webDir, 'apple-touch-icon.png'));
  fs.copyFileSync(path.join(webDir, 'icon-192.png'), path.join(webDir, 'icon-192.png'));
  fs.copyFileSync(path.join(webDir, 'icon-512.png'), path.join(webDir, 'icon-512-maskable.png'));
  logOk('apple-touch-icon.png, icon-192.png, icon-512-maskable.png');
}

async function generateSocial(product, srcSvg, outDir) {
  log(`  Social...`);
  const socialDir = path.join(outDir, 'social');
  mkdir(socialDir);
  const renderer = product.renderer;

  // GitHub social preview 1280×640 (wide — use icon centered on brand-color bg)
  await renderSvgToPng(srcSvg, path.join(socialDir, 'github-social-preview.png'), 640, renderer);
  logOk('github-social-preview.png (640px icon — needs banner treatment in design)');

  // OG image 1200×630 (icon at 400px centered on brand background)
  await renderSvgToPng(srcSvg, path.join(socialDir, 'og-image-icon.png'), 400, renderer);
  logOk('og-image-icon.png (400px — compose into full OG in design tool)');

  // GitHub org avatar 500×500
  await renderSvgToPng(srcSvg, path.join(socialDir, 'github-avatar.png'), 500, renderer);
  logOk('github-avatar.png (500px)');
}

// ── Main ─────────────────────────────────────────────────────

async function processProduct(product) {
  const srcSvg = path.join(ICONS_SRC, product.src);

  if (!fs.existsSync(srcSvg)) {
    logErr(`Source SVG not found: ${srcSvg} — skipping ${product.id}`);
    return;
  }

  const outDir = path.join(ICONS_DIST, product.id);
  mkdir(outDir);

  log(`\n▶ ${product.id} (${product.renderer})`);
  log(`  Source: ${srcSvg}`);

  for (const platform of product.platforms) {
    switch (platform) {
      case 'android': await generateAndroid(product, srcSvg, outDir); break;
      case 'ios':     await generateIos(product, srcSvg, outDir);     break;
      case 'macos':   await generateMacos(product, srcSvg, outDir);   break;
      case 'windows': await generateWindows(product, srcSvg, outDir); break;
      case 'linux':   await generateLinux(product, srcSvg, outDir);   break;
      case 'web':     await generateWeb(product, srcSvg, outDir);     break;
      case 'social':  await generateSocial(product, srcSvg, outDir);  break;
      default:        logErr(`Unknown platform: ${platform}`);
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  const allFlag = args.includes('--all');
  const productFlag = args.find(a => a.startsWith('--product='))?.split('=')[1]
    || (args.indexOf('--product') >= 0 ? args[args.indexOf('--product') + 1] : null);

  let targets = PRODUCTS;

  if (productFlag && !allFlag) {
    targets = PRODUCTS.filter(p => p.id === productFlag);
    if (targets.length === 0) {
      console.error(`Unknown product: ${productFlag}`);
      console.error(`Valid: ${PRODUCTS.map(p => p.id).join(', ')}`);
      process.exit(1);
    }
  }

  if (!allFlag && !productFlag) {
    console.log('Usage:');
    console.log('  node generate-icons.js --all');
    console.log('  node generate-icons.js --product friendly');
    process.exit(0);
  }

  log(`\nTanvrit Icon Generation Pipeline`);
  log(`Products: ${targets.map(p => p.id).join(', ')}`);
  log(`Output: ${ICONS_DIST}`);

  for (const product of targets) {
    await processProduct(product);
  }

  log('\n✓ All icons generated successfully\n');
}

main().catch(err => {
  console.error('Generation failed:', err);
  process.exit(1);
});
