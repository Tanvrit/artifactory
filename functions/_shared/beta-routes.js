// Shared by Worker and Pages. Opt-in Jashva beta routing; stable routes return null.
const RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS' };
const KEY = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-beta\.([1-9][0-9]{0,9})$/;
const EXT = { 'macos-arm64': 'dmg', 'windows-x64': 'msi', 'linux-x64': 'deb', 'linux-x64-app': 'AppImage' };
function error(status, message) {
  return new Response(JSON.stringify({ error: message, status }), { status,
    headers: { ...CORS, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });
}
export function validateManifest(manifest, requested) {
  const key = typeof manifest.release_key === 'string' && manifest.release_key.match(KEY);
  if (!key || manifest.product !== 'jashva' || manifest.channel !== 'beta' ||
    manifest.version !== key.slice(1, 4).join('.') || manifest.build !== Number(key[4]) || manifest.build > 2100000000 ||
    manifest.source_repository !== 'Tanvrit/app-jashva' || !/^[a-f0-9]{40}$/.test(manifest.source_commit) ||
    (requested !== 'latest' && manifest.release_key !== requested) ||
    !manifest.platforms || Array.isArray(manifest.platforms)) return false;
  return true;
}
export async function routeBeta(request, env, fetcher = fetch) {
  const path = new URL(request.url).pathname;
  if (!path.startsWith('/jashva/beta/')) return null;
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
  if (!['GET', 'HEAD'].includes(request.method)) return error(405, 'Method not allowed');
  const parts = path.split('/').slice(3);
  let version, platform;
  if (parts.length === 1 && parts[0] === 'latest.json') version = 'latest';
  else if (parts.length === 2 && (parts[0] === 'latest' || KEY.test(parts[0]))) {
    version = parts[0];
    if (parts[1] !== 'latest.json') platform = parts[1];
  } else return error(404, 'Unknown Jashva beta route');
  if (platform && !Object.hasOwn(EXT, platform)) return error(400, 'Unsupported Jashva beta platform');
  const manifestKey = `manifests/jashva/beta/${version}.json`;
  let manifest;
  if (env.ARTIFACTS) {
    const object = await env.ARTIFACTS.get(manifestKey);
    if (!object) return error(404, 'Beta manifest is not published');
    manifest = await object.json();
  } else {
    const response = await fetcher(`${RAW}/${manifestKey}`);
    if (!response.ok) return error(404, 'Beta manifest is not published');
    manifest = await response.json();
  }
  if (!validateManifest(manifest, version)) return error(503, 'Beta source/build metadata is invalid');
  if (!platform) return new Response(request.method === 'HEAD' ? null : JSON.stringify(manifest), {
    status: 200, headers: { ...CORS, 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=60' } });
  const row = manifest.platforms[platform];
  if (!row || row.available !== true) return error(503, 'This beta installer is not available');
  const stem = platform === 'linux-x64-app' ? 'linux-x64' : platform;
  const filename = `jashva-${manifest.release_key}-${stem}.${EXT[platform]}`;
  const target = `https://dl.tanvrit.com/releases/jashva/${manifest.release_key}/${filename}`;
  if (row.filename !== filename || row.r2_url !== target || !/^[a-f0-9]{64}$/.test(row.sha256) ||
    !Number.isSafeInteger(row.size_bytes) || row.size_bytes <= 0) return error(503, 'Beta installer custody is invalid');
  return new Response(null, { status: 302,
    headers: { ...CORS, Location: target, 'Cache-Control': 'no-store' } });
}
