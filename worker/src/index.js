/**
 * Tanvrit Artifacts CDN Worker
 * artifacts.tanvrit.com
 *
 * Routes:
 *   GET /catalog.json                           → master catalog (R2 or GitHub raw fallback)
 *   GET /{product}/latest.json                  → latest manifest (R2 or GitHub raw fallback)
 *   GET /{product}/{version}/latest.json        → versioned manifest (R2 or GitHub raw fallback)
 *   GET /{product}/latest/{platform}            → redirect to latest binary
 *   GET /{product}/{version}/{platform}         → redirect to versioned binary
 *   GET /brand/{product}/{asset}                → branding assets (R2 or GitHub raw)
 *   GET /brand/press-kit.zip                    → press kit (R2 or GitHub raw)
 *
 * Storage:
 *   Phase 1 (now): Manifests served directly from GitHub raw content (no R2 needed)
 *   Phase 2 (after R2 enabled): Uncomment [[r2_buckets]] in wrangler.toml + re-deploy
 *   GitHub Releases stores the actual binaries (free bandwidth always)
 */

// GitHub raw content base for manifest fallback (when R2 not yet enabled)
const GITHUB_RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const CACHE_MANIFEST = 'public, max-age=60, s-maxage=60';         // 1 min — manifests change on release
const CACHE_BINARY   = 'public, max-age=3600, s-maxage=3600';     // 1 hr — redirect URLs are stable
const CACHE_BRANDING = 'public, max-age=31536000, immutable';     // 1 yr — versioned branding assets
const CACHE_CATALOG  = 'public, max-age=30, s-maxage=30';        // 30 sec — catalog is most volatile

const VALID_PRODUCTS = [
  'friendly', 'desipops', 'mandee', 'swyft',
  'bharat-bandhu', 'school', 'wedding', 'control'
];

const PLATFORM_FILE_MAP = {
  'macos-arm64':     { ext: 'dmg', label: 'macOS Apple Silicon' },
  'macos-x64':       { ext: 'dmg', label: 'macOS Intel' },
  'macos-universal': { ext: 'dmg', label: 'macOS Universal' },
  'windows-x64':     { ext: 'msi', label: 'Windows' },
  'linux-x64':       { ext: 'deb', label: 'Linux (DEB)' },
  'linux-x64-rpm':   { ext: 'rpm', label: 'Linux (RPM)' },
  'linux-x64-appimage': { ext: 'AppImage', label: 'Linux (AppImage)' },
};

const PORTAL_ORIGIN = 'https://tanvrit-artifacts-portal.pages.dev';

const TANVRIT_ICONS = ['tanvrit-mark', 'tanvrit-wordmark', 'tanvrit-wordmark-light'];

function resolveBrandKey(assetPath) {
  const parts = assetPath.split('/');
  if (parts[0] === 'icons' && parts.length === 2) {
    const filename = parts[1];
    if (filename.endsWith('.svg')) {
      const stem = filename.slice(0, -4);
      if (TANVRIT_ICONS.includes(stem)) return `branding/icons/src/tanvrit/${stem}.svg`;
      if (VALID_PRODUCTS.includes(stem)) return `branding/icons/src/${stem}/${stem}-icon.svg`;
    }
  }
  return `branding/${assetPath}`;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return new Response('Method Not Allowed', { status: 405 });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      return await routeRequest(path, request, env, ctx);
    } catch (err) {
      console.error('Worker error:', err);
      return jsonError(500, 'Internal server error');
    }
  }
};

async function routeRequest(path, request, env, ctx) {
  // Strip leading slash and split segments
  const segments = path.replace(/^\//, '').split('/');

  // --- / (root) or portal paths — proxy to Pages portal
  if (path === '/' || path === '') {
    return proxyToPortal(request);
  }

  // --- /catalog.json
  if (path === '/catalog.json') {
    return serveFromR2(env.ARTIFACTS, 'manifests/catalog.json', CACHE_CATALOG, 'application/json');
  }

  // --- /brand/{...} — branding assets with icon shorthand resolution
  if (segments[0] === 'brand') {
    const assetPath = segments.slice(1).join('/');
    if (!assetPath) return jsonError(400, 'Missing brand asset path');
    const r2Key = resolveBrandKey(assetPath);
    return serveFromR2(env.ARTIFACTS, r2Key, CACHE_BRANDING);
  }

  // Product routes: segments[0] = product
  const product = segments[0];
  if (!VALID_PRODUCTS.includes(product)) {
    // Not an API route — proxy to portal (handles /_next/, /icons/, etc.)
    return proxyToPortal(request);
  }

  // --- /{product}/latest.json
  if (segments.length === 2 && segments[1] === 'latest.json') {
    return serveFromR2(env.ARTIFACTS, `manifests/${product}/latest.json`, CACHE_MANIFEST, 'application/json');
  }

  // --- /{product}/{version}/latest.json
  if (segments.length === 3 && segments[2] === 'latest.json') {
    const version = segments[1];
    return serveFromR2(env.ARTIFACTS, `manifests/${product}/${version}.json`, CACHE_MANIFEST, 'application/json');
  }

  // --- /{product}/latest/{platform} — redirect to latest binary
  if (segments.length === 3 && segments[1] === 'latest') {
    const platform = segments[2];
    return await resolveAndRedirect(env.ARTIFACTS, product, 'latest', platform, ctx);
  }

  // --- /{product}/{version}/{platform} — redirect to versioned binary
  if (segments.length === 3) {
    const version  = segments[1];
    const platform = segments[2];
    return await resolveAndRedirect(env.ARTIFACTS, product, version, platform, ctx);
  }

  // --- /{product} — return product info page (JSON)
  if (segments.length === 1) {
    return serveFromR2(env.ARTIFACTS, `manifests/${product}/latest.json`, CACHE_MANIFEST, 'application/json');
  }

  return jsonError(404, 'Not found');
}

function proxyToPortal(request) {
  const url = new URL(request.url);
  url.hostname = 'tanvrit-artifacts-portal.pages.dev';
  url.port = '';
  return fetch(new Request(url.toString(), request));
}

async function resolveAndRedirect(bucket, product, versionOrLatest, platform, ctx) {
  const platformInfo = PLATFORM_FILE_MAP[platform];
  if (!platformInfo) {
    return jsonError(400, `Unknown platform: ${platform}. Valid: ${Object.keys(PLATFORM_FILE_MAP).join(', ')}`);
  }

  // Load manifest — from R2 if available, otherwise GitHub raw
  const manifestKey = versionOrLatest === 'latest'
    ? `manifests/${product}/latest.json`
    : `manifests/${product}/${versionOrLatest}.json`;

  let manifest;
  if (bucket) {
    const obj = await bucket.get(manifestKey);
    if (!obj) return jsonError(404, `Manifest not found for ${product} ${versionOrLatest}`);
    manifest = await obj.json();
  } else {
    const res = await fetch(`${GITHUB_RAW}/${manifestKey}`);
    if (!res.ok) return jsonError(404, `Manifest not found for ${product} ${versionOrLatest}`);
    manifest = await res.json();
  }

  const platformData = manifest?.platforms?.[platform];
  if (!platformData) {
    return jsonError(404, `Platform ${platform} not available for ${product} ${versionOrLatest}`);
  }
  if (!platformData.available) {
    return jsonError(503, `Build for ${product} ${platform} is not yet available`);
  }

  const redirectUrl = platformData.direct_url || platformData.url;
  return Response.redirect(redirectUrl, 302);
}

async function serveFromR2(bucket, key, cacheControl, contentType) {
  let body, etag;

  if (bucket) {
    // R2 path (Phase 2 — after R2 enabled)
    const object = await bucket.get(key);
    if (!object) return jsonError(404, `Not found: ${key}`);
    body  = object.body;
    etag  = object.httpEtag;
  } else {
    // GitHub raw fallback (Phase 1 — no R2)
    const res = await fetch(`${GITHUB_RAW}/${key}`);
    if (!res.ok) return jsonError(404, `Not found: ${key}`);
    body  = res.body;
    etag  = res.headers.get('etag') || '';
  }

  const headers = new Headers({
    ...CORS_HEADERS,
    'Cache-Control': cacheControl,
  });
  if (etag) headers.set('ETag', etag);

  if (contentType) {
    headers.set('Content-Type', contentType);
  } else {
    const ext = key.split('.').pop()?.toLowerCase();
    const mimeMap = {
      svg: 'image/svg+xml', png: 'image/png', ico: 'image/x-icon',
      zip: 'application/zip', json: 'application/json', icns: 'image/x-icns',
      dmg: 'application/x-apple-diskimage', msi: 'application/x-msi',
      deb: 'application/vnd.debian.binary-package',
    };
    headers.set('Content-Type', mimeMap[ext] || 'application/octet-stream');
  }

  return new Response(body, { status: 200, headers });
}

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message, status }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}
