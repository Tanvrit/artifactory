export const GITHUB_RAW = 'https://raw.githubusercontent.com/tanvrit/artifactory/main';

export const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export const VALID_PRODUCTS = [
  'friendly',
  'desipops',
  'mandee',
  'swyft',
  'bharat-bandhu',
  'school',
  'wedding',
  'control',
];

export const PLATFORM_FILE_MAP = {
  'macos-arm64':        { ext: 'dmg',      label: 'macOS Apple Silicon' },
  'macos-x64':          { ext: 'dmg',      label: 'macOS Intel' },
  'macos-universal':    { ext: 'dmg',      label: 'macOS Universal' },
  'windows-x64':        { ext: 'msi',      label: 'Windows x64' },
  'linux-x64':          { ext: 'deb',      label: 'Linux (DEB)' },
  'linux-x64-rpm':      { ext: 'rpm',      label: 'Linux (RPM)' },
  'linux-x64-appimage': { ext: 'AppImage', label: 'Linux (AppImage)' },
};

export const MIME_MAP = {
  svg:     'image/svg+xml',
  png:     'image/png',
  ico:     'image/x-icon',
  icns:    'image/x-icns',
  zip:     'application/zip',
  json:    'application/json',
  dmg:     'application/x-apple-diskimage',
  msi:     'application/x-msi',
  deb:     'application/vnd.debian.binary-package',
  rpm:     'application/x-rpm',
  appimage:'application/x-executable',
};

export function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message, status }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

export async function fetchManifest(env, product, versionOrLatest) {
  const manifestKey = versionOrLatest === 'latest'
    ? `manifests/${product}/latest.json`
    : `manifests/${product}/${versionOrLatest}.json`;

  if (env.ARTIFACTS) {
    const obj = await env.ARTIFACTS.get(manifestKey);
    if (!obj) return null;
    return obj.json();
  } else {
    const res = await fetch(`${GITHUB_RAW}/${manifestKey}`);
    if (!res.ok) return null;
    return res.json();
  }
}
