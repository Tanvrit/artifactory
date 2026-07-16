# Signed Deployment — fleet runbook

Canonical reference for how every Tanvrit artifact gets **cryptographically signed** on release.
Full rollout plan: `~/.claude/plans/i-need-you-to-woolly-hamming.md`. This doc is the executable
runbook + status board.

## Model (locked)

- **Android:** one org **upload keystore** (Play App Signing re-signs per listing). Every app's Gradle
  `signingConfig` reads `RELEASE_STORE_FILE / RELEASE_STORE_PASSWORD / RELEASE_KEY_ALIAS /
  RELEASE_KEY_PASSWORD`; the shared `artifactory/release-android-template.yml` decodes the
  `ANDROID_SIGNING_KEYSTORE` org secret into those env vars.
- **iOS / macOS:** **portable Fastlane Match + GitHub-secret certs** (not runner login-keychain).
- **Supply chain:** Maven artifacts GPG-signed (sdk/core/agent), server/host containers cosign-signed
  (keyless OIDC) + SLSA provenance, web bundles SLSA-attested.

## Canonical org secret set

Provision once at the **org** level (`tanvrit`) so every repo inherits.

| Group | Secrets |
|---|---|
| android | `ANDROID_SIGNING_KEYSTORE` (base64 .jks), `ANDROID_SIGNING_KEYSTORE_PASSWORD`, `ANDROID_SIGNING_KEY_ALIAS`, `ANDROID_SIGNING_KEY_PASSWORD` |
| play | `PLAY_STORE_SERVICE_ACCOUNT_JSON` |
| apple | `ASC_API_KEY_P8` (base64 .p8), `ASC_API_KEY_ID`, `ASC_API_ISSUER_ID`, `APPLE_TEAM_ID`=`F846F22MFM` |
| match | `MATCH_GIT_URL`, `MATCH_PASSWORD`, `MATCH_GIT_BASIC_AUTHORIZATION` |
| macos | `APPLE_DEVELOPER_ID_CERT_P12` (base64), `APPLE_CERT_PASSWORD`, `MACOS_DEVELOPER_ID_APPLICATION` (identity string), `MACOS_NOTARIZATION_APPLE_ID`, `MACOS_NOTARIZATION_PASSWORD` |
| windows | `WINDOWS_SIGNING_CERT` (base64 .pfx), `WINDOWS_SIGNING_PASSWORD` |
| maven | `GPG_PRIVATE_KEY` (ascii-armored), `GPG_PASSPHRASE` |

Retired variants (do not reuse): `ANDROID_KEYSTORE_BASE64*` (ai), bare `KEYSTORE_PASSWORD/KEY_ALIAS/
KEY_PASSWORD` (store/mandee). Gradle keeps a temporary fallback to the legacy `ANDROID_KEYSTORE_*` /
`MACOS_SIGN_IDENTITY` names during migration.

## Owner runbook (provisioning — do once)

```bash
# 1. Generate the org Android upload keystore (RSA-2048, 25000-day). Prints SHA-1/256
#    to register under each app's Play Console → App integrity. BACK IT UP.
artifactory/scripts/set-org-signing-secrets.sh --gen-keystore

# 2. Collect Apple/Play/GPG material (or reuse platforms/ai/scripts/bootstrap-signing.sh),
#    then record values + file paths:
artifactory/scripts/set-org-signing-secrets.sh --init          # writes ~/.tanvrit-signing/secrets.env (chmod 600)
$EDITOR ~/.tanvrit-signing/secrets.env

# 3. Preview, then push to the org:
artifactory/scripts/set-org-signing-secrets.sh --dry-run
artifactory/scripts/set-org-signing-secrets.sh                 # or: --repo tanvrit/ai  (single-repo test)
gh secret list --org tanvrit
```

`~/.tanvrit-signing/` lives **outside** the repo and is never committed. `.gitignore` already blocks
`*.jks *.p8 *.p12 *.keystore .env **/creds/`.

## Per-target verification (never trust a green badge)

| Target | Verify |
|---|---|
| Android | `apksigner verify --print-certs app.aab/apk` → shows the **upload key**, not the debug key; confirm on Play internal track |
| iOS | `codesign -dv --verbose=4` on the .ipa payload; confirm TestFlight processing |
| macOS | `spctl -a -t open --context context:primary-signature app.dmg` + `stapler validate` |
| Maven | `gpg --verify <artifact>.asc <artifact>` against the published public key |
| Container | `cosign verify <image>@<digest>`; inspect provenance attestation |

## Rollout status

| Phase | State |
|---|---|
| P0 secret substrate + keystore helper (`artifactory/scripts/set-org-signing-secrets.sh`) | **code done** — owner runs provisioning |
| P0 fix `ai` macOS env-var name mismatch | **done** |
| P1-S1 `ai` Android → `RELEASE_STORE_*` + canonical secret | **code done** — verify after org secret set |
| P1-S2 `friendly` (rotate git-tracked key + align) | pending |
| P1-S3 `desipops` (rotate git-tracked key + align) | pending |
| P1-S4 `mandee-business` + `store` (drop signing.properties; retarget `store` → :androidApp) | pending |
| P1-S5 `swyft`/`wedding`/`school`/`bharat`/`auditor`/`compute` (add/align; retarget `swyft` → :androidApp) | pending |
| P2 iOS (Match) · P3 desktop · P4 Maven GPG · P5 container cosign · P6 web SLSA | pending |
