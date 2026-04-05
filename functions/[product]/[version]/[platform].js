/**
 * GET /{product}/{version}/{platform}
 * Resolves a versioned manifest and redirects to the binary direct_url.
 * Returns 503 if the build is not yet available.
 */

import {
  CORS_HEADERS,
  VALID_PRODUCTS,
  PLATFORM_FILE_MAP,
  jsonError,
  fetchManifest,
} from '../../_shared/constants.js';

export async function onRequest(context) {
  const { env, params, request } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const { product, version, platform } = params;

  if (!VALID_PRODUCTS.includes(product)) {
    return jsonError(404, `Unknown product: ${product}. Valid: ${VALID_PRODUCTS.join(', ')}`);
  }

  if (!PLATFORM_FILE_MAP[platform]) {
    return jsonError(400, `Unknown platform: ${platform}. Valid: ${Object.keys(PLATFORM_FILE_MAP).join(', ')}`);
  }

  // Sanitize version to prevent path traversal
  if (!/^[\w.\-]+$/.test(version)) {
    return jsonError(400, `Invalid version format: ${version}`);
  }

  const manifest = await fetchManifest(env, product, version);
  if (!manifest) {
    return jsonError(404, `Manifest not found for ${product} ${version}`);
  }

  const platformData = manifest?.platforms?.[platform];
  if (!platformData) {
    return jsonError(404, `Platform ${platform} not found for ${product} ${version}`);
  }

  if (!platformData.available) {
    return jsonError(503, `Build for ${product} ${platform} ${version} is not yet available`);
  }

  const redirectUrl = platformData.direct_url || platformData.url;
  if (!redirectUrl) {
    return jsonError(404, `No download URL for ${product} ${platform} ${version}`);
  }

  return Response.redirect(redirectUrl, 302);
}
