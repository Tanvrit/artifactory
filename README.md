# Tanvrit Artifactory

Centralized artifact, branding, and release management for all Tanvrit products.

**Subdomain:** `artifacts.tanvrit.com`

## Products

| Product | Slug | Platform | Package |
|---------|------|----------|---------|
| Friendly | `friendly` | Android, iOS, macOS, Windows, Linux, Web | `com.friendly` |
| DesiPops | `desipops` | Android, iOS, macOS, Windows, Linux, Web | `com.desipops` |
| Mandee | `mandee` | Android, macOS, Windows, Linux, Web | `business.mandee.ai` |
| Swyft | `swyft` | Android, iOS, macOS, Windows, Linux, Web | `com.tanvrit.swyft` |
| Bharat Bandhu | `bharat-bandhu` | Android, iOS, Web | `com.tanvrit.bharat` |
| School | `school` | Android, iOS, macOS, Windows, Linux, Web | `com.school` |
| Wedding | `wedding` | Android, iOS, macOS, Web | `com.friendly.wedding` |
| Control | `control` | macOS | `com.tanvrit.control` |

## Repository Structure

```
artifactory/
├── manifests/          # Release version manifests (source of truth)
├── branding/           # Design tokens + icon sources + exports
│   ├── tokens/         # Design tokens (JSON → CSS/TS/Kotlin/Swift)
│   └── icons/          # Master SVGs + generated platform exports
├── press-kit/          # Brand guidelines, screenshots, press assets
├── worker/             # Cloudflare Worker for artifacts.tanvrit.com
└── scripts/            # Automation scripts (icon gen, token gen, etc.)
```

## Download URLs

```
# Latest version manifest
GET artifacts.tanvrit.com/{product}/latest.json

# All products catalog
GET artifacts.tanvrit.com/catalog.json

# Download latest build
GET artifacts.tanvrit.com/{product}/latest/macos-arm64
GET artifacts.tanvrit.com/{product}/latest/macos-x64
GET artifacts.tanvrit.com/{product}/latest/windows-x64
GET artifacts.tanvrit.com/{product}/latest/linux-x64

# Specific version
GET artifacts.tanvrit.com/{product}/{version}/macos-arm64

# Branding assets
GET artifacts.tanvrit.com/brand/{product}/icon.svg
GET artifacts.tanvrit.com/brand/{product}/icon-512.png
GET artifacts.tanvrit.com/brand/{product}/og-image.png
GET artifacts.tanvrit.com/brand/press-kit.zip
```

## GitHub Release Tag Convention

```
{product}-v{MAJOR}.{MINOR}.{PATCH}
```

Examples: `friendly-v1.2.0`, `control-v1.0.3`, `mandee-v2.1.0`

## Asset Naming Convention

```
{product}-{version}-{platform}-{arch}.{ext}
```

Examples:
- `friendly-1.2.0-macos-arm64.dmg`
- `friendly-1.2.0-windows-x64.msi`
- `friendly-1.2.0-linux-x64.deb`
- `control-1.0.3-macos-universal.dmg`

## Quick Links

- [Design Tokens](./branding/tokens/)
- [Icon Sources](./branding/icons/src/)
- [Cloudflare Worker](./worker/)
- [Release Manifests](./manifests/)
- [GitHub Actions](./.github/workflows/)
