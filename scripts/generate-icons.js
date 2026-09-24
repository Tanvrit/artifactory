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
 *   inkscape on PATH (Wedding icon — SVG filter rendering; brew install --cask
 *   inkscape, or the pinned AppImage that .github/workflows/export-icons.yml uses)
 *   .icns is written in-process (writeIcns below) — no iconutil, any OS.
 *
 * Pipeline:
 *   SVG source → 1024px PNG (sharp/inkscape) → platform-specific exports
 */

'use strict';

const sharp = require('sharp');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
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
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
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
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'control',
    src: 'tanvrit/tanvrit-mark.svg',  // internal tool — uses Tanvrit corporate mark
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'compute',
    src: 'tanvrit/tanvrit-mark.svg',  // internal tool — uses Tanvrit corporate mark
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'ai',
    src: 'tanvrit/tanvrit-mark.svg',  // placeholder — designed branding pending
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'auditor',
    src: 'tanvrit/tanvrit-mark.svg',  // placeholder — designed branding pending
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'automator',
    src: 'tanvrit/tanvrit-mark.svg',  // placeholder — existing composeApp/icons/automator.icns kept as override
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'market',
    src: 'tanvrit/tanvrit-mark.svg',  // placeholder — designed branding pending
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
  },
  {
    id: 'admin',
    src: 'tanvrit/tanvrit-mark.svg',  // internal tool — uses Tanvrit corporate mark
    renderer: 'sharp',
    platforms: ['android', 'ios', 'macos', 'windows', 'linux', 'web'],
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
      // sharp cannot render these SVG filters, so the fallback ships a wrong
      // icon. Acceptable for a local preview; in CI it must fail the run.
      if (process.env.GITHUB_ACTIONS === 'true') {
        throw new Error(`Inkscape is required in CI to render ${path.basename(svgPath)}: ${result.error.message}`);
      }
      logErr(`Inkscape not available, falling back to sharp for ${path.basename(svgPath)}`);
      await sharp(svgPath).resize(size, size).png({ quality: 100 }).toFile(outPath);
    } else if (result.status !== 0 || !fs.existsSync(outPath)) {
      // Inkscape ran but produced nothing (e.g. a missing shared library).
      // Without this check the missing PNG only surfaced later, or not at all.
      throw new Error(
        `Inkscape failed (exit ${result.status}) rendering ${path.basename(svgPath)} at ${size}px: ` +
        `${String(result.stderr || '').trim().slice(-2000)}`,
      );
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

  // Pack the iconset into .icns in-process, on every OS. This used to shell out
  // to `iconutil` on macOS and `require('makeicns')` elsewhere, but makeicns was
  // never a dependency: on Linux the require threw, the catch only logged, and
  // the run went green with no .icns at all. Any failure here now fails the run.
  await writeIcns(iconsetDir, path.join(macosDir, `${product.id}.icns`));
  logOk(`${product.id}.icns (${ICNS_ENTRIES.length} entries)`);
}

// ── ICNS ─────────────────────────────────────────────────────
// Entry set copied from what `iconutil -c icns` wrote into the committed
// dist/*/macos/*.icns (parsed 2026-09-24): PNG payloads for 32px and up, and
// ARGB (per-channel RLE) for the 16/32 px 1x slots, where iconutil also avoids
// PNG. Straight (not premultiplied) alpha, also matching iconutil.
const ICNS_ENTRIES = [
  // [OSType, pixel size, payload format]
  ['ic04', 16, 'argb'],   // 16x16
  ['ic05', 32, 'argb'],   // 32x32
  ['ic11', 32, 'png'],    // 16x16@2x
  ['ic12', 64, 'png'],    // 32x32@2x
  ['ic07', 128, 'png'],   // 128x128
  ['ic13', 256, 'png'],   // 128x128@2x
  ['ic08', 256, 'png'],   // 256x256
  ['ic14', 512, 'png'],   // 256x256@2x
  ['ic09', 512, 'png'],   // 512x512
  ['ic10', 1024, 'png'],  // 512x512@2x
];

// The icns RLE: a control byte < 0x80 is followed by (byte + 1) literal bytes;
// a control byte >= 0x80 repeats the next byte (byte - 0x80 + 3) times.
function icnsRle(bytes) {
  const out = [];
  let i = 0;
  while (i < bytes.length) {
    let run = 1;
    while (i + run < bytes.length && run < 130 && bytes[i + run] === bytes[i]) run++;
    if (run >= 3) {
      out.push(0x80 + run - 3, bytes[i]);
      i += run;
      continue;
    }
    const start = i;
    while (i < bytes.length && i - start < 128) {
      if (i + 2 < bytes.length && bytes[i] === bytes[i + 1] && bytes[i] === bytes[i + 2]) break;
      i++;
    }
    out.push(i - start - 1);
    for (let k = start; k < i; k++) out.push(bytes[k]);
  }
  return Buffer.from(out);
}

async function icnsPayload(pngPath, size, format) {
  const meta = await sharp(pngPath).metadata();
  if (meta.width !== size || meta.height !== size) {
    throw new Error(`${pngPath} is ${meta.width}x${meta.height}, expected ${size}x${size} for .icns`);
  }
  if (format === 'png') return fs.readFileSync(pngPath);

  const { data } = await sharp(pngPath).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const pixels = size * size;
  // Channel order in the payload is A, R, G, B; raw RGBA offsets are 3, 0, 1, 2.
  const planes = [3, 0, 1, 2].map((offset) => {
    const plane = Buffer.alloc(pixels);
    for (let p = 0; p < pixels; p++) plane[p] = data[p * 4 + offset];
    return icnsRle(plane);
  });
  return Buffer.concat([Buffer.from('ARGB', 'latin1'), ...planes]);
}

async function writeIcns(iconsetDir, outPath) {
  const chunks = [];
  for (const [type, size, format] of ICNS_ENTRIES) {
    const payload = await icnsPayload(path.join(iconsetDir, `icon_${size}x${size}.png`), size, format);
    const header = Buffer.alloc(8);
    header.write(type, 0, 'latin1');
    header.writeUInt32BE(payload.length + 8, 4);
    chunks.push(header, payload);
  }
  const body = Buffer.concat(chunks);
  const fileHeader = Buffer.alloc(8);
  fileHeader.write('icns', 0, 'latin1');
  fileHeader.writeUInt32BE(body.length + 8, 4);
  fs.writeFileSync(outPath, Buffer.concat([fileHeader, body]));
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
