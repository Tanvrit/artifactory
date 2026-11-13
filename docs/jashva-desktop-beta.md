# Jashva desktop public beta

This is an explicit, additive channel for Jashva. Existing stable release callers,
URLs, manifests and catalog entries keep their current contract. A beta does not
submit a Mac App Store package or change a store release already in review.

## Identity and source gates

The prelaunch public version stays `1.0.0`; `VERSION_CODE` is the independently
allocated native build number. The operator supplies `release_channel=beta`, the
full `source_commit`, and `expected_build_code` to the existing per-OS App workflow.
The code must equal both the workflow event SHA and checkout HEAD, and must be
reachable from the approved remote `main`. A manually selected immutable candidate
tag can therefore remain valid after `main` advances. An arbitrary PR source,
modified tracked source, missing target, mismatched version/build or unsupported
native host fails before packaging/publication. No helper edits version properties
or dependency pins.

The resulting transport identity is `1.0.0-beta.<build>`:

- GitHub prerelease tag: `jashva-v1.0.0-beta.<build>`.
- Installer: `jashva-1.0.0-beta.<build>-<platform>.<extension>`.
- R2 prefix: `releases/jashva/1.0.0-beta.<build>/`.
- Platform custody receipt: `jashva-1.0.0-beta.<build>-<platform>.release.json`.

Receipts identify public version, native build, exact source repository/SHA,
channel, platform, file size and SHA-256. They contain no account or credential
information. A shared GitHub prerelease must contain exactly the matching source
marker before another OS appends its own assets.

## Native prerequisites remain mandatory

macOS uses the existing ARM64 host, Developer ID identity and notarization
configuration. Qualification checks stapling, Gatekeeper assessment, bundle ID,
public version, native `CFBundleVersion`, code signature and actual ARM64
executable. The direct beta channel never invokes App Store Connect upload.

Windows uses the existing native x64 packaging host and existing signing identity.
Jashva beta MSI publication requires a valid Authenticode signature, a trusted
timestamp (the existing signer requests RFC3161), certificate purposes and successful strict `signtool`
verification. An absent certificate, signing failure or absent/untrusted timestamp
fails closed. No signing certificate is generated. The existing MSI UpgradeCode
is unchanged. Same-public-version MSI upgrade behavior must be exercised on the
real Windows host before a later beta is advertised as an in-place upgrade.

Linux DEB/AppImage packaging stays on Linux X64. The DEB must identify amd64 and
the correct public version; the optional AppImage must be an x86_64 executable.
A failed/unbuilt AppImage is not advertised. ARM64 portable-check runners are not
substitutes for either the Linux X64 or Windows native packaging host. Missing
hosts/signing prerequisites remain release blockers rather than invented targets.

## Immutable publication and manifests

Installers, checksums and receipts are never overwritten. R2 uses atomic
`If-None-Match: *` creation. Existing objects can be reused only when both providers
return exactly matching full-file SHA-256/size and the matching
source/build/platform receipt. Unknown ownership, partial unreceipted uploads,
changed bytes or an uncertain existence check fail. A new allocated build is the
safe path after a genuinely different rebuild; deleting or replacing existing
artifacts is not part of this flow.

Each publisher requests a separate manifest job only after complete GitHub and R2
readback verification. Requesting that job is not a publication-complete receipt.
The per-product manifest workflow retains in-flight work and scans all completed
platform receipts, so distinct native publishers can complete independently.
It rechecks receipt equality and actual artifact bytes before merging available
platforms. Previously qualified platforms must remain independently verifiable or the update fails without changing either manifest. Missing new platforms stay unavailable; older builds cannot roll back the
latest beta pointer. Beta metadata never advances the stable catalog or a forced
update floor.

Explicit beta consumers use:

- `/jashva/beta/latest.json`.
- `/jashva/beta/1.0.0-beta.<build>/latest.json`.
- `/jashva/beta/latest/<platform>` or the exact release-key equivalent.

Worker and Pages use one shared beta handler. It rejects malformed source/build
metadata and redirects only to the exact qualified R2 object. `-PdesktopChannel=beta`
packages this explicit consumer selection; ordinary direct builds use Jashva's
stable manifest, and Mac App Store builds suppress update discovery. The updater
uses the generated public version and native build, so a newer beta can be found
while the public version remains `1.0.0`. Update discovery does not install code.

## Qualification before a real release

Run the synthetic contracts without credentials or external writes:

```sh
node --test scripts/test-product-registration.cjs .github/actions/desktop-beta/test-*.cjs
python3 scripts/test-desktop-beta-workflows.py
```

The dedicated portable self-test workflow runs these on the existing Linux ARM64
pool. App wrapper guards and the isolated `DesktopUpdateManifestTest` additionally
belong to the App repository. A passing synthetic test does not replace a native
build, real signing/notarization checks, installer launch/upgrade testing, the
asynchronous manifest job, Worker/Pages deployment or public-byte verification.
Only the coordinating release owner allocates build numbers, dispatches releases
and declares the resulting artifacts/public routes available.
