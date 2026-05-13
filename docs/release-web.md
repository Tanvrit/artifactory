# release-web.md — Cloudflare Pages / Firebase Hosting pipeline

Callers:
- `<platform>/.github/workflows/release-web.yml` (Cloudflare Pages — default)
- `desipops/.github/workflows/release-web.yml` (Firebase Hosting — exception)

Templates:
- `tanvrit/artifactory/.github/workflows/release-web-template.yml` (Cloudflare Pages)
- `tanvrit/artifactory/.github/workflows/release-firebase-template.yml` (Firebase Hosting)

Runner: `ubuntu-latest`
Output: deployed Wasm/JS bundle at `<product>.pages.dev` or `<product>.firebaseapp.com`

## Why web is always-live (not a versioned download)

Unlike .dmg/.apk/.ipa which users download and install, the web app is the
canonical UI users access via a browser. We deploy on every push to `main`
rather than on version tags. The `update-manifest` dispatch records the
deploy with `version: <git-sha>` for audit purposes; the live URL is what
users actually use.

## Source-side requirements

The graceful-skip guard requires either:
- **`wasmJs { browser(); binaries.executable() }`** declared in build.gradle.kts (modern Compose Wasm), OR
- **`js(IR) { browser { … }; binaries.executable() }`** (legacy Compose-for-Web)

If neither, the workflow skips. (Pure Next.js sites like `tanvrit/web/`
have NO composeApp; they're called via the same template with overridden
`gradle_task` + `assemble_script` inputs.)

## Modern: wasmJs (Compose Multiplatform)

### Build setup

`composeApp/build.gradle.kts`:

```kotlin
import org.jetbrains.kotlin.gradle.ExperimentalWasmDsl

kotlin {
    @OptIn(ExperimentalWasmDsl::class)
    wasmJs {
        outputModuleName.set("composeApp")
        browser {
            commonWebpackConfig {
                outputFileName = "composeApp.js"
            }
        }
        binaries.executable()
    }

    sourceSets {
        wasmJsMain.dependencies {
            implementation("org.jetbrains.kotlin:kotlin-stdlib-wasm-js:2.3.10")
        }
    }
}
```

### Entry point

`composeApp/src/wasmJsMain/kotlin/main.kt`:

```kotlin
import androidx.compose.ui.window.CanvasBasedWindow

fun main() {
    CanvasBasedWindow(canvasElementId = "ComposeTarget") {
        App()
    }
}
```

### HTML host

`composeApp/src/wasmJsMain/resources/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title><Your Product></title>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; overflow: hidden; }
        canvas { display: block; }
    </style>
</head>
<body>
    <canvas id="ComposeTarget"></canvas>
    <script src="composeApp.js"></script>
</body>
</html>
```

## Caller workflow inputs

```yaml
jobs:
  release-web:
    uses: tanvrit/artifactory/.github/workflows/release-web-template.yml@main
    with:
      product: <product>
      gradle_task: ':composeApp:wasmJsBrowserDistribution'  # or :composeApp:patchWasmIndexHtml for content-hashed bundles
      wasm_output_dir: 'composeApp/build/dist/wasmJs/productionExecutable'
      landing_build_dir: 'web'  # optional — Next.js landing site to merge
      cf_project_name: '<product>-app'
      cf_branch: 'main'
    secrets: inherit
```

### Optional landing-site merge

If your repo has a Next.js (or any static) landing site at `web/` that
should sit alongside the Compose app, set `landing_build_dir: 'web'`. The
template runs:

1. `cd web && npm ci && npm run build` (produces `web/out/`)
2. Copies `web/out/` to `dist/` (landing site at root)
3. Copies `composeApp/build/dist/wasmJs/productionExecutable/` to `dist/app/`
4. Adds `dist/_redirects` with `/app/* /app/index.html 200` so client-side
   routing inside the Compose app works

Final URL structure: `<product>.pages.dev/` serves the marketing landing
site; `<product>.pages.dev/app` serves the Compose app.

## Firebase Hosting variant (desipops only)

desipops is the only platform on Firebase Hosting. Its caller calls
`release-firebase-template.yml` instead:

```yaml
uses: tanvrit/artifactory/.github/workflows/release-firebase-template.yml@main
with:
  product: desipops
  firebase_project_id: desipops-d17ed
  firebase_channel_id: live
  gradle_task: ':composeApp:wasmJsBrowserDistribution'
  wasm_output_dir: 'composeApp/build/dist/wasmJs/productionExecutable'
  landing_build_dir: 'web'
secrets:
  ARTIFACTS_REPO_TOKEN: ${{ secrets.ARTIFACTS_REPO_TOKEN }}
  FIREBASE_SERVICE_ACCOUNT_JSON: ${{ secrets.FIREBASE_SERVICE_ACCOUNT_DESIPOPS_D17ED }}
  GH_PACKAGES_TOKEN: ${{ secrets.GH_PACKAGES_TOKEN }}
```

Per-product Firebase service-account secret (one per Firebase project) lives
on the platform's own repo, not the org.

## Build pipeline (what the Cloudflare template does)

1. **Guard** — `wasmJs()` or `js()` declaration check.
2. **Setup** — Java 21 + Node 20 + Gradle.
3. **Build Wasm/JS bundle** — `./gradlew <gradle_task>` (default
   `:composeApp:wasmJsBrowserDistribution`). Produces
   `composeApp/build/dist/wasmJs/productionExecutable/composeApp.<hash>.js`
   + assets.
4. **Build landing site** (if `landing_build_dir` set) — `cd <dir>; npm ci;
   npm run build`.
5. **Assemble distribution** — default: merge wasm output + landing output
   into `dist/`. Custom: invoke `assemble_script` for products with
   special merge logic (admin's `dist/_headers`, etc.).
6. **Deploy** — `wrangler pages deploy dist --project-name=<cf_project_name>
   --branch=<cf_branch>`.
7. **Manifest dispatch** — `platform: web`, `version: <git-sha>`.

## Org-level secrets (Cloudflare)

| Secret | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → Create. Scopes: Pages:Edit, Account:Read |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare → right sidebar |
| `ARTIFACTS_REPO_TOKEN` | GitHub PAT with `repo` on tanvrit/artifactory |
| `GH_PACKAGES_TOKEN` | GitHub PAT with `read:packages` for SDK pulls |

## Per-product (Firebase only)

| Secret | Value |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_<PROJECT_UPPER>` | Firebase Console → Project Settings → Service Accounts → Generate new private key. The full JSON. |

## Content-hash patching (friendly, mandee, store)

Some platforms patch `index.html` post-build to reference the
content-hashed bundle filename. The pattern lives in each platform's
build.gradle.kts:

```kotlin
tasks.register("patchWasmIndexHtml") {
    dependsOn("wasmJsBrowserDistribution")
    // ... rewrites <script src> to point to composeApp.<hash>.js
}
```

Their caller workflow uses `gradle_task: ':composeApp:patchWasmIndexHtml'`
instead of the default `:composeApp:wasmJsBrowserDistribution`. The patch
ensures browser caching works (the `<script src>` matches the hashed
filename on disk).

New platforms can skip this — webpack's default behavior is fine for
non-hashed output.

## Custom domain wiring

After Cloudflare Pages creates `<product>-app.pages.dev`, point a real
domain at it:

1. Cloudflare → DNS → Add Record → CNAME → `app.<your-domain>` →
   `<product>-app.pages.dev` → Proxy: ON
2. Cloudflare → Pages → `<product>-app` → Custom Domains → Add → enter
   `app.<your-domain>`
3. Cloudflare auto-issues an SSL cert (Let's Encrypt) in ~minutes.

Or for `tanvrit.com` and subdomains — DNS records already exist; just
add the custom domain in Pages.

## Troubleshooting

**`wasmJsBrowserDistribution` fails with "JavaScript heap out of memory"**

Increase node heap: `NODE_OPTIONS=--max-old-space-size=8192` env var on
the build step.

**`wrangler pages deploy` fails with "Failed to upload assets"**

Cloudflare API token doesn't have Pages:Edit scope. Re-create with
`Edit Cloudflare Pages` template.

**Compose Wasm app loads but UI is blank**

Two common causes:
- The canvas element ID in `index.html` doesn't match the
  `canvasElementId` parameter in `main.kt`.
- `composeApp.js` 404s — check the path in the `<script src>`. If
  content-hashed, ensure `patchWasmIndexHtml` ran.

**Firebase deploy succeeds but the site doesn't update**

Firebase Hosting caches aggressively. Either wait ~5 min or invalidate
manually via `firebase hosting:channel:deploy live --only … --force`.

**Compose Wasm bundle is 50+ MB**

Compose Wasm is heavier than expected — the Compose UI framework + Skia
renderer + Kotlin stdlib + your app code add up. Targeting smaller bundle
size is a longer-term Compose Multiplatform roadmap item.

## v2 backlog

- Wasm/Skia bundle-size optimizations (skiko is the main weight).
- Per-product Cloudflare Workers for server-side rendering / API proxying.
- Preview deployments on PRs (one Pages branch per PR for visual review).
- Lighthouse CI integration to fail builds on perf regressions.
- Pre-rendered SEO landing pages (the Compose Wasm bundle isn't
  crawler-friendly today).
