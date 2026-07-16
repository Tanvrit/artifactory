#!/usr/bin/env bash
# =============================================================================
# Tanvrit — canonical signing-secret provisioner (owner-run)
#
# Pushes the ONE canonical set of code-signing secrets to the GitHub org (or a
# single repo for testing) so every platform's release CI can produce SIGNED
# artifacts. This is the substrate for the fleet-wide signed-deployment rollout
# (see ~/.claude/plans/ signed-deployment plan; docs below).
#
# It NEVER stores secrets in the repo: it reads plain values + file PATHS from a
# gitignored config file (default ~/.tanvrit-signing/secrets.env, chmod 600),
# base64-encodes the binary material on the fly, and calls `gh secret set`.
#
# Usage:
#   scripts/set-org-signing-secrets.sh --init            # write a config template
#   scripts/set-org-signing-secrets.sh --dry-run         # show what WOULD be set
#   scripts/set-org-signing-secrets.sh                   # set org secrets (visibility: all)
#   scripts/set-org-signing-secrets.sh --repo tanvrit/ai # set on ONE repo (safe test)
#   scripts/set-org-signing-secrets.sh --only android    # only the android group
#
# Groups: android | apple | macos | windows | maven | play | match  (default: all)
#
# Prereqs: `gh auth login` with org-admin (org secrets) or repo-admin (--repo).
# Companion: platforms/ai/scripts/bootstrap-signing.sh generates the keystore +
# collects Apple/Play creds into ~/.tanvrit-ai-secrets/ — point this script's
# file paths at that output.
# =============================================================================
set -euo pipefail

ORG="tanvrit"
CONFIG="${TANVRIT_SIGNING_CONFIG:-$HOME/.tanvrit-signing/secrets.env}"
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
log()  { printf '%s[secrets]%s %s\n' "$CYAN" "$NC" "$*"; }
ok()   { printf '%s[secrets] ✓%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s[secrets] !%s %s\n' "$YELLOW" "$NC" "$*" >&2; }
die()  { printf '%s[secrets] ✗%s %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

DRY_RUN=false
REPO=""            # empty => org scope
ONLY=""            # empty => all groups
for arg in "$@"; do
  case "$arg" in
    --init)          INIT=true ;;
    --gen-keystore)  GEN_KEYSTORE=true ;;
    --dry-run) DRY_RUN=true ;;
    --repo)    NEXT=repo ;;
    --only)    NEXT=only ;;
    -h|--help) sed -n '2,/^# ====/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)
      case "${NEXT:-}" in
        repo) REPO="$arg"; NEXT="" ;;
        only) ONLY="$arg"; NEXT="" ;;
        *)    die "Unknown arg: $arg (see --help)" ;;
      esac ;;
  esac
done

# ---- --init: write a config template -----------------------------------------
if [ "${INIT:-false}" = true ]; then
  mkdir -p "$(dirname "$CONFIG")"; chmod 700 "$(dirname "$CONFIG")" 2>/dev/null || true
  [ -f "$CONFIG" ] && die "Config already exists at $CONFIG (refusing to overwrite)"
  cat > "$CONFIG" <<'TEMPLATE'
# Tanvrit signing secrets — plain VALUES and file PATHS. chmod 600. Never commit.
# Leave a line blank/commented to SKIP that secret. Binary files are base64'd
# automatically by the provisioner; JSON/armored-key files are sent as-is.

# ── Android (group: android) ────────────────────────────────────────────────
ANDROID_KEYSTORE_FILE=$HOME/.tanvrit-signing/android-upload.keystore   # .jks path
ANDROID_SIGNING_KEYSTORE_PASSWORD=
ANDROID_SIGNING_KEY_ALIAS=tanvrit-upload
ANDROID_SIGNING_KEY_PASSWORD=

# ── Google Play upload (group: play) ────────────────────────────────────────
PLAY_STORE_SERVICE_ACCOUNT_JSON_FILE=$HOME/.tanvrit-signing/play-service-account.json

# ── Apple: App Store Connect API key (group: apple) ─────────────────────────
ASC_API_KEY_P8_FILE=$HOME/.tanvrit-signing/AuthKey.p8                  # .p8 path
ASC_API_KEY_ID=
ASC_API_ISSUER_ID=
APPLE_TEAM_ID=F846F22MFM

# ── Apple: Fastlane Match (group: match) ────────────────────────────────────
MATCH_GIT_URL=
MATCH_PASSWORD=
MATCH_GIT_BASIC_AUTHORIZATION=      # base64 of "user:token" for HTTPS match repo

# ── Apple: macOS Developer ID (group: macos) ────────────────────────────────
APPLE_DEVELOPER_ID_CERT_P12_FILE=$HOME/.tanvrit-signing/developer-id.p12
APPLE_CERT_PASSWORD=
MACOS_DEVELOPER_ID_APPLICATION=     # identity string, e.g. "Developer ID Application: Tanvrit Pvt. Ltd. (F846F22MFM)"
MACOS_NOTARIZATION_APPLE_ID=
MACOS_NOTARIZATION_PASSWORD=        # app-specific password

# ── Windows Authenticode (group: windows) ───────────────────────────────────
WINDOWS_SIGNING_CERT_FILE=          # .pfx path (leave blank until an OV/EV cert exists)
WINDOWS_SIGNING_PASSWORD=

# ── Maven artifact GPG signing (group: maven) ───────────────────────────────
GPG_PRIVATE_KEY_FILE=$HOME/.tanvrit-signing/maven-signing-key.asc      # ascii-armored
GPG_PASSPHRASE=
TEMPLATE
  chmod 600 "$CONFIG"
  ok "Wrote template to $CONFIG — fill it in, then re-run without --init."
  exit 0
fi

# ---- --gen-keystore: generate the org Android upload keystore -----------------
# Runs BEFORE the config must exist (chicken/egg: gen the key, THEN record its
# path + passwords in secrets.env). Reusable across every Tanvrit Android app
# (Play App Signing lets one upload key serve many package IDs).
if [ "${GEN_KEYSTORE:-false}" = true ]; then
  command -v keytool >/dev/null || die "keytool not found (install a JDK)."
  KS="${ANDROID_KEYSTORE_FILE:-$HOME/.tanvrit-signing/android-upload.keystore}"
  mkdir -p "$(dirname "$KS")"; chmod 700 "$(dirname "$KS")" 2>/dev/null || true
  [ -f "$KS" ] && die "Keystore already exists at $KS (refusing to overwrite — this key is PERMANENT)."
  warn "════════════════════════════════════════════════════════════════════"
  warn " This upload keystore is PERMANENT. Losing it BRICKS every Play"
  warn " listing signed with it (Google does not allow re-keying). Back it up"
  warn " to a password manager + an encrypted offline copy IMMEDIATELY after."
  warn "════════════════════════════════════════════════════════════════════"
  read -r -p "  Type 'I UNDERSTAND' to proceed: " confirm
  [ "$confirm" = "I UNDERSTAND" ] || die "Aborted."
  read -r -s -p "  Keystore password (≥12 chars): " KP; echo
  [ "${#KP}" -ge 12 ] || die "Password too short (≥12)."
  read -r -p "  Key alias [tanvrit-upload]: " ALIAS; ALIAS="${ALIAS:-tanvrit-upload}"
  read -r -p "  Org (O) [Tanvrit Pvt. Ltd.]: " O; O="${O:-Tanvrit Pvt. Ltd.}"
  read -r -p "  Country (C, ISO alpha-2) [IN]: " C; C="${C:-IN}"
  keytool -genkeypair -keystore "$KS" -storepass "$KP" -keypass "$KP" \
    -keyalg RSA -keysize 2048 -validity 25000 -alias "$ALIAS" \
    -dname "CN=Tanvrit, O=$O, C=$C" || die "keytool failed."
  chmod 600 "$KS"
  ok "Generated $KS (alias=$ALIAS)."
  log "SHA-1 / SHA-256 fingerprints (register under Play Console → App integrity):"
  keytool -list -v -keystore "$KS" -storepass "$KP" -alias "$ALIAS" 2>/dev/null \
    | grep -E 'SHA1:|SHA256:' | sed 's/^/    /'
  log "Now record in $CONFIG:"
  log "  ANDROID_KEYSTORE_FILE=$KS"
  log "  ANDROID_SIGNING_KEYSTORE_PASSWORD=<the password you just set>"
  log "  ANDROID_SIGNING_KEY_ALIAS=$ALIAS"
  log "  ANDROID_SIGNING_KEY_PASSWORD=<same as keystore password>"
  exit 0
fi

command -v gh >/dev/null || die "gh CLI not found. Install: https://cli.github.com"
gh auth status >/dev/null 2>&1 || die "gh not authenticated. Run: gh auth login"
[ -f "$CONFIG" ] || die "No config at $CONFIG. Run: $0 --init"
# shellcheck disable=SC1090
set -a; . "$CONFIG"; set +a

if [ -n "$REPO" ]; then
  SCOPE_ARGS=(--repo "$REPO"); SCOPE_DESC="repo $REPO"
else
  SCOPE_ARGS=(--org "$ORG" --visibility all); SCOPE_DESC="org $ORG (visibility: all)"
fi
log "Target scope: $SCOPE_DESC"
$DRY_RUN && warn "DRY RUN — no secrets will be written."

group_active() { [ -z "$ONLY" ] || [ "$ONLY" = "$1" ]; }

# set_secret NAME VALUE
set_secret() {
  local name="$1" value="$2"
  if [ -z "$value" ]; then warn "skip $name (empty)"; return 0; fi
  if $DRY_RUN; then ok "would set $name (${#value} bytes)"; return 0; fi
  printf '%s' "$value" | gh secret set "$name" "${SCOPE_ARGS[@]}" --body - \
    && ok "set $name" || die "failed to set $name"
}
# set_from_file NAME PATH [--base64]
set_from_file() {
  local name="$1" path="$2" mode="${3:-}"
  if [ -z "$path" ]; then warn "skip $name (no path in config)"; return 0; fi
  [ -f "$path" ] || { warn "skip $name (file not found: $path)"; return 0; }
  local value
  if [ "$mode" = "--base64" ]; then value="$(base64 < "$path" | tr -d '\n')"; else value="$(cat "$path")"; fi
  set_secret "$name" "$value"
}

# ── Android ──────────────────────────────────────────────────────────────────
if group_active android; then
  log "── Android upload key ──"
  set_from_file ANDROID_SIGNING_KEYSTORE "${ANDROID_KEYSTORE_FILE:-}" --base64
  set_secret    ANDROID_SIGNING_KEYSTORE_PASSWORD "${ANDROID_SIGNING_KEYSTORE_PASSWORD:-}"
  set_secret    ANDROID_SIGNING_KEY_ALIAS         "${ANDROID_SIGNING_KEY_ALIAS:-}"
  set_secret    ANDROID_SIGNING_KEY_PASSWORD      "${ANDROID_SIGNING_KEY_PASSWORD:-}"
fi
# ── Play ─────────────────────────────────────────────────────────────────────
if group_active play; then
  log "── Google Play service account ──"
  set_from_file PLAY_STORE_SERVICE_ACCOUNT_JSON "${PLAY_STORE_SERVICE_ACCOUNT_JSON_FILE:-}"
fi
# ── Apple ASC API key ────────────────────────────────────────────────────────
if group_active apple; then
  log "── App Store Connect API key ──"
  set_from_file ASC_API_KEY_P8 "${ASC_API_KEY_P8_FILE:-}" --base64
  set_secret    ASC_API_KEY_ID    "${ASC_API_KEY_ID:-}"
  set_secret    ASC_API_ISSUER_ID "${ASC_API_ISSUER_ID:-}"
  set_secret    APPLE_TEAM_ID     "${APPLE_TEAM_ID:-}"
fi
# ── Match ────────────────────────────────────────────────────────────────────
if group_active match; then
  log "── Fastlane Match ──"
  set_secret MATCH_GIT_URL                "${MATCH_GIT_URL:-}"
  set_secret MATCH_PASSWORD               "${MATCH_PASSWORD:-}"
  set_secret MATCH_GIT_BASIC_AUTHORIZATION "${MATCH_GIT_BASIC_AUTHORIZATION:-}"
fi
# ── macOS Developer ID ───────────────────────────────────────────────────────
if group_active macos; then
  log "── macOS Developer ID ──"
  set_from_file APPLE_DEVELOPER_ID_CERT_P12 "${APPLE_DEVELOPER_ID_CERT_P12_FILE:-}" --base64
  set_secret    APPLE_CERT_PASSWORD             "${APPLE_CERT_PASSWORD:-}"
  set_secret    MACOS_DEVELOPER_ID_APPLICATION  "${MACOS_DEVELOPER_ID_APPLICATION:-}"
  set_secret    MACOS_NOTARIZATION_APPLE_ID     "${MACOS_NOTARIZATION_APPLE_ID:-}"
  set_secret    MACOS_NOTARIZATION_PASSWORD     "${MACOS_NOTARIZATION_PASSWORD:-}"
fi
# ── Windows ──────────────────────────────────────────────────────────────────
if group_active windows; then
  log "── Windows Authenticode ──"
  set_from_file WINDOWS_SIGNING_CERT "${WINDOWS_SIGNING_CERT_FILE:-}" --base64
  set_secret    WINDOWS_SIGNING_PASSWORD "${WINDOWS_SIGNING_PASSWORD:-}"
fi
# ── Maven GPG ────────────────────────────────────────────────────────────────
if group_active maven; then
  log "── Maven GPG signing ──"
  set_from_file GPG_PRIVATE_KEY "${GPG_PRIVATE_KEY_FILE:-}"
  set_secret    GPG_PASSPHRASE "${GPG_PASSPHRASE:-}"
fi

if [ -n "$REPO" ]; then
  log "Done. Verify with:  gh secret list --repo $REPO"
else
  log "Done. Verify with:  gh secret list --org $ORG"
fi
$DRY_RUN && warn "This was a DRY RUN — re-run without --dry-run to apply."
