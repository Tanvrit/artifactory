/**
 * GET /{product}/{version}/latest.json
 * Serves a specific versioned manifest for a product.
 */

import {
  GITHUB_RAW,
  CORS_HEADERS,
  VALID_PRODUCTS,
  jsonError,
} from '../../_shared/constants.js';

export async function onRequest(context) {
  const { env, params, request } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const { product, version } = params;

  if (!VALID_PRODUCTS.includes(product)) {
    return jsonError(404, `Unknown product: ${product}. Valid: ${VALID_PRODUCTS.join(', ')}`);
  }

  // Sanitize version to prevent path traversal
  if (!/^[\w.\-]+$/.test(version)) {
    return jsonError(400, `Invalid version format: ${version}`);
  }

  const key = `manifests/${product}/${version}.json`;
  let body, etag;

  if (env.ARTIFACTS) {
    const obj = await env.ARTIFACTS.get(key);
    if (!obj) return jsonError(404, `Manifest not found: ${product}/${version}.json`);
    body = await obj.text();
    etag = obj.httpEtag;
  } else {
    const res = await fetch(`${GITHUB_RAW}/${key}`);
    if (!res.ok) return jsonError(404, `Manifest not found: ${product}/${version}.json`);
    body = await res.text();
    etag = res.headers.get('etag') || '';
  }

  const headers = new Headers({
    ...CORS_HEADERS,
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=60, s-maxage=60',
  });
  if (etag) headers.set('ETag', etag);

  return new Response(body, { status: 200, headers });
}
