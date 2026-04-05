/**
 * Root middleware — intercepts /brand/* before any [product] dynamic route can match.
 * All other requests pass through to their matched function.
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

const TANVRIT_ICONS = ['tanvrit-mark', 'tanvrit-wordmark', 'tanvrit-wordmark-light'];

const MIME_MAP = {
  svg: 'image/svg+xml', png: 'image/png', ico: 'image/x-icon',
  icns: 'image/x-icns', webp: 'image/webp', zip: 'application/zip',
  json: 'application/json', dmg: 'application/x-apple-diskimage',
  msi: 'application/x-msi', deb: 'application/vnd.debian.binary-package',
  rpm: 'application/x-rpm',
};

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

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  // Intercept /brand/* before the [product] dynamic route can match
  if (url.pathname.startsWith('/brand/')) {
    const assetPath = url.pathname.slice('/brand/'.length);
    if (!assetPath) {
      return new Response(JSON.stringify({ error: 'Missing asset path', status: 400 }), {
        status: 400,
        headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
      });
    }

    const r2Key = resolveBrandKey(assetPath);
    const ext = assetPath.split('.').pop()?.toLowerCase() ?? '';
    const contentType = MIME_MAP[ext] ?? 'application/octet-stream';

    let body, etag;
    if (env.ARTIFACTS) {
      const obj = await env.ARTIFACTS.get(r2Key);
      if (!obj) {
        const fallback = await fetch(`${GITHUB_RAW}/${r2Key}`);
        if (!fallback.ok) return new Response(JSON.stringify({ error: `Not found: ${assetPath}`, status: 404 }), { status: 404, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' } });
        body = fallback.body; etag = fallback.headers.get('etag') || '';
      } else { body = obj.body; etag = obj.httpEtag; }
    } else {
      const res = await fetch(`${GITHUB_RAW}/${r2Key}`);
      if (!res.ok) return new Response(JSON.stringify({ error: `Not found: ${assetPath}`, status: 404 }), { status: 404, headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' } });
      body = res.body; etag = res.headers.get('etag') || '';
    }

    const headers = new Headers({ ...CORS_HEADERS, 'Content-Type': contentType, 'Cache-Control': 'public, max-age=31536000, immutable' });
    if (etag) headers.set('ETag', etag);
    return new Response(body, { status: 200, headers });
  }

  // All other requests — pass to matched function
  return next();
}
