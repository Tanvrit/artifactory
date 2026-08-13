# Branding Changelog

All notable brand and artifact changes are documented here.
Follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Release manifests advertised a download link that did not resolve.
  `scripts/update-manifest.js` wrote `r2_url` as
  `https://artifacts.tanvrit.com/releases/…`, a path the Worker has no route for
  (`releases` is not a `VALID_PRODUCT`), so it fell through to the portal proxy
  and returned the portal's Next.js 404 page instead of the installer. `r2_url`
  now points at `https://dl.tanvrit.com/releases/…` — the same Worker, on the
  hostname that maps the whole path to the R2 key the release templates already
  upload, with Range support. No Worker or workflow change, so no deploy is
  needed to activate it.
- AppImage entries pointed at a filename that is never built. The manifest key
  is `linux-x64-app` but the artifact is `<product>-<version>-linux-x64.AppImage`,
  and the filename was derived from the key — breaking both `direct_url` and
  `r2_url` for every AppImage. Added an explicit filename-stem override.

### Added
- Initial branding system setup
- Design token system (base, semantic, product tokens)
- Placeholder SVG icons for all 8 products
- Cloudflare Worker for artifacts.tanvrit.com
- GitHub Actions workflows for icon generation and manifest updates
- Release manifest JSON structure for all 8 products

## [1.0.0] — 2026-04-01

### Added
- Initial repository structure
- Tanvrit umbrella brand tokens (Forest Green, Saffron Gold)
- Product brand colors for all 8 products
- Typography tokens (Plus Jakarta Sans, Inter)
- Spacing and radius tokens
