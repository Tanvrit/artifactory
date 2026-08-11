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
  'bharat-bandhu', 'school', 'wedding', 'control', 'compute'
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
  // --- dl.tanvrit.com — flat R2 object host.
  //
  // This hostname is bound to this Worker as a Production domain. It exists for
  // one job: serve large binaries out of `tanvrit-artifacts` over the normal
  // Cloudflare CDN.
  //
  // It replaces the public `pub-*.r2.dev` endpoint, which Cloudflare rate-limits
  // and documents as development-only. Serving 260-280 MB installers through it
  // degraded badly and erratically under load — measured on one file within a
  // single minute: 13.9 MB/s, then 1.2 MB/s, then 125 KB/s. At 125 KB/s a
  // 260 MB download takes ~35 minutes, which reads to a user as "the download
  // link is broken". Requests here are cached at the edge instead, and the
  // bucket needs no public dev URL at all.
  //
  // The ENTIRE path is the R2 key, so both `ai/manual/...` (the desktop
  // installers) and `dl/...` resolve without needing a route per prefix.
  // Scoped by hostname so artifacts.tanvrit.com keeps its existing API surface
  // exactly as-is.
  if (new URL(request.url).hostname === 'dl.tanvrit.com') {
    const key = path.replace(/^\/+/, '');
    if (!key) return jsonError(400, 'Missing object key');
    // Reject traversal and absolute-ish keys before they reach the bucket.
    if (key.includes('..') || key.includes('//')) return jsonError(400, 'Invalid object key');
    return serveBinaryFromR2(env.ARTIFACTS, key, request);
  }

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

  // --- /dl/{...} — release binaries streamed directly from R2 (Phase 2).
  // Desktop installers (dmg/msi/deb) are uploaded under the `dl/` prefix and
  // served here without a manifest round-trip; serveFromR2 streams object.body
  // and picks the right Content-Type from the extension. This is what the
  // versioned download links on the landing site point at.
  if (segments[0] === 'dl') {
    const key = segments.slice(1).join('/');
    if (!key) return jsonError(400, 'Missing download path');
    return serveFromR2(env.ARTIFACTS, `dl/${key}`, CACHE_BINARY);
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

  // Prefer the R2 mirror when the manifest exposes one (set since the
  // release-desktop-template's "Mirror binaries to Cloudflare R2" step
  // started running). Falls back to the GitHub Releases CDN otherwise.
  const r2Url     = platformData.r2_url;
  const directUrl = r2Url || platformData.direct_url || platformData.url;
  // R2 URLs (artifacts.tanvrit.com/releases/...) are served directly by this
  // same worker through the public R2 bucket — no CDN redirect chain to
  // resolve, so we can return a plain 302 instead of proxyBinary's GitHub
  // resolve dance.
  if (r2Url) {
    return Response.redirect(r2Url, 302);
  }
  return proxyBinary(directUrl, platformInfo);
}

// Resolve the binary download URL and redirect the client to the CDN-signed URL.
//
// GitHub releases: github.com/releases/... → 302 → release-assets.githubusercontent.com/...?jwt=...
// Cloudflare Worker IPs are blocked by GitHub's release CDN for proxying, so instead:
//   1. Worker resolves github.com redirect → gets the signed CDN URL
//   2. Worker redirects client to that signed CDN URL (time-limited, opaque blob ID)
//
// When R2 is enabled (Phase 2), binaries will be stored in R2 and served directly.
async function proxyBinary(url, platformInfo) {
  const filename = url.split('/').pop() || `download.${platformInfo.ext}`;

  // Resolve github.com release URL → CDN signed URL
  const resolveResp = await fetch(url, {
    method: 'GET',
    headers: { 'User-Agent': 'Tanvrit-Artifacts-Proxy/1.0' },
    redirect: 'manual',
  });

  let downloadUrl = url;
  if (resolveResp.status === 301 || resolveResp.status === 302 ||
      resolveResp.status === 307 || resolveResp.status === 308) {
    const location = resolveResp.headers.get('location');
    if (location) downloadUrl = location;
  } else if (resolveResp.ok) {
    // Direct URL (no redirect needed) — stream it
    const headers = new Headers({
      ...CORS_HEADERS,
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Cache-Control': CACHE_BINARY,
      'X-Content-Type-Options': 'nosniff',
    });
    const cl = resolveResp.headers.get('Content-Length');
    if (cl) headers.set('Content-Length', cl);
    return new Response(resolveResp.body, { status: 200, headers });
  } else {
    return jsonError(502, `Binary temporarily unavailable (upstream ${resolveResp.status})`);
  }

  // Redirect client to the CDN-signed URL (opaque blob URL, not github.com)
  return new Response(null, {
    status: 302,
    headers: {
      ...CORS_HEADERS,
      'Location': downloadUrl,
      'Cache-Control': 'no-store',
    },
  });
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
      webp: 'image/webp', zip: 'application/zip', json: 'application/json',
      icns: 'image/x-icns', dmg: 'application/x-apple-diskimage',
      msi: 'application/x-msi', deb: 'application/vnd.debian.binary-package',
      rpm: 'application/x-rpm', appimage: 'application/x-executable',
    };
    headers.set('Content-Type', mimeMap[ext] || 'application/octet-stream');
  }

  return new Response(body, { status: 200, headers });
}

/**
 * Stream a binary object from R2 with Range support.
 *
 * Kept separate from serveFromR2 rather than folded into it: serveFromR2 backs
 * the manifest/branding routes and has a GitHub-raw fallback, and those paths
 * should not change behaviour just because installers needed ranges.
 *
 * Range matters here specifically. These objects are 260-280 MB, and without
 * a 206 the browser cannot resume a dropped download — it restarts from zero,
 * which on a flaky connection means it may never finish at all.
 */
async function serveBinaryFromR2(bucket, key, request) {
  if (!bucket) return jsonError(500, 'R2 binding is not configured on this Worker');

  const rangeHeader = request.headers.get('Range');
  const object = rangeHeader
    ? await bucket.get(key, { range: request.headers })
    : await bucket.get(key);

  if (!object) return jsonError(404, `Not found: ${key}`);

  const headers = new Headers({
    ...CORS_HEADERS,
    'Cache-Control': CACHE_BINARY,
    'Content-Type': mimeForKey(key),
    // Advertise range support so clients and download managers will resume.
    'Accept-Ranges': 'bytes',
  });
  if (object.httpEtag) headers.set('ETag', object.httpEtag);

  // HEAD must not carry a body, but should carry the same metadata — the
  // download proxy probes with HEAD before redirecting.
  if (request.method === 'HEAD') {
    headers.set('Content-Length', String(object.size));
    return new Response(null, { status: 200, headers });
  }

  // A ranged hit comes back with object.range; translate it into the 206 the
  // client asked for. Without Content-Range a 206 is malformed.
  if (object.range && rangeHeader) {
    const offset = object.range.offset ?? 0;
    const length = object.range.length ?? (object.size - offset);
    headers.set('Content-Range', `bytes ${offset}-${offset + length - 1}/${object.size}`);
    headers.set('Content-Length', String(length));
    return new Response(object.body, { status: 206, headers });
  }

  headers.set('Content-Length', String(object.size));
  return new Response(object.body, { status: 200, headers });
}

/** Content-Type from a key's extension; shared shape with serveFromR2's map. */
function mimeForKey(key) {
  const ext = key.split('.').pop()?.toLowerCase();
  const mimeMap = {
    svg: 'image/svg+xml', png: 'image/png', ico: 'image/x-icon',
    webp: 'image/webp', zip: 'application/zip', json: 'application/json',
    icns: 'image/x-icns', dmg: 'application/x-apple-diskimage',
    msi: 'application/x-msi', deb: 'application/vnd.debian.binary-package',
    rpm: 'application/x-rpm', appimage: 'application/x-executable',
  };
  return mimeMap[ext] || 'application/octet-stream';
}

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message, status }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}
