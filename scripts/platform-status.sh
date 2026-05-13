#!/usr/bin/env bash
#
# platform-status.sh — Print KMP target coverage + iOS/Android scaffold
# state across every Tanvrit platform.
#
# Helps future Claude sessions / engineers see at a glance:
#   - Which platforms declare which KMP targets (Android, iOS, JVM, Wasm, JS)
#   - Which have iosApp/ Xcode wrappers
#   - Which use composeApp shape vs app shape
#   - Which have per-OS release-<os>.yml callers
#
# Purely read-only. No side effects. No git, no gradle.
#
# Usage:
#   scripts/platform-status.sh                   # all platforms, default format
#   scripts/platform-status.sh --json            # JSON output (for scripting)
#   scripts/platform-status.sh --product <id>    # single platform detail
#
# Reads from $TANVRIT_ROOT (default /Users/viveksingh/Developer/tanvrit).

set -euo pipefail

TANVRIT_ROOT="${TANVRIT_ROOT:-/Users/viveksingh/Developer/tanvrit}"
JSON=""
PRODUCT_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)              JSON="1"; shift ;;
    --product)           PRODUCT_FILTER="$2"; shift 2 ;;
    --product=*)         PRODUCT_FILTER="${1#*=}"; shift ;;
    -h|--help)
      sed -n '/^# /,/^$/p' "$0" | sed 's/^# //; s/^#//'
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# ── Platform discovery ────────────────────────────────────────────────────
PLATFORMS=()
for d in "$TANVRIT_ROOT"/platforms/*/ "$TANVRIT_ROOT"/client/*/ "$TANVRIT_ROOT"/control/; do
  [[ -d "$d" ]] || continue
  name="$(basename "$d")"
  case "$name" in
    artifactory|deploy-logs|_shared|artistic-salon-spa|madison-geeks) continue ;;
  esac
  if [[ -n "$PRODUCT_FILTER" && "$name" != "$PRODUCT_FILTER" ]]; then
    continue
  fi
  PLATFORMS+=("$d")
done

# ── Per-platform inspection ───────────────────────────────────────────────
check_grep() {
  local file="$1"
  local pattern="$2"
  if [[ -f "$file" ]] && grep -qE "$pattern" "$file"; then
    echo "Y"
  else
    echo "N"
  fi
}

inspect_platform() {
  local dir="$1"
  local name="$(basename "$dir")"

  local shape="—"
  local gradle=""
  if [[ -f "$dir/composeApp/build.gradle.kts" ]]; then
    shape="composeApp"
    gradle="$dir/composeApp/build.gradle.kts"
  elif [[ -f "$dir/app/build.gradle.kts" ]]; then
    shape="app"
    gradle="$dir/app/build.gradle.kts"
  fi

  local has_iosapp="N"
  [[ -d "$dir/iosApp" ]] && has_iosapp="Y"

  local has_iosmain="N"
  if [[ "$shape" != "—" ]]; then
    local mod="${shape}"
    [[ -d "$dir/$mod/src/iosMain" ]] && has_iosmain="Y"
  fi

  local target_and="—"
  local target_ios="—"
  local target_jvm="—"
  local target_wasm="—"
  local target_js="—"
  if [[ -n "$gradle" ]]; then
    target_and=$(check_grep "$gradle" 'androidTarget')
    target_ios=$(check_grep "$gradle" 'iosArm64\s*\(|iosSimulatorArm64\s*\(')
    target_jvm=$(check_grep "$gradle" '^\s*jvm\s*\(|^\s*jvm\s*\{|jvm\s*\("desktop"')
    target_wasm=$(check_grep "$gradle" 'wasmJs\s*\{|wasmJs\s*\(')
    target_js=$(check_grep "$gradle" '^\s*js\s*\(')
  fi

  local callers=""
  for os in android ios macos windows linux web; do
    if [[ -f "$dir/.github/workflows/release-${os}.yml" ]]; then
      callers+="${os:0:1}"
    else
      callers+="-"
    fi
  done

  if [[ -n "$JSON" ]]; then
    cat <<EOF
{
  "name": "$name",
  "shape": "$shape",
  "iosApp": "$has_iosapp",
  "iosMain": "$has_iosmain",
  "targets": {
    "android": "$target_and",
    "ios": "$target_ios",
    "jvm": "$target_jvm",
    "wasm": "$target_wasm",
    "js": "$target_js"
  },
  "callers": "$callers"
}
EOF
  else
    printf "%-22s %-12s %-8s %-8s   %-3s %-3s %-3s %-3s %-3s   %s\n" \
      "$name" "$shape" "$has_iosapp" "$has_iosmain" \
      "$target_and" "$target_ios" "$target_jvm" "$target_wasm" "$target_js" \
      "$callers"
  fi
}

# ── Output ────────────────────────────────────────────────────────────────
if [[ -n "$JSON" ]]; then
  echo "["
  first=1
  for p in "${PLATFORMS[@]}"; do
    [[ $first -eq 1 ]] || echo ","
    inspect_platform "$p"
    first=0
  done
  echo
  echo "]"
else
  printf "%-22s %-12s %-8s %-8s   %-3s %-3s %-3s %-3s %-3s   %s\n" \
    "PLATFORM" "SHAPE" "iosApp/" "iosMain/" \
    "AND" "iOS" "JVM" "WASM" "JS" "CALLERS"
  printf "%s\n" "─────────────────────────────────────────────────────────────────────────────────────"
  for p in "${PLATFORMS[@]}"; do
    inspect_platform "$p"
  done
  echo
  echo "CALLERS column: a/i/m/w/l/w (android/ios/macos/windows/linux/web). - = caller missing."
fi
