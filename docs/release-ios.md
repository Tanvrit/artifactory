# release-ios.md — iOS .ipa pipeline

Caller: `<platform>/.github/workflows/release-ios.yml`
Template: `tanvrit/artifactory/.github/workflows/release-ios-template.yml`
Runner: `macos-14`
Output: `<product>-<version>-ios.ipa` + `.sha256`

## Source-side requirements

The graceful-skip guard in the template requires both:

1. **`iosArm64()`** (and ideally `iosSimulatorArm64()`) declared in the
   caller's `composeApp/build.gradle.kts` or `app/build.gradle.kts`. The
   framework block names the binary `ComposeApp` (kept consistent across
   all platforms so Swift `import ComposeApp` is portable):
   ```kotlin
   listOf(iosArm64(), iosSimulatorArm64()).forEach { iosTarget ->
       iosTarget.binaries.framework {
           baseName = "ComposeApp"
           isStatic = true
       }
   }
   ```
2. **`iosApp/iosApp.xcodeproj`** Xcode project at the caller's repo root.
   This is the SwiftUI host app that wraps the Kotlin/Native framework.

If either is missing, the template exits 0 with a `::notice::skipped: …`.
No false-positive failure.

## Scaffolding a new iOS app from scratch

Cleanest path: clone an existing platform's `iosApp/` and retarget. Friendly
is the canonical reference (`platforms/friendly/iosApp/`). The scaffold is
small — 11 files, ~500 LOC.

### Steps

```bash
# 1. Clone friendly's scaffold into your platform
cp -r platforms/friendly/iosApp platforms/<your-product>/iosApp

# 2. Drop user-specific dirs that don't belong in git
rm -rf platforms/<your-product>/iosApp/iosApp.xcodeproj/xcuserdata
rm -rf platforms/<your-product>/iosApp/iosApp.xcodeproj/project.xcworkspace/xcuserdata
rm -rf platforms/<your-product>/iosApp/build
```

### Files to edit

| File | What to change |
|---|---|
| `iosApp/Configuration/Config.xcconfig` | `PRODUCT_NAME=<Your Product>`, `PRODUCT_BUNDLE_IDENTIFIER=com.tanvrit.<product>$(TEAM_ID)` |
| `iosApp/iosApp.xcodeproj/project.pbxproj` | Replace all `Friendly.app` → `<Your>.app` (3 occurrences typically). |
| `iosApp/iosApp/ContentView.swift` | Replace `MainViewControllerKt.MainViewController()` with whatever your KMP iOS entry function generates. See §"Swift bridge naming" below. |
| `iosApp/iosApp/Info.plist` | No change needed for stub apps; add NSUsageDescription strings for permissions later. |
| `iosApp/iosApp/Assets.xcassets/AppIcon.appiconset/app-icon-1024.png` | Replace with your product's 1024×1024 PNG. See §"Icon requirements". |

### app/ shape platforms (school, admin, control)

Some platforms use `app/` instead of `composeApp/` (historical naming). For
these, the pbxproj's `Compile Kotlin Framework` shellScript also needs an
edit at line ~155:

```diff
-./gradlew :composeApp:embedAndSignAppleFrameworkForXcode
+./gradlew :app:embedAndSignAppleFrameworkForXcode
```

And the caller workflow's input:

```diff
-gradle_module: composeApp
+gradle_module: app
```

## Swift bridge naming

The Swift code in `ContentView.swift` calls one Kotlin-generated function.
The exact name depends on **the Kotlin file name** and **the function name**:

| Kotlin file | Kotlin function | Swift call |
|---|---|---|
| `MainViewController.kt` | `fun MainViewController()` | `MainViewControllerKt.MainViewController()` |
| `MainViewController.kt` | `fun mainViewController()` | `MainViewControllerKt.mainViewController()` |
| `main.kt` | `fun mainViewController()` | `MainKt.mainViewController()` |

Convention is `MainViewController.kt` + `fun MainViewController()` (uppercase
both). Older platforms use lowercase — leave them in place rather than mass-
rename (every dependent file would need to update).

## KMP iOS entry point

The Kotlin side lives at `composeApp/src/iosMain/kotlin/<package>/MainViewController.kt`:

```kotlin
package com.tanvrit.<product>

import androidx.compose.ui.window.ComposeUIViewController
import com.tanvrit.core.app.AppStartupConfig
import com.tanvrit.core.app.PlatformType
import com.tanvrit.core.constant.Environment
import com.tanvrit.core.storage.DatabaseConfig

fun MainViewController() = ComposeUIViewController(
    configure = {
        init<Product>()
        val config = AppStartupConfig(
            appName = "<Product>",
            tagline = "<your tagline>",
            platformType = PlatformType.Mobile,
            environment = Environment.prod,
            databaseConfig = DatabaseConfig("<product>.db", 1),
            serverUrl = "https://api.tanvrit.com",
        )
        initTanvritSdk(config)
    },
) {
    <Product>App()
}
```

For **stub apps** (no real iOS feature work yet), keep it minimal — pure
Compose imports, no SDK init:

```kotlin
package com.tanvrit.<product>

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.ComposeUIViewController

fun MainViewController() = ComposeUIViewController {
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("<Product>", style = MaterialTheme.typography.headlineMedium)
            Spacer(Modifier.height(8.dp))
            Text("Mobile companion — coming soon", style = MaterialTheme.typography.bodyLarge)
        }
    }
}
```

The stub form is what automator uses (`com.tanvrit.automator.MainViewController`).
It avoids pulling in the SDK / Koin / SQLDelight init paths that may not have
production-ready iOS implementations yet.

## iOS source set dependencies

For most platforms, `iosMain.dependencies` block adds the Darwin engines:

```kotlin
sourceSets {
    val iosMain by creating {
        dependsOn(commonMain.get())
        dependencies {
            implementation(libs.ktor.client.darwin)
            implementation(libs.sqldelight.native.driver)
        }
    }
    val iosArm64Main by getting { dependsOn(iosMain) }
    val iosSimulatorArm64Main by getting { dependsOn(iosMain) }
}
```

Why an intermediate `iosMain by creating` instead of `iosMain.dependencies`?
Compose Multiplatform / KMP doesn't ship an `iosMain` source set by default
when you only declare `iosArm64() + iosSimulatorArm64()` (no umbrella `ios()`).
The intermediate set is how you share Darwin code between the two leaf
source sets.

## Bundle ID convention

`com.tanvrit.<product>`. Examples in this repo:

- `com.tanvrit.friendly`
- `com.tanvrit.swyft`
- `com.tanvrit.wedding`
- `com.tanvrit.school`
- `com.tanvrit.admin`
- `com.tanvrit.automator`

The Xcode project's default bundle id in `Config.xcconfig` includes a
`$(TEAM_ID)` suffix — handy for differentiating developer builds (where
TEAM_ID resolves to your Apple Developer team ID) from the canonical CI
build (where TEAM_ID is empty so the bundle id stays clean).

At CI release time, the template's exportOptions.plist stamps the canonical
`com.tanvrit.<product>` (passed via the `bundle_id` input on the caller
workflow). So even if your local dev build has team-suffixed bundle, the
shipped `.ipa` always has the clean bundle id.

## Apple Developer Portal setup

One-time per product, before the first real release tag:

1. **Bundle ID registration**: Apple Developer → Certificates, Identifiers
   & Profiles → Identifiers → "+" → App. Enter `com.tanvrit.<product>`.
2. **Capabilities**: enable only what your app needs (push notifications,
   sign-in with Apple, in-app purchase, etc.).
3. **App Store Connect record**: required before TestFlight upload. Create
   under "My Apps" with the matching bundle id.
4. **Provisioning profile**: Apple Developer → Profiles → "+". Choose
   "App Store" distribution, the bundle id above, your Distribution cert,
   and download the `.mobileprovision`.
5. **Add the profile to the org**:
   ```bash
   gh secret set IOS_PROVISIONING_PROFILE_<PRODUCT_UPPER> \
     --repo Tanvrit/<product> < profile.mobileprovision.base64
   ```
   Per-product rather than org-wide because each profile is bundle-id-scoped.

## Org-level secrets

These ship signed builds for every product:

| Secret | Source |
|---|---|
| `IOS_SIGNING_CERT` | Apple Developer → Distribution cert → export as `.p12` → `base64 -i cert.p12 -o cert.b64` → upload contents |
| `IOS_SIGNING_CERT_PASSWORD` | The `.p12` export password |
| `APPLE_TEAM_ID` | Found in Apple Developer → Membership |
| `APP_STORE_CONNECT_API_KEY_ID` | App Store Connect → Users & Access → Keys → Create. The 10-char key ID. |
| `APP_STORE_CONNECT_API_ISSUER_ID` | Same page; the issuer UUID. |
| `APP_STORE_CONNECT_API_KEY_P8` | The `.p8` file's contents (NOT base64-encoded — paste raw). |

`APP_STORE_CONNECT_API_*` are required only for TestFlight upload (deferred
to v2). Until those land, the `.ipa` lands in GitHub Release + R2; you
Transporter it to TestFlight manually.

## Build pipeline (what the template does)

1. **Guard** — `iosArm64()` + `iosApp/iosApp.xcodeproj` check.
2. **Setup** — Java 21 + Gradle.
3. **Cert import** (if `IOS_SIGNING_CERT` set) — decodes `.p12`, creates
   temp keychain, imports cert with `apple-tool:apple:codesign:`
   partition list, installs `.mobileprovision` into
   `~/Library/MobileDevice/Provisioning Profiles/`.
4. **KMP framework build** — `./gradlew :<module>:linkPodReleaseFrameworkIosArm64`.
   Produces the `.framework` bundle that the Xcode project embeds.
5. **Xcode archive** — `xcodebuild archive -project iosApp/iosApp.xcodeproj
   -scheme iosApp -configuration Release -destination 'generic/platform=iOS'`.
6. **Export archive** — `xcodebuild -exportArchive` with an
   `exportOptions.plist` containing `method=app-store`, `teamID=<APPLE_TEAM_ID>`,
   `signingStyle=manual`. Produces the `.ipa`.
7. **Rename + checksum** — `<product>-<version>-ios.ipa` + `.sha256`.
8. **Keychain cleanup** — runs in `if: always()` so even on failure the
   temp keychain is deleted.
9. **GitHub Release upload** — `softprops/action-gh-release@v2` with
   `update-release-if-exists: true`. Appends to the tag's Release.
10. **R2 mirror** (if `R2_ACCESS_KEY_ID` set) — `aws s3 cp` to
    `s3://tanvrit-artifacts/releases/<product>/<version>/`.
11. **Manifest dispatch** — `repository_dispatch` to
    `tanvrit/artifactory` with `platform: ios` in the payload.

## Icon requirements

iOS App Store requires a 1024×1024 PNG with no transparency, no rounded
corners (the OS handles those), at
`iosApp/iosApp/Assets.xcassets/AppIcon.appiconset/app-icon-1024.png`.

The `Contents.json` next to it declares the image set; in our scaffold it's
a single 1024×1024 entry which Xcode auto-generates the smaller sizes from.

For local previewing, also fill out the other size slots if you want
non-blurry icons in TestFlight / older devices. The
`tools/generate-platform-icons.js` script (deferred to §3.3 of the master
plan) will produce the full set from a single SVG master.

## Troubleshooting

**`xcodebuild archive` fails with "No signing certificate"**

Means `IOS_SIGNING_CERT` wasn't decoded into the temp keychain. Check:
- The secret value is the BASE64-encoded `.p12`, not the raw `.p12`. Run
  `base64 -i cert.p12 -o cert.b64` and upload `cert.b64`'s contents.
- `IOS_SIGNING_CERT_PASSWORD` matches the password you set during export.
- `APPLE_TEAM_ID` is the 10-char team identifier, not the team name.

**`exportArchive` fails with "No profiles for 'com.tanvrit.X' were found"**

The provisioning profile in `IOS_PROVISIONING_PROFILE_<PRODUCT>` doesn't
match the bundle id you're shipping. Re-download the profile from Apple
Developer with the right bundle id and re-upload.

**Workflow runs but graceful-skips with "no iosArm64()"**

Check `composeApp/build.gradle.kts` (or `app/build.gradle.kts` for app-shape).
The declaration must be `iosArm64()` (with parens) — not just `ios()` (that's
removed in Kotlin 2.x) and not `iosArm64` (no parens, just a property
reference).

**Workflow graceful-skips with "iosApp/iosApp.xcodeproj not found"**

The Xcode project directory is missing. Either you haven't scaffolded it yet
(see §"Scaffolding a new iOS app from scratch") or the `xcodeproj_path` input
in your `release-ios.yml` caller points to the wrong location.

**Kotlin compile fails at `:composeApp:compileKotlinIosArm64` with
"unresolved reference: <Tanvrit SDK class>"**

The Tanvrit SDK version pulled in by `corePluginVersion` doesn't ship iOS
klibs. Confirm:
```bash
cd platforms/<your-product>
./gradlew dependencies --configuration iosArm64MainCompilationApi | grep tanvrit
```
SDK 2.0.8 has iOS klibs; older versions may not.

## v2 backlog (not yet built)

- TestFlight upload via `xcrun altool --upload-app` once
  `APP_STORE_CONNECT_API_KEY_P8` lands org-wide.
- `release_notes` + `release_notes_hi` inputs flow through to the
  TestFlight build metadata.
- App Store Connect automated review submission via the
  `App Store Connect API`.
- `iosX64()` (Intel Mac simulator) revival if Apple un-deprecates the
  Compose Multiplatform target.
