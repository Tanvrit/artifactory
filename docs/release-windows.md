# release-windows.md — Windows .msi pipeline

Caller: `<platform>/.github/workflows/release-windows.yml`
Template: `tanvrit/artifactory/.github/workflows/release-windows-template.yml`
Runner: `windows-latest` (x64)
Output: `<product>-<version>-windows-x64.msi` + `.sha256`

## Source-side requirements

Same as macOS: `jvm()` + `compose.desktop { application { ... } }` block
with at least `mainClass`, `nativeDistributions.targetFormats(TargetFormat.Msi)`,
and a `windows {}` sub-block.

### `windows {}` config

```kotlin
compose.desktop {
    application {
        nativeDistributions {
            windows {
                menuGroup = "Tanvrit"
                iconFile.set(project.file("icons/<product>.ico"))
                upgradeUuid = "<fresh-uuidgen>"
            }
        }
    }
}
```

`upgradeUuid` must be **unique per product** and **immutable once set**. It's
the GUID Windows uses to identify your app for upgrades — changing it later
makes Windows treat the next release as a separate app, leaving the old
version installed in parallel. Generate once via `uuidgen` and never change
it.

⚠️ Historical bug: admin's `app/build.gradle.kts` originally copied wedding's
upgradeUuid verbatim (`E5F6A7B8-C9D0-1234-EF01-234567890004`). The master
plan §15 has a TODO to regenerate admin's. Don't propagate that pattern.

## Icon requirements

Windows uses an `.ico` file containing multiple sizes:
- 16, 32, 48, 64, 128, 256 pixels in one bundle.

Generate from a 1024×1024 PNG master via ImageMagick:

```bash
magick convert master.png \
  -define icon:auto-resize=256,128,64,48,32,16 \
  composeApp/icons/<product>.ico
```

If you don't have ImageMagick on macOS, install via Homebrew:
`brew install imagemagick`. On Windows/Linux,
`apt install imagemagick` / `winget install ImageMagick.ImageMagick`.

## Authenticode code signing

Currently **scaffolded but not active** in the template — the signing step
is gated on `WINDOWS_SIGNING_CERT` org secret which isn't yet provisioned.
Without signing, the MSI installs fine but shows the "Unknown publisher"
Windows SmartScreen warning on first launch.

To activate (v2 work):

1. Acquire a code-signing cert from DigiCert, Sectigo, or another CA.
   Standard certs cost ~$200/year and require company verification (D-U-N-S
   number or alternative). EV certs (~$300+/year) eliminate the SmartScreen
   warning entirely.
2. Export the cert as `.pfx` (Windows format) with the private key.
3. `base64 -i cert.pfx -o cert.pfx.b64`.
4. `gh secret set WINDOWS_SIGNING_CERT --org Tanvrit < cert.pfx.b64`.
5. `gh secret set WINDOWS_SIGNING_CERT_PASSWORD --org Tanvrit --body '<password>'`.

The template's signing step calls `signtool sign /f cert.pfx /p <password>
/tr http://timestamp.digicert.com /td sha256 /fd sha256 <msi>`.

Timestamping is required — without it, the cert's signature expires when
the cert itself expires (vs. timestamped signatures stay valid forever).

## Build pipeline (what the template does)

1. **Guard** — `jvm()` declaration check.
2. **Setup** — Java 21 (Temurin), Gradle.
3. **Build** — `./gradlew :<module>:packageMsi`. Compose Desktop's
   `nativeDistributions` invokes `jpackage` to produce a Windows MSI
   bundling a custom JRE.
4. **Sign** (if `WINDOWS_SIGNING_CERT` set) — `signtool` with the cert.
5. **Rename + checksum** — `<product>-<version>-windows-x64.msi` + `.sha256`.
6. **GitHub Release upload** — with `update-release-if-exists`.
7. **R2 mirror** — if `R2_ACCESS_KEY_ID` set.
8. **Manifest dispatch** — `platform: windows-x64`.

## Troubleshooting

**`packageMsi` fails with "Could not locate jpackage"**

The runner's Java distribution doesn't include `jpackage`. Use Temurin
(Adoptium) JDK 21 — it ships with `jpackage`. Some older OpenJDK builds
don't.

**MSI installs but the app immediately crashes**

Compose Desktop bundles a JRE inside the MSI. If `jpackage` truncated the
JRE (a known AGP bug with `isMinifyEnabled = true`), the runtime is
incomplete. Set `buildTypes.release { isMinifyEnabled = false }` in
`compose.desktop.application` — minification is for app code, not the
bundled JRE.

**Upgrades don't replace the old version (two copies in Programs &
Features)**

Different `upgradeUuid` between versions. Check `compose.desktop {
windows.upgradeUuid }` — must be byte-identical between releases.

**MSI is 200+ MB**

Normal — Compose Desktop bundles a complete JRE (~140 MB) + your app + native
libs. Use the AppImage on Linux for a smaller alternative, or
`packageDistributionForCurrentOS` to ship a folder bundle instead of MSI.

## v2 backlog

- Authenticode signing activation when `WINDOWS_SIGNING_CERT` is provisioned.
- Windows on ARM (winarm64) — Compose Desktop supports it in alpha; add
  a `windows-arm64` slot once that stabilizes.
- AppX / MSIX packaging for Microsoft Store distribution (separate
  pipeline if/when we want Store).
- Windows installer language packs (currently English only).
