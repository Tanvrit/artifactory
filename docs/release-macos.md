# release-macos.md — macOS .dmg pipeline (arm64 + x64)

Caller: `<platform>/.github/workflows/release-macos.yml`
Template: `tanvrit/artifactory/.github/workflows/release-macos-template.yml`
Runners: `macos-14` (arm64) + `macos-13` (x64) — two parallel jobs
Output: `<product>-<version>-macos-arm64.dmg` + `-macos-x64.dmg` + their `.sha256` files

## Why two jobs instead of one universal binary

A single universal DMG would require `lipo`-ing arm64 + x64 binaries
together, which doubles the file size and complicates Compose Multiplatform
build settings. Apple Silicon Macs run x64 DMGs through Rosetta cleanly,
and Intel Macs are EOL — most users have arm64 hardware. Two separate
DMGs lets users download exactly what their machine runs. Storage cost
is bounded (~2× single-arch).

`control/app/` is an exception — it produces a `macos-universal.dmg` via
a custom `lipo` step because its operators run a mixed fleet.

## Source-side requirements

The template's graceful-skip guard requires:

1. **`jvm()`** or **`jvm("desktop")`** target declared in the caller's
   `composeApp/build.gradle.kts` or `app/build.gradle.kts`.
2. **`compose.desktop { application { … } }`** block with at least:
   - `mainClass = "<package>.MainKt"`
   - `nativeDistributions { targetFormats(TargetFormat.Dmg) }`
   - `macOS { bundleID = "com.tanvrit.<product>"; iconFile.set(file("…")) }`

## Minimal macOS scaffold

If your platform doesn't yet have desktop targets:

### `composeApp/build.gradle.kts` additions

```kotlin
import org.jetbrains.compose.desktop.application.dsl.TargetFormat

kotlin {
    jvm()  // or jvm("desktop") if you have multiple JVM targets
}

compose.desktop {
    application {
        mainClass = "com.tanvrit.<product>.MainKt"
        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Deb)
            packageName = "com.tanvrit.<product>"
            packageVersion = rootProject.findProperty("VERSION_NAME") as String? ?: "1.0.0"
            description = "<Your Product Description>"
            vendor = "Tanvrit"
            copyright = "© 2026 Tanvrit"
            macOS {
                bundleID = "com.tanvrit.<product>"
                iconFile.set(project.file("icons/<product>.icns"))
                dmgPackageVersion = rootProject.findProperty("VERSION_NAME") as String? ?: "1.0.0"
            }
        }
    }
}
```

### Desktop entry point

`composeApp/src/jvmMain/kotlin/com/tanvrit/<product>/Main.kt`:

```kotlin
package com.tanvrit.<product>

import androidx.compose.ui.window.Window
import androidx.compose.ui.window.application

fun main() = application {
    Window(onCloseRequest = ::exitApplication, title = "<Your Product>") {
        App()
    }
}
```

## Code signing + notarization

DMG distribution on macOS requires two separate Apple processes:

1. **Code signing** — proves the binary was built by a known developer
   (your Developer ID Application cert). Without it, Gatekeeper blocks
   the app on launch.
2. **Notarization** — Apple's malware scan. Without it, Gatekeeper shows
   a more aggressive warning even for signed apps.

Both happen in the template, gated on org secrets.

### Apple Developer Portal one-time setup

1. **Developer ID Application certificate** — Apple Developer →
   Certificates, Identifiers & Profiles → Certificates → "+" → Developer
   ID Application. Generate a CSR via Keychain Access → Certificate
   Assistant → Request a Certificate From a CA. Upload the CSR, download
   the resulting `.cer`, double-click to install in Keychain, then export
   from Keychain as `.p12` (with private key).
2. **App-specific password** — appleid.apple.com → Sign-In and Security
   → App-Specific Passwords → Generate. Used by `notarytool` instead of
   your real Apple ID password.
3. **Team ID** — Apple Developer → Membership. 10-character string.

### Org-level secrets

| Secret | Value |
|---|---|
| `APPLE_CERTIFICATE` | base64-encoded `.p12` (`base64 -i cert.p12`) |
| `APPLE_CERTIFICATE_PASSWORD` | The `.p12` export password |
| `APPLE_NOTARIZATION_APPLE_ID` | The Apple ID email (e.g. `you@tanvrit.com`) |
| `APPLE_NOTARIZATION_PASSWORD` | The app-specific password generated above |
| `APPLE_TEAM_ID` | Your 10-char Team ID |

These are inherited via `secrets: inherit` and shared with `release-ios.yml`
which uses some of the same secrets for `.ipa` signing.

## Build pipeline (what the template does)

For each architecture (arm64 on macos-14, x64 on macos-13):

1. **Guard** — `jvm()` declaration check in build.gradle.kts.
2. **Setup** — Java 21, Gradle.
3. **Cert import** (if `APPLE_CERTIFICATE` set) — decodes `.p12` into a
   temp keychain. Same one-way pattern as iOS template.
4. **Build** — `./gradlew :<module>:packageDmg` with `SIGNING_IDENTITY` env
   var set to the cert's identity. Compose Desktop's
   `nativeDistributions` invokes `codesign` automatically when this env
   var is present.
5. **Notarization** (if `APPLE_NOTARIZATION_PASSWORD` set) — runs
   `xcrun notarytool submit <dmg> --apple-id … --password … --team-id …
   --wait`. Then `xcrun stapler staple <dmg>` to embed the notarization
   ticket in the DMG.
6. **Rename + checksum** — `<product>-<version>-macos-<arch>.dmg` + `.sha256`.
7. **Keychain cleanup** — `if: always()`.
8. **GitHub Release upload** — with `update-release-if-exists`.
9. **R2 mirror** — if `R2_ACCESS_KEY_ID` set.
10. **Manifest dispatch** — `platform: macos-arm64` or `macos-x64`.

## Icon requirements

macOS uses an `.icns` bundle containing multiple sizes:
- 16, 32, 64, 128, 256, 512, 1024 pixels — each in @1x and @2x variants.

Generate from a 1024×1024 PNG master via:

```bash
# Create iconset directory
mkdir <product>.iconset
sips -z 16 16     master.png --out <product>.iconset/icon_16x16.png
sips -z 32 32     master.png --out <product>.iconset/icon_16x16@2x.png
sips -z 32 32     master.png --out <product>.iconset/icon_32x32.png
sips -z 64 64     master.png --out <product>.iconset/icon_32x32@2x.png
sips -z 128 128   master.png --out <product>.iconset/icon_128x128.png
sips -z 256 256   master.png --out <product>.iconset/icon_128x128@2x.png
sips -z 256 256   master.png --out <product>.iconset/icon_256x256.png
sips -z 512 512   master.png --out <product>.iconset/icon_256x256@2x.png
sips -z 512 512   master.png --out <product>.iconset/icon_512x512.png
cp master.png     <product>.iconset/icon_512x512@2x.png

# Convert to .icns
iconutil -c icns <product>.iconset -o composeApp/icons/<product>.icns
```

The `tools/generate-platform-icons.js` script (deferred) automates this.

## Troubleshooting

**`packageDmg` succeeds but the DMG isn't signed**

`SIGNING_IDENTITY` env var wasn't set, or didn't match an identity in the
temp keychain. Check the keychain import step in the workflow logs —
should report "1 identity found" matching `Developer ID Application: …`.

**Gatekeeper blocks the app with "damaged and can't be opened"**

The DMG isn't notarized. Check `xcrun notarytool history --apple-id … …`
to see the submission status. If "Invalid", run `xcrun notarytool log
<submission-id> …` to find the rejection reason (usually unsigned nested
binaries — common with Compose Desktop's bundled JRE).

**`xcrun notarytool submit` hangs**

Apple's notarization queue is sometimes slow. The `--wait` flag polls
indefinitely; you can `--wait` with a `--timeout 1800` ceiling. If it
times out, the submission usually completes within an hour and you can
staple later.

**DMG signs locally but fails in CI**

The `.p12` was exported without "Include private key" checked. Re-export
from Keychain Access with that option.

**Two parallel jobs (arm64 + x64) both finish but only one DMG appears
in the Release**

`softprops/action-gh-release@v2` with `update-release-if-exists: true`
appends, but race conditions when the Release doesn't yet exist can lose
the loser. The template's job dependencies don't strictly serialize; this
is a known minor risk. Mitigation: re-trigger via `workflow_dispatch` on
the failing arch (it appends fine on subsequent runs).

## v2 backlog

- macOS Catalyst (run iOS app on macOS via UIKit-on-AppKit) — separate
  pipeline if/when we want iPad-class macOS apps.
- Universal DMG (lipo of arm64+x64) for products with mixed-arch user bases.
- macOS sandboxing entitlements (App Store distribution requires this).
- Crash reporting via `os_log` + Sentry SDK.
