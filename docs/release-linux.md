# release-linux.md — Linux .deb + .AppImage pipeline

Caller: `<platform>/.github/workflows/release-linux.yml`
Template: `tanvrit/artifactory/.github/workflows/release-linux-template.yml`
Runner: `ubuntu-latest`
Outputs:
- `<product>-<version>-linux-x64.deb` (Debian/Ubuntu install)
- `<product>-<version>-linux-x64.AppImage` (distro-agnostic single-file run)

## Why both .deb and .AppImage

| Format | Pros | Cons |
|---|---|---|
| **.deb** | Native package manager integration on Debian/Ubuntu; auto-launch on install; updates via apt | Locked to Debian-family; manual install on Fedora/Arch |
| **.AppImage** | Runs on any Linux distro; no install required; self-contained | No menu integration by default; user must `chmod +x` |

Most platforms ship both. The manifest tracks them as separate platform
keys: `linux-x64` (deb) and `linux-x64-app` (AppImage).

## Source-side requirements

Same as macOS/Windows: `jvm()` + `compose.desktop { application { ... } }`
with `targetFormats(TargetFormat.Deb)` plus a `linux {}` sub-block:

```kotlin
compose.desktop {
    application {
        nativeDistributions {
            targetFormats(TargetFormat.Dmg, TargetFormat.Msi, TargetFormat.Deb)
            linux {
                packageName = "tanvrit-<product>"  // dpkg-friendly slug; lowercase, hyphens
                iconFile.set(project.file("icons/<product>-256.png"))
                debMaintainer = "team@tanvrit.com"
            }
        }
    }
}
```

`packageName` for Linux is `tanvrit-<product>` (hyphenated, lowercase) — dpkg
rejects underscores and uppercase. This differs from the macOS/Windows
`com.tanvrit.<product>` form.

## Icon requirements

Linux uses a 256×256 PNG (older distros use smaller; 256 is plenty for
modern displays). Located at `composeApp/icons/<product>-256.png`.

For AppImage, the icon is auto-extracted from `composeApp/icons/<product>-256.png`
into the `.AppImage` payload's `.DirIcon`. No extra step.

## Build pipeline (what the template does)

1. **Guard** — `jvm()` declaration check.
2. **Setup** — Java 21, Gradle.
3. **Build DEB** — `./gradlew :<module>:packageDeb`. Compose Desktop's
   `nativeDistributions` runs `jpackage --type deb`.
4. **Build AppImage** — wraps Compose Desktop's
   `:<module>:createDistributable` output via `appimagetool`:
   ```bash
   appimagetool composeApp/build/compose/binaries/main/app/<product>/
   ```
   This bundles the same JRE + app structure as the DEB but as a
   self-contained AppImage file.
5. **Rename + checksum**:
   - `<product>-<version>-linux-x64.deb` + `.sha256`
   - `<product>-<version>-linux-x64.AppImage` + `.sha256`
6. **GitHub Release upload** — both files appended via
   `update-release-if-exists`.
7. **R2 mirror** — if `R2_ACCESS_KEY_ID` set.
8. **Manifest dispatch** — two separate dispatches:
   - `platform: linux-x64` (for the DEB)
   - `platform: linux-x64-app` (for the AppImage)

## Troubleshooting

**`packageDeb` fails with "dpkg-deb: error: parsing file 'control'"**

`linux.packageName` contains an underscore or uppercase. Use lowercase
hyphens only: `tanvrit-<product>`.

**AppImage runs but shows no icon in the launcher**

The runner stripped `.DirIcon` from the AppImage. Verify
`composeApp/icons/<product>-256.png` exists and is referenced in the
linux config; appimagetool reads from `<app>/build/compose/binaries/main/app/<product>/lib/<product>.png`.

**`.deb` installs but the launcher doesn't appear**

`jpackage` writes a `.desktop` entry at `/usr/share/applications/<product>.desktop`.
On Ubuntu, run `update-desktop-database` (or log out/in) to pick it up.

**AppImage fails to launch with "FUSE not available"**

Older glibc / no FUSE2 driver. Modern AppImages use FUSE3 which is bundled.
Worst case: the user runs `<appimage> --appimage-extract-and-run` which
unpacks to a temp dir and runs directly.

**The .deb size is much larger than expected (200+ MB)**

Compose Desktop bundles a complete JRE. There's no compact equivalent
because Compose UI needs the full JFX-equivalent. This is the same overhead
as the Windows MSI; it's unavoidable for desktop-Compose distribution.

**AppImage isn't signed**

AppImage signing via libsign is on the v2 backlog. Currently AppImages
ship unsigned — users verify integrity via the `.sha256` checksum file.

## v2 backlog

- AppImage signing via libsign + `appimagetool --sign`.
- Snap packaging for Ubuntu Software Center distribution.
- Flatpak packaging for Fedora/Arch users (different sandbox model).
- Arm64 builds (linux-arm64) for Raspberry Pi / ARM servers.
- RPM packaging via `jpackage --type rpm` for Fedora/RHEL.
