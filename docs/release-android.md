# release-android.md — Android .apk / .aab pipeline

Caller: `<platform>/.github/workflows/release-android.yml`
Template: `tanvrit/artifactory/.github/workflows/release-android-template.yml`
Runner: `ubuntu-latest`
Output: `<product>-<version>-android.apk` + signed AAB (when signing secrets present)

## Why GitHub-hosted ubuntu-latest (not self-hosted)

Self-hosted runners share Gradle home + Android SDK between runs. A corrupted
transform cache from one Compose build can poison every subsequent build —
historically a recurring flake source. GitHub-hosted runners build from a
clean filesystem every time; ~3 min slower than a warm self-hosted runner,
but eliminates a class of "works locally, fails in CI" issues.

The PR-time CI gate (`ci-android.yml`, the renamed-from-`assemble_android.yml`)
continues to use `[self-hosted, tanvrit]` for fast iteration on PR builds.
Only release builds — the ones that actually ship to Play — go through
GitHub-hosted.

## Source-side requirements

The template's graceful-skip guard requires:

1. **`androidTarget()`** block in the caller's `composeApp/build.gradle.kts`
   or `app/build.gradle.kts`:
   ```kotlin
   androidTarget {
       @OptIn(ExperimentalKotlinGradlePluginApi::class)
       compilerOptions { jvmTarget.set(JvmTarget.JVM_11) }
   }
   ```
2. **`com.android.application`** plugin applied:
   ```kotlin
   plugins {
       alias(libs.plugins.androidApplication)  // or libs.plugins.android.application
   }
   ```
3. **`android {}`** block declaring `namespace`, `compileSdk`, `defaultConfig`.

If any is missing → exit 0 with skip notice.

## Minimal Android scaffold

For platforms adding Android for the first time, the smallest workable scaffold:

### `composeApp/build.gradle.kts` additions

```kotlin
plugins {
    // existing plugins…
    alias(libs.plugins.android.application)
}

kotlin {
    androidTarget()
    // existing targets (iOS, JVM, Wasm, etc.)…

    sourceSets {
        androidMain.dependencies {
            implementation(libs.androidx.activity.compose)
        }
        // existing dependency blocks…
    }
}

android {
    namespace = "com.tanvrit.<product>"
    compileSdk = libs.versions.android.compileSdk.get().toInt()
    defaultConfig {
        applicationId = "com.tanvrit.<product>"
        minSdk = libs.versions.android.minSdk.get().toInt()
        targetSdk = libs.versions.android.targetSdk.get().toInt()
        versionCode = (findProperty("VERSION_CODE") as? String)?.toIntOrNull() ?: 10000
        versionName = findProperty("VERSION_NAME") as? String ?: "1.0.0"
    }
    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
    buildTypes {
        getByName("release") { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}
```

### `gradle/libs.versions.toml` additions

```toml
[versions]
agp = "9.1.0"
android-compileSdk = "37"
android-minSdk = "24"
android-targetSdk = "36"
androidx-activity = "1.12.2"

[libraries]
androidx-activity-compose = { module = "androidx.activity:activity-compose", version.ref = "androidx-activity" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
```

### `composeApp/src/androidMain/AndroidManifest.xml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET"/>
    <application
        android:allowBackup="true"
        android:label="<Your Product>"
        android:theme="@android:style/Theme.Material.Light.NoActionBar">
        <activity android:exported="true" android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

### `composeApp/src/androidMain/kotlin/com/tanvrit/<product>/MainActivity.kt`

```kotlin
package com.tanvrit.<product>

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            App()  // or your stub Compose UI
        }
    }
}
```

## Signing config

There are two patterns in use:

### Pattern A: env-var-driven (recommended for new platforms)

```kotlin
android {
    signingConfigs {
        create("release") {
            val ksPath = System.getenv("ANDROID_KEYSTORE_PATH")
            if (!ksPath.isNullOrBlank()) {
                storeFile = file(ksPath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD") ?: ""
                keyAlias = System.getenv("ANDROID_KEY_ALIAS") ?: ""
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD") ?: ""
            }
        }
    }
    buildTypes {
        getByName("release") {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}
```

In CI, the template writes `ANDROID_SIGNING_KEYSTORE` (base64 .jks) to a
temp file and sets `ANDROID_KEYSTORE_PATH` to that file's path. Locally,
`./gradlew :composeApp:assembleRelease` without those env vars produces an
unsigned APK.

### Pattern B: checked-in keystore path (friendly's legacy pattern)

```kotlin
signingConfigs {
    create("release") {
        storeFile = file("../creds/key_store_friendly")
        storePassword = System.getenv("KEYSTORE_PASSWORD") ?: ""
        keyAlias = "Key0"
        keyPassword = System.getenv("KEYSTORE_PASSWORD") ?: ""
    }
}
```

`creds/` is in `.gitignore`; the keystore file is materialized by CI at
`creds/key_store_<product>` before the build runs. Don't use this pattern
for new platforms — Pattern A is cleaner and doesn't pollute the working
tree.

## Bundle / package convention

| Field | Value |
|---|---|
| Kotlin namespace | `com.tanvrit.<product>` |
| `applicationId` | `com.tanvrit.<product>` (matches namespace; identical Play Store package id) |
| `packageName` in release-android.yml caller | `com.tanvrit.<product>` |
| `versionCode` | integer monotonically increasing, ≥ 10000 (leave room for hotfix versions) |
| `versionName` | semver string from `gradle.properties` |

Legacy platforms (friendly, school) use `com.friendly` / `com.school` —
forward-only means we leave them as-is; new platforms use the
`com.tanvrit.<product>` form.

## Org-level secrets

| Secret | What |
|---|---|
| `ANDROID_SIGNING_KEYSTORE` | Base64-encoded `.jks` keystore file |
| `ANDROID_SIGNING_KEYSTORE_PASSWORD` | The keystore password |
| `ANDROID_SIGNING_KEY_ALIAS` | The key alias inside the keystore (default `Key0`) |
| `ANDROID_SIGNING_KEY_PASSWORD` | The key password (often same as keystore password) |
| `PLAY_STORE_SERVICE_ACCOUNT_JSON` | Google Cloud Service Account JSON with Play publishing role; optional. When present, the template uploads the AAB to Play's internal track via `r0adkll/upload-google-play@v1`. |

### Generating a keystore

One-time:

```bash
keytool -genkey -v -keystore tanvrit-release.jks \
  -keyalg RSA -keysize 2048 -validity 25000 \
  -alias Key0 \
  -dname "CN=Tanvrit, O=Tanvrit Pvt. Ltd., L=Bengaluru, S=Karnataka, C=IN"

base64 -i tanvrit-release.jks -o tanvrit-release.jks.b64

gh secret set ANDROID_SIGNING_KEYSTORE --org Tanvrit < tanvrit-release.jks.b64
gh secret set ANDROID_SIGNING_KEYSTORE_PASSWORD --org Tanvrit --body '<your-password>'
gh secret set ANDROID_SIGNING_KEY_ALIAS --org Tanvrit --body 'Key0'
gh secret set ANDROID_SIGNING_KEY_PASSWORD --org Tanvrit --body '<your-password>'
```

**Never lose the keystore** — Play Store binds the keystore to your app
forever. Lose the keystore, lose the ability to ship updates. Back up
`tanvrit-release.jks` (NOT to git — to an encrypted offline vault).

## Play Store wiring

After generating the keystore and uploading the first AAB to the Play
Console manually (one-time), subsequent automated uploads need a Google
Cloud service account:

1. Create a project at console.cloud.google.com.
2. Enable the Android Publisher API.
3. IAM → Create service account → grant role "Service Account User" + link
   the SA to your Play Console under Users & Permissions with publishing role.
4. Download the SA JSON.
5. `gh secret set PLAY_STORE_SERVICE_ACCOUNT_JSON --org Tanvrit < sa.json`

The template's Play upload step is gated on this secret's presence — without
it, the workflow stops after producing the APK + AAB and uploading to
GitHub Release.

## Build pipeline (what the template does)

1. **Guard** — `androidTarget()` + `android.application` plugin check.
2. **Setup** — Java 21, Gradle, Android SDK auto-installed by AGP.
3. **Keystore decode** (if `ANDROID_SIGNING_KEYSTORE` set) — writes the
   base64-decoded `.jks` to a temp path, exports `ANDROID_KEYSTORE_PATH`
   env var.
4. **Build** — `./gradlew :<module>:assembleRelease :<module>:bundleRelease`.
   Produces unsigned APK + AAB if no keystore; signed if keystore present.
5. **Rename + checksum** — `<product>-<version>-android.apk` +
   `<product>-<version>-android.aab` + their `.sha256` files.
6. **GitHub Release upload** — `softprops/action-gh-release@v2`.
7. **R2 mirror** (if `R2_ACCESS_KEY_ID` set).
8. **Play Store upload** (if `PLAY_STORE_SERVICE_ACCOUNT_JSON` set) —
   `r0adkll/upload-google-play@v1` to the `internal` track. Promote to
   `production` manually via Play Console after smoke-testing.
9. **Manifest dispatch** — `platform: android`.

## Icon requirements

Android uses density-bucketed mipmaps under `composeApp/src/androidMain/res/`:

| Density | Folder | Min size |
|---|---|---|
| mdpi | `mipmap-mdpi/` | 48×48 |
| hdpi | `mipmap-hdpi/` | 72×72 |
| xhdpi | `mipmap-xhdpi/` | 96×96 |
| xxhdpi | `mipmap-xxhdpi/` | 144×144 |
| xxxhdpi | `mipmap-xxxhdpi/` | 192×192 |

Each density needs `ic_launcher.png` (square) and `ic_launcher_round.png`
(circular). Adaptive icon XML at `mipmap-anydpi-v26/ic_launcher.xml`
references separate foreground + background layers.

The `tools/generate-platform-icons.js` script (deferred to §3.3) will
produce all of these from a single 1024×1024 master.

## Troubleshooting

**`./gradlew :composeApp:assembleRelease` fails with "Could not find
androidx.activity:activity-compose"**

Means `androidMain.dependencies { implementation(libs.androidx.activity.compose) }`
is missing or the `androidx-activity-compose` libs.versions.toml entry
doesn't exist.

**Build succeeds but APK is unsigned (Play upload fails)**

`ANDROID_SIGNING_KEYSTORE` secret isn't set. Check:
```bash
gh secret list --org Tanvrit | grep ANDROID
```

**Different signing identity than the previous release**

You've changed your keystore. Play Store will refuse the upload because
the new APK is signed with a different identity than the prior production
APK. You can only fix this by recovering the original keystore or by
asking Google to reset the app signing key (a multi-week process). Avoid
by NEVER losing the keystore.

**Play upload fails with "Track 'internal' not found"**

The `play_track` input (default `internal`) doesn't exist in Play Console.
You probably haven't manually uploaded a first build via the Play Console
UI yet — that creates the internal track. Do that once, then automated
uploads work.

**`R8 reduced` aggressive minification breaks reflection-heavy code**

Set `isMinifyEnabled = false` in `buildTypes.release` (the default in our
scaffold). Proper R8 rules can be added per-product when shipping moves
beyond stubs.

## v2 backlog

- ABI splits (per-CPU APKs) for smaller download sizes.
- App Bundle (.aab) is already produced; Play Store Dynamic Delivery
  hooks (on-demand feature modules) deferred.
- Proper R8 / ProGuard rules per product.
- Per-product Firebase service account secrets for FCM push setup.
- Adaptive icon foreground+background SVGs in `tools/generate-platform-icons.js`.
