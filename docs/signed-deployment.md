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

## SECURITY REMEDIATION — committed signing secrets (OWNER ACTION, high priority)

Discovered during the P1 rollout: signing material is **committed to git** (tracked in HEAD) across
several repos. These are exposed to anyone with repo/history access and must be **rotated**, not just
untracked (untracking leaves them in history).

| Repo | Tracked secret files |
|---|---|
| `platforms/mandee-business` | `creds/AuthKey_96VBAC29H2.p8` (**Apple ASC API key — REVOKE FIRST**), `creds/key_store`, `key_store_bharat`, `key_store_mandee`, `key_store_solute` |
| `platforms/store` | `creds/key_store`, `key_store_bharat`, `key_store_mandee`, `key_store_solute` |
| `client/desipops` | `creds/key_store`, `key_store_bharat`, `key_store_solute` |
| `platforms/friendly` | `creds/key_store_friendly` — **untracked in HEAD** during P1, but still in history |

Remediation sequence (owner-driven — do NOT untrack before rotating, and history rewrite = force-push):
1. **Revoke** the Apple ASC API key `AuthKey_96VBAC29H2` in App Store Connect → Users & Access → Integrations, and issue a fresh one (store as `ASC_API_KEY_P8` org secret).
2. **Rotate** each exposed upload keystore. If an app is already live on Play under that upload key, request a Play Console **upload-key reset** (App integrity → Upload key certificate → Request reset). Move the new key to the `ANDROID_SIGNING_KEYSTORE` org secret.
3. `git rm --cached` the files + confirm `.gitignore` covers `creds/` (`**/creds/` already present in most), commit, push.
4. **Purge history** (`git filter-repo`/BFG) and force-push — coordinate, as this rewrites shared history.

## Open flags (owner decisions surfaced during P1)

- **`desipops` Android package**: code/`CLAUDE.md` conflict — the release-android.yml `application_id`
  is `com.tanvrit.desipops` (documented as the "release applicationId"), but the AAB's real
  `applicationId` is `com.desipops` (even the release flavor). Left unchanged; confirm the intended
  Play package (a Play upload of a `com.desipops` AAB to a `com.tanvrit.desipops` listing will fail).
- **`school` alias graceful-skip — RESOLVED**: the shared release templates now resolve a
  `settings.gradle.kts` module→dir alias (`project(":composeApp").projectDir = file("app")`) for both
  the build-file guard and the artifact-output path. android template fixed on `main`; ios/macos on
  `signing/templates`.
- **`mandee-business` Play task**: `assemble_android.yml` runs `:androidApp:publishBusinessReleaseBundle`
  but there are no product flavors, so the task is likely `publishReleaseBundle` — verify it resolves.

## Rollout status

| Phase | State |
|---|---|
| P0 secret substrate + keystore helper (`artifactory/scripts/set-org-signing-secrets.sh`) | **code done** — owner runs provisioning |
| P0 fix `ai` macOS env-var name mismatch | **done** |
| P1 Android — `ai`, `friendly`, `desipops`, `mandee-business`, `store`, `swyft`, `wedding`, `school`, `auditor`, `compute` all → `RELEASE_STORE_*`; committed+pushed; `store`/`swyft` retargeted `:composeApp`→`:androidApp`; `friendly`/`school` Play packageName fixed | **code done + configs validated** (ai/swyft/wedding green) — end-to-end signed-AAB verify after org secret set |
| P3 app-side — macOS `signing{}` block added to 12 desktop apps (`swyft`,`tanvrit`,`friendly`,`wedding`,`admin`,`automator`,`compute`,`market`,`mandee-business`,`auditor`,`school`,`desipops`) reading `SIGNING_IDENTITY` | **done + pushed** — wedding/school configs validated |
| P6 wiring — web SLSA `permissions:{id-token,attestations}` added to all `release-web.yml` template-callers | **done + pushed** (non-template web callers `tanvrit`/`mandee`/`desipops` skipped correctly) |
| Template alias-awareness (fix `school`-style module/dir alias) — android on `main` (5032d8d), ios/macos on `signing/templates` (a1ed651) | **done** |
| Non-git-repo dirs: `bharat-online`, `tackll`, `madison-geeks`, `artistic-salon-spa` | **edited** — not git repos; owner must `git init`/create GitHub repo + commit the working-tree edits |
| P2 iOS Match + P3 macOS secrets + P6 web SLSA (`artifactory` templates, additive) | **branch `signing/templates`** (a1ed651) — yaml-validated; app-side ready (all apps declare iosArm64 + have xcodeproj) |
| P4 Maven GPG — `sdk` (`signing/gpg` bab56da), `core` (`signing/gpg` db34dad) | **branch ready** — gradle-config validated keyless AND keyed |
| P4+P5 — `compute` agent JAR GPG + native codesign/notarize/cosign + homebrew sha | **branch `signing/gpg-cosign`** (dd2b542) |
| P5 container cosign — `server` (`signing/cosign` 96d86aa), `host` (`signing/cosign` 34158e6) | **branch ready** — keyless OIDC, no secret |

All P2–P6 changes are **additive** (existing behavior unchanged when new secrets absent) and live on
`signing/*` branches — **not `main`** — so nothing publishes/deploys until you merge.

### Draft PRs (review-ready)

| PR | Branch | Merge caveat |
|---|---|---|
| [artifactory#4](https://github.com/Tanvrit/artifactory/pull/4) | `signing/templates` | needs Apple secrets + Match repo to activate; additive |
| [sdk#86](https://github.com/Tanvrit/sdk/pull/86) | `signing/gpg` | **republishes signed** — merge as a `pluginVersion` bump |
| [core#21](https://github.com/Tanvrit/core/pull/21) | `signing/gpg` | **republishes signed** — merge as a `pluginVersion` bump |
| [compute#4](https://github.com/Tanvrit/compute/pull/4) | `signing/gpg-cosign` | merge with next agent release |
| [server#84](https://github.com/Tanvrit/server/pull/84) | `signing/cosign` | merging deploys server; keyless, no secret |
| [host#1](https://github.com/Tanvrit/host/pull/1) | `signing/cosign` | keyless, no secret |

## Merge & activation order

1. **Provision org secrets first** (`artifactory/scripts/set-org-signing-secrets.sh`): GPG for Maven;
   Match repo + ASC + Developer ID for Apple. Keyless cosign needs nothing.
2. **`signing/cosign` (server, host)** — safe to merge anytime; keyless, additive. Merging `server`
   triggers a Cloud Run + Mac-mini deploy (that's the normal deploy path) — do it as a deliberate deploy.
3. **`signing/templates` (artifactory)** — merge after the Apple secrets + Match repo exist; additive,
   so the 14 callers keep working meanwhile. **Web provenance also needs each caller's `release-web.yml`
   `release:` job to add `permissions: { id-token: write, attestations: write }`** — `secrets: inherit`
   does not pass permissions (a follow-up sweep across the web callers).
4. **`signing/gpg` (sdk, core)** — merging republishes **signed** artifacts; do it **as a coordinated
   version bump** (a real release-train), not a casual merge, so you don't overwrite a live version.
5. **`signing/gpg-cosign` (compute)** — merge with the next agent release.
6. Verify each per the table above (`gpg --verify`, `cosign verify`, `codesign -dv`, `spctl`).
