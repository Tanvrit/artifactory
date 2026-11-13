'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const requireValue = (value, message) => { if (!value) throw new Error(message); };
function run(program, args, options = {}) {
  return execFileSync(program, args, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 1048576, ...options }).trim();
}
function verifyNative(contract, platform, artifact) {
  if (platform === 'windows-x64') {
    run('pwsh', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(__dirname, 'verify-windows.ps1'), '-Artifact', artifact]);
    return;
  }
  if (platform === 'linux-x64') {
    requireValue(run('dpkg-deb', ['--field', artifact, 'Architecture']) === 'amd64', 'DEB architecture must be amd64');
    const version = run('dpkg-deb', ['--field', artifact, 'Version']);
    requireValue(version === contract.version || version.startsWith(contract.version + '-'), 'DEB public version differs');
    return;
  }
  if (platform === 'linux-x64-app') {
    const fd = fs.openSync(artifact, 'r'); const header = Buffer.alloc(20);
    try { requireValue(fs.readSync(fd, header, 0, 20, 0) === 20, 'AppImage header is truncated'); }
    finally { fs.closeSync(fd); }
    requireValue(header.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) &&
      header[4] === 2 && header[5] === 1 && header.readUInt16LE(18) === 62, 'AppImage must be Linux x86_64');
    return;
  }
  requireValue(platform === 'macos-arm64', 'Unsupported native beta verification');
  run('xcrun', ['stapler', 'validate', artifact]);
  run('spctl', ['--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', artifact]);
  let mounts = [];
  try {
    const plist = run('hdiutil', ['attach', '-readonly', '-nobrowse', '-plist', artifact]);
    const parsed = JSON.parse(run('plutil', ['-convert', 'json', '-o', '-', '-'], { input: plist }));
    mounts = parsed['system-entities'].filter(row => row['mount-point']).map(row => row['mount-point']);
    requireValue(mounts.length === 1, 'Expected one read-only DMG mount'); const mount = mounts[0];
    const apps = fs.readdirSync(mount).filter(name => name.endsWith('.app'));
    requireValue(apps.length === 1 && apps[0] === 'Jashva.app', 'Unexpected direct application bundle');
    const app = path.join(mount, apps[0]);
    requireValue(!fs.lstatSync(app).isSymbolicLink(), 'Application bundle cannot be a symlink');
    run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', app]);
    run('spctl', ['--assess', '--type', 'execute', '--verbose=2', app]);
    const info = path.join(app, 'Contents/Info.plist');
    requireValue(run('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleIdentifier', info]) === 'com.jashva', 'macOS bundle identity differs');
    requireValue(run('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleShortVersionString', info]) === contract.version,
      'macOS public version differs');
    requireValue(run('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleVersion', info]) === String(contract.build),
      'macOS native build differs');
    const executable = run('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleExecutable', info]);
    requireValue(/^[A-Za-z0-9._-]+$/.test(executable), 'Unexpected macOS executable path');
    requireValue(run('lipo', ['-archs', path.join(app, 'Contents/MacOS', executable)]) === 'arm64', 'macOS installer must contain arm64 executable');
  } finally { for (const mount of mounts.reverse()) run('hdiutil', ['detach', mount]); }
}
module.exports = { verifyNative };
