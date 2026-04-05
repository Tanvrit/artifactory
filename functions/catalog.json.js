/**
 * GET /catalog.json
 * Serves the master product catalog from R2 (if configured) or GitHub raw.
 */

const GITHUB_RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export async function onRequest(context) {
  const { env, request } = context;

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  let body, etag;

  if (env.ARTIFACTS) {
    const obj = await env.ARTIFACTS.get('manifests/catalog.json');
    if (!obj) return jsonError(404, 'catalog.json not found');
    body = await obj.text();
    etag = obj.httpEtag;
  } else {
    const res = await fetch(`${GITHUB_RAW}/manifests/catalog.json`);
    if (!res.ok) return jsonError(404, 'catalog.json not found');
    body = await res.text();
    etag = res.headers.get('etag') || '';
  }

  const headers = new Headers({
    ...CORS_HEADERS,
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=30, s-maxage=30',
  });
  if (etag) headers.set('ETag', etag);

  return new Response(body, { status: 200, headers });
}

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message, status }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}
