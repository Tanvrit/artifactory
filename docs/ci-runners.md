# CI runner routing

Portable CI uses the `tanvrit` runner group with `[self-hosted, macOS, ARM64]`
from 2026-10-19. This replaces the older Linux routing notes in product docs.
The shared Android/web templates and the Jashva version resolver use this default.
Existing caller overrides remain self-hosted and must match the actual toolchain.

## Runner preparation

Register an Apple Silicon macOS runner in the `tanvrit` group and grant the calling
repositories access to that group. The default GitHub runner labels must include
`self-hosted`, `macOS`, and `ARM64`. Public repositories also require group access.
A workflow change cannot make an offline or inaccessible runner accept jobs.

- Install Xcode/Command Line Tools and Homebrew for the runner user. Each workflow
  continues to select its own JDK, Node and Python versions.
- `.github/actions/macos-ci-tools` provisions missing GNU command tools, Bash, jq
  and OpenSSL through Homebrew, and exports their paths. Existing packages are reused.
  Concurrent registrations retry explicit Homebrew busy-lock errors; other install
  failures stop the job immediately.
- Docker jobs require Docker Desktop, Colima, or a configured Docker context with a
  running Linux-container engine. The action fails before a build if only the CLI exists.
- Compose UI tests require the runner user to own an active console login. Start
  the runner as that user's LaunchAgent and keep the login session active. A system
  LaunchDaemon does not supply a native window server. These tests run directly on
  macOS; Linux overrides retain Xvfb where applicable.
- `.github/actions/android-sdk` reads compileSdk from the catalog or tracked module
  declarations. The SHA-pinned setup action selects the host OS command-line tools
  archive. API37 uses `platforms;android-37.0`; an existing platform is reused.
  Android SDK paths are exported through Actions environment files.
- The existing `gradle-home` action continues to isolate caches by runner and trust
  level. Credentials, release signing, deployment gates and artifact versions are unchanged.

## Native and host-specific jobs

These jobs retain their actual target OS or explicitly selected host. They are not
silently skipped or relabelled as macOS builds:

| Repository / workflow / job | Reason |
| --- | --- |
| `platforms/artifactory / release-linux-template.yml / build-linux` | Native Linux DEB/AppImage packaging |
| `platforms/artifactory / release-windows-template.yml / build-windows` | Native Windows MSI/WiX packaging |
| `platforms/compute / agent-release.yml / build` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |
| `platforms/compute / release-desktop.yml / build` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |
| `platforms/ai / model-download-gate.yml / download-gate` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |
| `platforms/ai / release-linux.yml / build-and-publish` | Native Linux x64/arm64 packaging matrix |
| `platforms/ai / release-macos.yml / build-and-publish` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |
| `platforms/ai / release-windows.yml / build-and-publish` | Native Windows MSI and bundled Windows runtime |
| `platforms/ai / training-smoke-gate.yml / smoke` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |
| `server / fleet-control.yml / host` | Explicit operator-selected deployment host |
| `server / runner-health.yml / ping-mac-a` | Specific Mac host identity health check |
| `server / runner-health.yml / ping-mac-b` | Specific Mac host identity health check |
| `server / runner-health.yml / fleet-audit` | Explicit operator-selected host health matrix |
| `sdk / runner-health.yml / ping` | Measures the existing Linux X64 host disk, not generic CI |
| `sdk / runner-reclaim.yml / reclaim` | Linux host-specific disk maintenance; never run its /proc and root filesystem cleanup on a Mac |
| `additional/smritidb / release.yml / python-wheels` | Preserve target-specific native build/test matrix; Mac legs retain their native architecture |

## Validation and rollout

The shared preparation and compile SDK selection have executable fixture tests in
`.github/actions/`, exercised by `selftest-macos-ci.yml`. Workflow changes are checked
with YAML parsing, actionlint and the self-hosted runner guard. Publish Artifactory
first because callers consume its shared actions and reusable workflows at `main`.
Only then publish the caller workflow changes. Existing queued runs keep the labels
from their original commit; a new commit creates runs with the new selection.
