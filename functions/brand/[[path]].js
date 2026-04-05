/**
 * GET /brand/{...path}
 * Serves branding assets from R2 (if configured) or GitHub raw.
 *
 * Special handling for icon shorthand:
 *   /brand/icons/{product}.svg  →  tries branding/icons/src/{product}/{product}-icon.svg
 *   if not found in R2, falls back to GitHub raw at the same path.
 *
 * Also serves Tanvrit mark/wordmark from branding/icons/src/tanvrit/:
 *   /brand/icons/tanvrit-mark.svg      →  branding/icons/src/tanvrit/tanvrit-mark.svg
 *   /brand/icons/tanvrit-wordmark.svg  →  branding/icons/src/tanvrit/tanvrit-wordmark.svg
 */

import {
  GITHUB_RAW,
  CORS_HEADERS,
  VALID_PRODUCTS,
  MIME_MAP,
  jsonError,
} from '../_shared/constants.js';

const TANVRIT_ICON_FILES = [
  'tanvrit-mark.svg',
  'tanvrit-wordmark.svg',
  'tanvrit-wordmark-light.svg',
];

export async function onRequest(context) {
  const { env, params, request } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  // params.path is an array of path segments for [[path]]
  const segments = Array.isArray(params.path) ? params.path : (params.path ? [params.path] : []);
  const assetPath = segments.join('/');

  if (!assetPath) {
    return jsonError(400, 'Missing asset path. Usage: /brand/{path}');
  }

  // Resolve the R2/GitHub key
  const r2Key = resolveKey(assetPath);

  // Determine content type from extension
  const ext = assetPath.split('.').pop()?.toLowerCase() ?? '';
  const contentType = MIME_MAP[ext] ?? 'application/octet-stream';

  let body, etag;

  if (env.ARTIFACTS) {
    const obj = await env.ARTIFACTS.get(r2Key);
    if (!obj) {
      // Try GitHub raw as fallback
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

/**
 * Resolve a request asset path (relative to /brand/) to a storage key.
 *
 * Icon shorthand rules:
 *   icons/{product}.svg     → branding/icons/src/{product}/{product}-icon.svg
 *   icons/tanvrit-mark.svg  → branding/icons/src/tanvrit/tanvrit-mark.svg
 *   icons/{anything}.svg    → branding/icons/{anything}.svg  (passthrough)
 *   (everything else)       → branding/{assetPath}
 */
function resolveKey(assetPath) {
  const parts = assetPath.split('/');

  if (parts[0] === 'icons' && parts.length === 2) {
    const filename = parts[1]; // e.g. "friendly.svg" or "tanvrit-mark.svg"

    // Tanvrit brand files
    if (TANVRIT_ICON_FILES.includes(filename)) {
      const stem = filename.replace(/\.svg$/, '');
      return `branding/icons/src/tanvrit/${stem}.svg`;
    }

    // Product icon shorthand: {product}.svg → src/{product}/{product}-icon.svg
    const stem = filename.replace(/\.svg$/, '');
    if (VALID_PRODUCTS.includes(stem)) {
      return `branding/icons/src/${stem}/${stem}-icon.svg`;
    }
  }

  // Default passthrough: branding/{assetPath}
  return `branding/${assetPath}`;
}
