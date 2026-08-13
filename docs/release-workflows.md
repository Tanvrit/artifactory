# Release Workflows — Per-OS Convention

This document is the canonical reference for how every Tanvrit platform ships
a release. It supersedes the older per-platform release docs that lived in
each repo's README before the per-OS workflow restructure.

## Convention in one sentence

Every platform repo (friendly, mandee-business, store, swyft, wedding, school,
admin, ai, auditor, automator, compute, control, market, tanvrit, desipops)
has exactly **six** workflow files under `.github/workflows/`:

```
release-android.yml
release-ios.yml
release-macos.yml
release-windows.yml
release-linux.yml
release-web.yml
```

Each is a thin caller that delegates to a reusable template hosted in
`tanvrit/artifactory`. The templates do the real work (build, sign, package,
upload to GitHub Release, mirror to Cloudflare R2, dispatch manifest update).

## Why per-OS files (not one matrix)

History: from early-2026 through 2026-05 we had one `release-desktop-template.yml`
that produced macOS arm64 + macOS x64 + Windows MSI + Linux DEB + Linux AppImage
in a single matrix. It worked, but:

1. A single iOS or Android failure aborted the entire run, including the
   already-built desktop artifacts.
2. The file was a god-file (300+ lines, 4-OS interleaved). New OS additions
   meant rewriting it instead of dropping in a new file.
3. The matrix made it impossible to set per-OS concurrency limits or per-OS
   retries.

Per-OS files (each one self-contained) solved all three. The retirement
commit on `tanvrit/artifactory` was `53c7436`.

## The templates

All under `tanvrit/artifactory/.github/workflows/`:

| Template | Runner | Output |
|---|---|---|
| `release-android-template.yml` | `ubuntu-latest` | Signed `.apk` + `.aab`; optional Play Store upload |
| `release-ios-template.yml` | `macos-14` | Signed `.ipa` via xcodebuild archive + exportArchive |
| `release-macos-template.yml` | `macos-14` + `macos-13` (two parallel jobs) | Signed + notarized `.dmg` for arm64 + x64 |
| `release-windows-template.yml` | `windows-latest` | `.msi` (Authenticode signing scaffolded; gated on `WINDOWS_SIGNING_CERT`) |
| `release-linux-template.yml` | `ubuntu-latest` | `.deb` + `.AppImage` via appimagetool |
| `release-web-template.yml` | `ubuntu-latest` | Cloudflare Pages deploy of Wasm/JS bundle + optional Next.js landing merge |
| `release-firebase-template.yml` | `ubuntu-latest` | Firebase Hosting deploy (used by `desipops`; everyone else uses Cloudflare Pages) |

Each template begins with a **graceful-skip guard** that checks whether the
caller platform's source even targets that OS:

- macOS/Windows/Linux: requires `jvm()` or `jvm("desktop")` declaration in
  `composeApp/build.gradle.kts` (or `app/build.gradle.kts`).
- Android: requires `androidTarget {}` block + an Android Gradle plugin in
  the build file.
- iOS: requires `iosArm64()` or `iosSimulatorArm64()` declaration AND an
  `iosApp/iosApp.xcodeproj` Xcode wrapper.
- Web: requires `wasmJs {}` or `js {}` declaration.

If the guard fails, the workflow exits 0 with a `::notice::skipped: …` GitHub
annotation. No spurious failure email.

## Triggers

- `release-{android,ios,macos,windows,linux}.yml`: trigger on git tag
  matching `v[0-9]+.[0-9]+.[0-9]+` OR `workflow_dispatch` with explicit
  `version` input.
- `release-web.yml`: trigger on `push: branches: [main]` OR
  `workflow_dispatch`. (Web is always-live, not a versioned download.)

A single semver tag like `v1.2.0` fires all five non-web workflows in parallel.
Six artifacts (because macOS template produces arm64+x64) land on one GitHub
Release titled `<product>-v1.2.0`.

## Per-platform caller shape

Each caller is ~20 lines. Example (friendly's `release-macos.yml`):

```yaml
name: "Friendly — Release macOS"

on:
  push:
    tags: ['v[0-9]+.[0-9]+.[0-9]+']
  workflow_dispatch:
    inputs:
      version:
        required: true
        description: 'Version to release (e.g. 1.2.0)'

jobs:
  release-macos:
    uses: tanvrit/artifactory/.github/workflows/release-macos-template.yml@main
    with:
      product: friendly
      version: ${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || github.event.inputs.version }}
      gradle_module: composeApp
      main_class: com.friendly.MainKt
      package_name: com.friendly
      bundle_id: com.tanvrit.friendly
    secrets: inherit
```

Inputs are passed as plain strings; secrets are inherited via `secrets: inherit`
(no per-caller secret list — secrets live at the org or per-product repo level).

## Manifest fan-out

Each template's last step is `peter-evans/repository-dispatch@v3` posting an
`update-manifest` event to `tanvrit/artifactory` with this payload:

```json
{
  "product": "friendly",
  "version": "1.2.0",
  "platform": "macos-arm64",
  "build": "1234",
  "released_at": "2026-05-13T..."
}
```

The `update-manifest.yml` receiver runs `scripts/update-manifest.js --platform
macos-arm64 friendly 1.2.0`, which loads `manifests/friendly/latest.json`,
patches the single platform entry (sha256 + size + url + r2_url), bumps
version/build/released_at, and commits.

The receiver is gated by a `concurrency.group: "manifest-${product}-${version}"`
so the six dispatches queue rather than race. Six small commits land on
`tanvrit/artifactory@main` per release — audit-trail-friendly.

## Manifest schema

`manifests/<product>/latest.json` looks like:

```json
{
  "product": "friendly",
  "display_name": "Friendly",
  "tagline": "POS & Retail for Bharat",
  "version": "1.2.0",
  "build": 1234,
  "released_at": "2026-05-13T16:42:11Z",
  "platforms": {
    "macos-arm64": {
      "available": true,
      "filename": "friendly-1.2.0-macos-arm64.dmg",
      "sha256": "…",
      "size": 89012345,
      "direct_url": "https://github.com/Tanvrit/artifactory/releases/download/friendly-v1.2.0/friendly-1.2.0-macos-arm64.dmg",
      "r2_url": "https://dl.tanvrit.com/releases/friendly/1.2.0/friendly-1.2.0-macos-arm64.dmg"
    },
    "ios": { "available": false },
    "android": { "available": false },
    "...": "..."
  }
}
```

When the platform's source target hasn't yet been declared (Bucket C/D/E/F/G
in the master plan), `available: false` stays until that platform's first
real release. Re-runs flip to `true`; never the reverse.

## R2 vs GitHub Release URLs

Every artifact lands in two places:

1. **GitHub Release** — `softprops/action-gh-release@v2` with
   `update-release-if-exists: true`. Six parallel workflows safely append to
   one Release tag.
2. **Cloudflare R2** — `curl --aws-sigv4` PUT to
   `tanvrit-artifacts/releases/<product>/<version>/<file>` via R2's
   S3-compatible endpoint at `https://<account>.r2.cloudflarestorage.com`.

The Cloudflare Worker at `tanvrit/artifactory/worker/` resolves
`https://artifacts.tanvrit.com/<product>/<version>/<os>` to either:
- The R2 URL (302) when `r2_url` is set in `latest.json` — fast, no proxy chain.
- The GitHub Release `direct_url` otherwise — fallback for pre-R2 manifests.

`r2_url` is a **`dl.tanvrit.com`** URL. That hostname is bound to the same
Worker and treats the entire request path as the R2 object key, so
`https://dl.tanvrit.com/releases/<product>/<version>/<file>` maps 1:1 onto the
key the mirror step above uploads, and it is the only download path that
supports HTTP Range (a dropped 260 MB download resumes instead of restarting).

> **Do not point `r2_url` at `https://artifacts.tanvrit.com/releases/…`.** That
> host has no `/releases/` route — `releases` is not in the Worker's
> `VALID_PRODUCTS`, so the request falls through to the portal proxy and the
> user gets the portal's Next.js 404 page (HTML, ~9 KB) instead of the
> installer. This was live until 2026-08-13; every manifest the generator wrote
> advertised that dead link. The fix was to emit the `dl.tanvrit.com` URL, not
> to add a route, so that there is exactly one canonical download host.

### The mirror step fails the job (changed 2026-08-11)

It used to be `continue-on-error: true` behind an `R2_KEY_PRESENT` gate, so a
missing credential **or a failed upload** reported SUCCESS having mirrored
nothing. That is not a safe degradation, because `scripts/update-manifest.js`
writes `r2_url` into every manifest *unconditionally* and the worker *prefers*
`r2_url` over `direct_url` — a skipped mirror therefore publishes a release
whose download link points at an object that was never uploaded. (This bit
`tanvrit/ai` in production: three installers had to be mirrored by hand after a
"successful" release.)

The step now:
- runs on every supported build, credentials or not;
- emits `::error::` and exits 1 when `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` /
  `CF_ACCOUNT_ID` (or `CLOUDFLARE_ACCOUNT_ID`) are absent — a deliberate loud skip;
- reads every uploaded object back with a signed ranged GET and compares its size
  to the local file, failing on a missing or truncated object;
- fails if no expected artifact existed on disk at all.

A red mirror step loses **no** artifact — the binaries are attached to the GitHub
Release by the preceding step. It only reports, truthfully, that the CDN download
links for that release will not work.

It does, however, stop the release from being **published to the CDN at all**:
"Dispatch manifest update" runs *after* the mirror, so a red mirror means no
`repository_dispatch`, no regenerated `manifests/<product>/latest.json`, and no
catalog entry for that version. That is the intended ordering — a manifest whose
`r2_url` points at an object that was never uploaded is worse than no manifest —
but it means a mirror failure is a *release-visible* failure, not a cosmetic one.
Fix the credentials and re-run the workflow; the mirror is idempotent (a re-PUT
overwrites the same key).

> **Repo-level secrets required.** The org is on the free plan, so org-level
> secrets are *not* injected into private repos; they arrive empty. Every caller
> repo needs `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `CF_ACCOUNT_ID` set at
> **repo** level (`gh secret list -R tanvrit/<repo>` to check).

### Caller-repo secret inventory (audited 2026-08-13)

Sixteen repos call these templates, all via `secrets: inherit`. Exactly **one**
carries the R2 key pair; the other fifteen go red at their next release until the
pair is provisioned. `CF_ACCOUNT_ID`/`CLOUDFLARE_ACCOUNT_ID` is *not* the gap —
every repo has at least `CLOUDFLARE_ACCOUNT_ID`, which the step accepts as a
fallback.

| Caller repo | `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` | Account id present |
|---|---|---|
| `Tanvrit/admin-portal` | **yes** | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-main` (friendly, friendly-jobs, friendly-te) | no | `CF_ACCOUNT_ID` |
| `Tanvrit/app-desipops` | no | `CF_ACCOUNT_ID` |
| `Tanvrit/app-school` | no | `CF_ACCOUNT_ID` |
| `Tanvrit/app-swyft` | no | `CF_ACCOUNT_ID` |
| `Tanvrit/app-wedding` | no | `CF_ACCOUNT_ID` |
| `tanvrit/control` | no | `CF_ACCOUNT_ID` |
| `Tanvrit/app-bharat-online` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-auditor` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/automator` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `tanvrit/compute` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-lekhita` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-mandee-biz` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-market` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/app-store` | no | `CLOUDFLARE_ACCOUNT_ID` |
| `Tanvrit/website` | no | `CLOUDFLARE_ACCOUNT_ID` |

`Tanvrit/ai` has the R2 pair but is **not** a caller — its `release-*.yml` are
standalone workflows that mirror on their own; the templates do not apply to it.

Regenerate this table with:

```bash
for r in $(…caller list…); do echo "$r :: $(gh secret list -R "$r" | awk '{print $1}' | tr '\n' ',')"; done
```

## Secrets reference

All secrets are inherited via `secrets: inherit` in caller workflows. They
live at the org level (`Tanvrit`) or per-product repo. To rotate any one,
update it in GitHub → Settings → Secrets → Actions and trigger a new release.
The new value takes effect on next workflow run.

### Org-level (inherited everywhere)

| Secret | Used by | Source |
|---|---|---|
| `APPLE_CERTIFICATE` (.p12 base64), `_PASSWORD` | macOS DMG codesign | Apple Developer Portal → Developer ID Application cert export |
| `APPLE_NOTARIZATION_APPLE_ID`, `_PASSWORD`, `APPLE_TEAM_ID` | macOS notarytool | App Store Connect → app-specific password |
| `IOS_SIGNING_CERT` (.p12 base64), `_PASSWORD` | iOS .ipa codesign | Apple Developer Portal → iOS Distribution cert |
| `APP_STORE_CONNECT_API_KEY_ID`, `_ISSUER_ID`, `_KEY_P8` (base64) | TestFlight upload (deferred to v2) | App Store Connect → Users & Access → Keys |
| `ANDROID_SIGNING_KEYSTORE` (.jks base64), `_PASSWORD`, `_KEY_ALIAS`, `_KEY_PASSWORD` | Android signing | `keytool -genkey -keystore tanvrit-release.jks` (one-time) |
| `PLAY_STORE_SERVICE_ACCOUNT_JSON` | Play Store internal-track upload | Google Cloud Console → IAM → Play publishing SA |
| `WINDOWS_SIGNING_CERT`, `_PASSWORD` | MSI Authenticode | DigiCert / Sectigo cert |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `CF_ACCOUNT_ID` | R2 mirror | Cloudflare R2 dashboard → S3 token |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Pages deploy | Cloudflare dashboard |
| `GH_PACKAGES_TOKEN` | Gradle SDK + core pulls from GitHub Packages | GitHub PAT with `read:packages` |
| `ARTIFACTS_REPO_TOKEN` | Manifest dispatch + GitHub Release uploads | GitHub PAT with `repo` scope on tanvrit/artifactory |

### Per-product (live in the platform's own repo settings)

| Secret | Used by | Source |
|---|---|---|
| `IOS_PROVISIONING_PROFILE` (.mobileprovision base64) | iOS exportArchive | Apple Developer Portal — one per bundle ID |
| `FIREBASE_SERVICE_ACCOUNT_<PROJECT>` | Firebase Hosting (desipops only) | Firebase console → project settings → service accounts |

## Adding a new platform

1. Create a new GitHub repo `Tanvrit/<product>` (or `Tanvrit/app-<product>`).
2. Bootstrap your project (`composeApp/` or `app/` shape with at least one
   KMP target). See the per-OS deep-dive docs (`docs/release-{android,ios,
   web,macos,windows,linux}.md`) for the source-side requirements.
3. Copy the six `release-<os>.yml` callers from an existing platform
   (`platforms/friendly/.github/workflows/release-*.yml` is the canonical
   reference).
4. Edit each caller's `product`, `gradle_module`, `package_name`, `main_class`,
   `bundle_id` inputs.
5. Add a `PRODUCT_META` entry in
   `tanvrit/artifactory/scripts/update-manifest.js` with display_name +
   tagline. The `PLATFORM_CONFIGS` table uses the shared `ALL_OS_KEYS`
   constant — no per-product OS list to maintain.
6. Push a `v0.0.1-bootstrap` tag and watch six workflows fire. Graceful-skip
   on any OS whose source target you haven't built yet.
7. Add source-target declarations in subsequent commits; the workflows
   light up automatically.

## Adding a new OS target

(Hypothetical — say we want to add `release-haiku-template.yml` for the
Haiku OS, just because.)

1. Author the template under
   `tanvrit/artifactory/.github/workflows/release-haiku-template.yml`. Pattern:
   - Trigger on `workflow_call` with `product` + `version` + ... inputs.
   - Begin with a graceful-skip guard.
   - Build → rename → checksum → upload-to-Release → mirror-to-R2 → dispatch.
2. Add a `'haiku-x64': 'pkg'` entry to `PLATFORM_EXT` in
   `scripts/update-manifest.js`.
3. Add `'haiku-x64'` to `ALL_OS_KEYS`.
4. Add a `release-haiku.yml` caller to each platform repo (a 25-line shim).
5. Watch the existing graceful-skip behavior: platforms whose source doesn't
   target Haiku will skip; the rest will build.

## Adding a new product target to an existing platform

(Say `compute` wants to add Android.)

1. In `compute/composeApp/build.gradle.kts`, add `androidTarget {}` + the
   `android {}` block (namespace, defaultConfig, signingConfigs, buildTypes).
2. Add `composeApp/src/androidMain/AndroidManifest.xml` + `MainActivity.kt`.
3. Add `androidMain.dependencies { … }` block.
4. (If first Android target in the repo) add `androidApplication` plugin
   alias to `libs.versions.toml` and apply it in `build.gradle.kts`.
5. Generate icons via `tools/generate-platform-icons.js --product compute`
   (see `docs/release-android.md` for the icon size catalog).
6. Existing `release-android.yml` caller starts producing a real APK next
   release tag — no caller change needed.

Forward-only: don't remove any existing target, ever.

## Rolling back a bad release

You can't, by design. The release pipeline is forward-only:

- GitHub Release tags can be re-created with the same name; the upload step
  uses `update-release-if-exists: true` to append. Pushing `v1.2.0` a second
  time appends, doesn't replace.
- Manifest `latest.json` is recomputed from the artifacts that have
  successfully landed. A failed build doesn't degrade an earlier successful
  build's manifest entry.
- If a release is genuinely broken, ship `v1.2.1` (a hotfix). The portal
  worker resolves to `latest`, so users see the fixed version on next
  download.
- R2 retention is 10 versions per product (planned — a Worker cron job to
  prune is on the v2 roadmap).

## Verification at each phase

After landing a new template or modifying an existing one, verify on
`friendly` (the canonical pilot):

1. Bump `friendly/gradle.properties` to a test version like `1.99.0-test`
   on a `migration/<name>` branch.
2. Push the branch + a matching tag.
3. Wait for all six workflows. Each takes 5–15 min.
4. Verify the GitHub Release `friendly-v1.99.0-test` has six artifacts +
   six `.sha256` files.
5. Verify R2 `tanvrit-artifacts/releases/friendly/1.99.0-test/` has the
   same six artifacts.
6. Verify `manifests/friendly/latest.json` reflects all six platform keys
   `available: true`.
7. Verify the worker URL
   `https://artifacts.tanvrit.com/friendly/1.99.0-test/macos-arm64`
   resolves (302) to either R2 or the GitHub Release download — and then
   **follow the redirect** (`curl -IL`) and confirm the final response is the
   binary (`content-type: application/x-apple-diskimage`, `accept-ranges:
   bytes`), not `text/html`. A 302 alone proves nothing: the dead
   `artifacts.tanvrit.com/releases/…` link 302'd correctly and then served the
   portal's 404 page.
8. Clean up the test tag + Release + manifest commits.

## Further reading

- `docs/release-android.md` — Android signing, packageName, Play track, ABI splits.
- `docs/release-ios.md` — Xcode project setup, provisioning profiles, App Store Connect, TestFlight (v2).
- `docs/release-macos.md` — Notarization, DMG layout, universal vs split DMGs.
- `docs/release-windows.md` — UpgradeUuid, MSI install location, Authenticode.
- `docs/release-linux.md` — DEB vs AppImage, distro coverage, sandboxing.
- `docs/release-web.md` — Cloudflare Pages vs Firebase Hosting, custom domain wiring, content-hash patching.

## Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-04 | Replace single desktop template with per-OS templates | Failure isolation, no god-files |
| 2026-04 | Introduce R2 mirror | Faster downloads outside the US (Cloudflare's anycast) |
| 2026-04 | Worker prefers R2 over GitHub when both URLs present | Same |
| 2026-05-11 | Final retirement of release-desktop-template.yml | Zero callers remained |
| 2026-05-12 | Expand `PLATFORM_CONFIGS` to use `ALL_OS_KEYS` constant | Every platform has every target; no per-product OS list to maintain |
| 2026-05-13 | Author this canonical doc | Bus-factor protection |
