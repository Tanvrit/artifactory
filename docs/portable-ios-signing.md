# Portable certificate and API-key iOS signing

`release-ios-template.yml` supports an explicit `portable_signing: true` path.
The default remains `false`: existing login-keychain and Fastlane Match callers
retain their current behavior, even when portable signing secrets are inherited.

The path supports one application target and App Store distribution. Callers with
extensions must continue using the existing Match path. The caller's `bundle_id`
must match the effective Xcode build settings; signing never rewrites it.

## Required repository configuration

Pass `apple_team_id` from the public repository variable `APPLE_TEAM_ID`, validated
against the distribution certificate. The optional same-named secret, if present,
must agree. Configure these repository secrets without printing their values:

| Secret | Format |
| --- | --- |
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | Base64 P12 containing an Apple/iPhone Distribution certificate and its private key |
| `IOS_P12_PASSWORD` | P12 password |
| `APPSTORE_CONNECT_PRIVATE_KEY` | Raw PEM App Store Connect P-256 API private key |
| `APPSTORE_CONNECT_KEY_ID` | Ten-character API key ID |
| `APPSTORE_CONNECT_ISSUER_ID` | API issuer UUID |

The API key needs access to Certificates, Identifiers & Profiles. The imported
certificate must already be registered in the API key's Apple team; the action
never creates, replaces, or revokes signing certificates. It finds or registers
the exact app bundle ID, configures Push Notifications and Associated Domains
only when declared in that target's entitlements, then reuses a compatible active
App Store profile or creates an app-specific profile for the imported certificate.
These Apple-side identifiers, capabilities and reusable profiles persist.
Other declared capability types fail explicitly and need separate support.

Profiles from another app are never copied. The profile's certificate, team,
application identifier, distribution entitlements and expiry are validated before
manual archive/export. Existing legacy App ID prefixes may differ from Team ID.
The resulting IPA's bundle, team, entitlement flags, code signature and exact
leaf distribution certificate are checked before it becomes an output.

## Build and upload controls

| Input | Default | Effect |
| --- | --- | --- |
| `portable_signing` | `false` | Enables this certificate/API-key path |
| `portable_upload_testflight` | `false` | Explicitly uploads an internal-only TestFlight build; requires portable signing and an existing App Store Connect app record |
| `publish_artifacts` | `true` | Publishes to Artifactory's GitHub Release, R2 and release manifest; set `false` for signing validation |
| `firebase_messaging` | `false` | Requires validated per-app Firebase Messaging configuration |

Neither archive/export nor upload changes version or build numbers. Internal-only
TestFlight builds cannot later be distributed to external testers or the App
Store. Upload success is distinct from Apple processing, device delivery and
store publication. Since April 28, 2026, Apple requires Xcode 26 or later and the
iOS 26 SDK or later for App Store Connect uploads. See Apple's
[SDK minimum requirements](https://developer.apple.com/news/upcoming-requirements/).

For `firebase_messaging: true`, supply raw XML in `GOOGLE_SERVICE_INFO_PLIST`.
The caller must include `scripts/validate-firebase-config.py`, accepting
`--ios <path> --bundle-id <expected-id>`. The template writes
`iosApp/iosApp/GoogleService-Info.plist` with mode `0600`, refuses to overwrite
an existing file, validates without logging its contents, and removes the file in
an always-run cleanup step. The app's Xcode resource list must include this file.

## Credential handling and verification

The action creates a private temporary directory/keychain and serializes its
search-list changes for runner agents sharing one macOS user. It preserves other
keychains and existing identical profile files, removes only profile files it
installed, deletes its temporary keychain, and removes P8/P12 material when its
process exits. SIGTERM/SIGINT trigger cleanup; forced host termination/SIGKILL
cannot guarantee process cleanup. No credentials are written into the runner's
shared App Store Connect private-key directory. Signing secrets are removed from
the environment passed to build scripts. Diagnostics are captured; failures emit
only fixed stage/status messages. Security's import command necessarily receives
the P12 password as an argument, without printing it.

Local contract checks require no Apple credentials:

```sh
python3 -B -m unittest discover -s .github/actions/portable-ios-signing -p 'test_*.py' -v
actionlint .github/workflows/release-ios-template.yml .github/workflows/selftest-portable-ios-signing.yml
```

The self-test workflow runs the synthetic contracts. These checks cover API/profile
selection, signing arguments, identity rejection and cleanup; they do not prove a
real app archives, uploads, receives push notifications or passes store review.
Complete a signing validation build with `publish_artifacts: false` and
`portable_upload_testflight: false` before asserting those credentials build an IPA.

Apple API references: [certificates](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-certificates),
[bundle-specific profiles](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-bundleids-_id_-profiles),
[profile creation](https://developer.apple.com/documentation/appstoreconnectapi/post-v1-profiles),
[capability configuration](https://developer.apple.com/documentation/appstoreconnectapi/post-v1-bundleidcapabilities).
