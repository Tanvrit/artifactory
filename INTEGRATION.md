# Integrating Artifactory into Product Repos

This guide explains how each Tanvrit product repo connects to this artifactory repo
for desktop builds, icon management, and auto-updates.

---

## 1. Desktop Build Setup (Compose Desktop)

### Step 1: Add VERSION_NAME to gradle.properties

Add these lines to each product's root `gradle.properties`:

```properties
# App version (synced with artifacts.tanvrit.com manifests)
VERSION_NAME=1.0.0
VERSION_CODE=10000
```

### Step 2: Update build.gradle.kts

Update the `compose.desktop` block in your `composeApp/build.gradle.kts`
(or `app/build.gradle.kts` for School):

```kotlin
val appVersion = rootProject.findProperty("VERSION_NAME") as String? ?: "1.0.0"
val buildNumber = rootProject.findProperty("VERSION_CODE") as String? ?: "1"

compose.desktop {
    application {
        mainClass = "com.friendly.MainKt"  // update per product

        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Deb)
            packageName = "com.tanvrit.friendly"  // update per product
            packageVersion = appVersion

            macOS {
                bundleID = "com.tanvrit.friendly"  // update per product
                iconFile.set(project.file("src/desktopMain/resources/friendly.icns"))
                dmgPackageVersion = appVersion
                pkgPackageVersion = appVersion
            }
            windows {
                menuGroup = "Tanvrit"
                iconFile.set(project.file("src/desktopMain/resources/friendly.ico"))
                upgradeUuid = "F47AC10B-58CC-4372-A567-0E02B2C3D479"  // unique per product
            }
            linux {
                packageName = "tanvrit-friendly"  // update per product
                iconFile.set(project.file("src/desktopMain/resources/friendly-256.png"))
                debMaintainer = "team@tanvrit.com"
            }
        }
    }
}
```

### Step 3: Create desktop resources directory

```
composeApp/src/desktopMain/resources/
├── friendly.icns        ← synced from artifactory
├── friendly.ico         ← synced from artifactory
└── friendly-256.png     ← synced from artifactory
```

Icons are synced via `scripts/sync-to-apps.js`.

---

## 2. Release Workflow Setup

Add `.github/workflows/release-desktop.yml` to each product repo:

```yaml
name: Release Desktop

on:
  push:
    tags:
      - 'v[0-9]+.[0-9]+.[0-9]+'
  workflow_dispatch:
    inputs:
      version:
        required: true
        description: 'Version (e.g. 1.2.0)'

jobs:
  release:
    uses: tanvrit/artifactory/.github/workflows/release-desktop-template.yml@main
    with:
      product: friendly          # ← change per product
      version: ${{ github.ref_name || github.event.inputs.version }}
      gradle_module: composeApp
      main_class: com.friendly.MainKt
      package_name: com.friendly
      bundle_id: com.tanvrit.friendly
      windows_upgrade_uuid: F47AC10B-58CC-4372-A567-0E02B2C3D479
    secrets: inherit
```

### Required GitHub Secrets (per product repo)

| Secret | Description |
|--------|-------------|
| `ARTIFACTS_REPO_TOKEN` | PAT with write access to `tanvrit/artifactory` |
| `CF_R2_TOKEN` | Cloudflare R2 API token |
| `CF_ACCOUNT_ID` | Cloudflare account ID (`ce3f0ef57641c98d52af95c069bbb6a2`) |

---

## 3. In-App Update Checker

Add `UpdateChecker.kt` to your `jvmMain` source set:

```kotlin
// composeApp/src/jvmMain/kotlin/{package}/update/UpdateChecker.kt

package com.friendly.update  // change per product

import io.ktor.client.*
import io.ktor.client.call.*
import io.ktor.client.engine.okhttp.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.request.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class PlatformAsset(
    val url: String,
    val direct_url: String,
    val sha256: String,
    val size_bytes: Long,
    val format: String,
    val available: Boolean = false,
)

@Serializable
data class UpdateManifest(
    val product: String,
    val version: String,
    val build: Int,
    val released_at: String,
    val release_notes: String,
    val platforms: Map<String, PlatformAsset>,
)

data class UpdateInfo(
    val version: String,
    val releaseNotes: String,
    val downloadUrl: String,
    val sizeMb: Long,
)

object BuildConfig {
    // These are replaced by the generateBuildConfig Gradle task
    const val VERSION_NAME   = "1.0.0"
    const val PRODUCT_SLUG   = "friendly"
    const val UPDATE_CHECK_URL = "https://artifacts.tanvrit.com/friendly/latest.json"
}

class UpdateChecker {
    private val _updateAvailable = MutableStateFlow<UpdateInfo?>(null)
    val updateAvailable: StateFlow<UpdateInfo?> = _updateAvailable.asStateFlow()

    fun checkOnce() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val client = HttpClient(OkHttp) {
                    install(ContentNegotiation) {
                        json(Json { ignoreUnknownKeys = true })
                    }
                }
                val manifest = client.get(BuildConfig.UPDATE_CHECK_URL).body<UpdateManifest>()
                client.close()

                if (isNewer(manifest.version, BuildConfig.VERSION_NAME)) {
                    val platform = detectCurrentPlatform()
                    val asset = manifest.platforms[platform]
                    if (asset != null && asset.available) {
                        _updateAvailable.value = UpdateInfo(
                            version     = manifest.version,
                            releaseNotes = manifest.release_notes,
                            downloadUrl = asset.url,
                            sizeMb      = asset.size_bytes / 1_048_576L,
                        )
                    }
                }
            } catch (_: Exception) {
                // Silent — update check must never crash the app
            }
        }
    }

    private fun detectCurrentPlatform(): String {
        val os   = System.getProperty("os.name", "").lowercase()
        val arch = System.getProperty("os.arch", "").lowercase()
        return when {
            os.contains("mac") && (arch.contains("aarch64") || arch.contains("arm")) -> "macos-arm64"
            os.contains("mac")     -> "macos-x64"
            os.contains("windows") -> "windows-x64"
            else                   -> "linux-x64"
        }
    }

    private fun isNewer(remote: String, local: String): Boolean {
        val remoteParts = remote.split(".").mapNotNull { it.toIntOrNull() }
        val localParts  = local.split(".").mapNotNull { it.toIntOrNull() }
        for (i in 0 until maxOf(remoteParts.size, localParts.size)) {
            val r = remoteParts.getOrElse(i) { 0 }
            val l = localParts.getOrElse(i) { 0 }
            if (r > l) return true
            if (r < l) return false
        }
        return false
    }
}
```

---

## 4. Product Configuration Reference

| Product | Gradle Module | Main Class | Bundle ID | Windows UUID |
|---------|--------------|------------|-----------|-------------|
| Friendly | `composeApp` | `com.friendly.MainKt` | `com.tanvrit.friendly` | `F47AC10B-58CC-4372-A567-0E02B2C3D479` |
| DesiPops | `composeApp` | `com.desipops.MainKt` | `com.tanvrit.desipops` | `A1B2C3D4-E5F6-7890-ABCD-EF1234567890` |
| Mandee | `composeApp` | `business.mandee.ai.MainKt` | `com.tanvrit.mandee` | `B2C3D4E5-F6A7-8901-BCDE-F12345678901` |
| Swyft | `composeApp` | `com.tanvrit.swyft.MainKt` | `com.tanvrit.swyft` | `C3D4E5F6-A7B8-9012-CDEF-012345678902` |
| School | `app` (→ composeApp) | `com.school.MainKt` | `com.tanvrit.school` | `D4E5F6A7-B8C9-0123-DEF0-123456789003` |
| Wedding | `composeApp` | `com.friendly.wedding.MainKt` | `com.tanvrit.wedding` | `E5F6A7B8-C9D0-1234-EF01-234567890004` |
| Control | `app` | `com.tanvrit.control.app.MainKt` | `com.tanvrit.control` | `F6A7B8C9-D0E1-2345-F012-345678900005` |

---

## 5. Bumping Versions

```bash
# From the artifactory directory
cd /Users/viveksingh/Developer/artifactory

# Bump a specific product
./scripts/bump-version.sh friendly 1.2.0

# See all current versions
./scripts/bump-version.sh --list

# Then tag and release
cd /Users/viveksingh/Developer/tanvrit/platforms/friendly
git tag v1.2.0
git push --tags
# → triggers release-desktop.yml → builds all platforms → uploads to artifacts repo
```

---

## 6. Cloudflare Worker Deployment

```bash
cd /Users/viveksingh/Developer/artifactory/worker
npm install

# Deploy to production (artifacts.tanvrit.com)
npm run deploy

# Test locally
npm run dev
```

**Required Cloudflare setup:**
1. Create R2 bucket named `tanvrit-artifacts`
2. Create API token with R2 read/write permissions
3. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` to environment

---

## 7. Syncing Icons to Apps

After running icon generation (`node scripts/generate-icons.js --all`):

```bash
# Preview what will be synced (dry run)
node scripts/sync-to-apps.js --all --dry-run

# Actually sync
node scripts/sync-to-apps.js --all

# Sync specific product
node scripts/sync-to-apps.js --product friendly
```

The sync script copies icons into the correct resource directories in the Tanvrit monorepo at `TANVRIT_ROOT` (default: `/Users/viveksingh/Developer/tanvrit`).
