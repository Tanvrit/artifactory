/**
 * GET /brand/{...path}
 * Serves branding assets from R2 (if configured) or GitHub raw.
 *
 * Icon shorthand:
 *   /brand/icons/{product}.svg  →  branding/icons/src/{product}/{product}-icon.svg
 *   /brand/icons/tanvrit-mark.svg  →  branding/icons/src/tanvrit/tanvrit-mark.svg
 */

const GITHUB_RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const VALID_PRODUCTS = [
  'friendly', 'desipops', 'mandee', 'swyft',
  'bharat-bandhu', 'school', 'wedding', 'control',
];

const TANVRIT_ICONS = [
  'tanvrit-mark', 'tanvrit-wordmark', 'tanvrit-wordmark-light',
];

const MIME_MAP = {
  svg:  'image/svg+xml',
  png:  'image/png',
  ico:  'image/x-icon',
  icns: 'image/x-icns',
  webp: 'image/webp',
  zip:  'application/zip',
  json: 'application/json',
  dmg:  'application/x-apple-diskimage',
  msi:  'application/x-msi',
  deb:  'application/vnd.debian.binary-package',
  rpm:  'application/x-rpm',
};

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message, status }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

function resolveKey(assetPath) {
  const parts = assetPath.split('/');

  // Shorthand: icons/{name}.svg
  if (parts[0] === 'icons' && parts.length === 2) {
    const filename = parts[1];
    if (!filename.endsWith('.svg')) return `branding/${assetPath}`;
    const stem = filename.slice(0, -4); // remove .svg

    // Tanvrit mark / wordmark
    if (TANVRIT_ICONS.includes(stem)) {
      return `branding/icons/src/tanvrit/${stem}.svg`;
    }
    // Product icons
    if (VALID_PRODUCTS.includes(stem)) {
      return `branding/icons/src/${stem}/${stem}-icon.svg`;
    }
  }

  return `branding/${assetPath}`;
}

export async function onRequest(context) {
  const { env, params, request } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const rawPath = params.path;
  const assetPath = Array.isArray(rawPath) ? rawPath.join('/') : (rawPath || '');

  if (!assetPath) {
    return jsonError(400, 'Missing asset path');
  }

  const r2Key = resolveKey(assetPath);
  const ext = assetPath.split('.').pop()?.toLowerCase() ?? '';
  const contentType = MIME_MAP[ext] ?? 'application/octet-stream';

  let body, etag;

  if (env.ARTIFACTS) {
    const obj = await env.ARTIFACTS.get(r2Key);
    if (!obj) {
      const fallback = await fetch(`${GITHUB_RAW}/${r2Key}`);
      if (!fallback.ok) return jsonError(404, `Asset not found: ${assetPath}`);
      body = fallback.body;
      etag = fallback.headers.get('etag') || '';
    } else {
      body = obj.body;
      etag = obj.httpEtag;
    }
  } else {
    const res = await fetch(`${GITHUB_RAW}/${r2Key}`);
    if (!res.ok) return jsonError(404, `Asset not found: ${assetPath}`);
    body = res.body;
    etag = res.headers.get('etag') || '';
  }

  const headers = new Headers({
    ...CORS_HEADERS,
    'Content-Type': contentType,
    'Cache-Control': 'public, max-age=31536000, immutable',
  });
  if (etag) headers.set('ETag', etag);

  return new Response(body, { status: 200, headers });
}
