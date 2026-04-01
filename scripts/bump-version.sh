#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Tanvrit Version Bump Script
# Bumps VERSION_NAME and VERSION_CODE in a product's gradle.properties
#
# Usage:
#   ./bump-version.sh <product> <version>
#   ./bump-version.sh friendly 1.2.0
#   ./bump-version.sh control 1.0.3
#   ./bump-version.sh --list   (show current versions for all products)
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

TANVRIT_ROOT="${TANVRIT_ROOT:-/Users/viveksingh/Developer/tanvrit}"

# Product → gradle.properties path mapping
declare -A PRODUCT_PROPS=(
  [friendly]="platforms/friendly/gradle.properties"
  [desipops]="client/desipops/gradle.properties"
  [mandee]="platforms/mandee/gradle.properties"
  [swyft]="platforms/swyft/gradle.properties"
  [bharat-bandhu]="platforms/bharat-bandhu/gradle.properties"
  [school]="platforms/school/gradle.properties"
  [wedding]="platforms/wedding/gradle.properties"
  [control]="control/gradle.properties"
)

# Show all current versions
list_versions() {
  echo "Current Tanvrit Product Versions"
  echo "═══════════════════════════════"
  for product in "${!PRODUCT_PROPS[@]}"; do
    local props_file="$TANVRIT_ROOT/${PRODUCT_PROPS[$product]}"
    if [ -f "$props_file" ]; then
      local version=$(grep "^VERSION_NAME=" "$props_file" 2>/dev/null | cut -d= -f2 || echo "not set")
      local build=$(grep "^VERSION_CODE=" "$props_file" 2>/dev/null | cut -d= -f2 || echo "not set")
      printf "  %-16s  %s  (build %s)\n" "$product" "$version" "$build"
    else
      printf "  %-16s  gradle.properties not found\n" "$product"
    fi
  done
}

# Bump version for a product
bump_product() {
  local product="$1"
  local version="$2"

  if [ -z "${PRODUCT_PROPS[$product]+x}" ]; then
    echo "Error: Unknown product '$product'"
    echo "Valid products: ${!PRODUCT_PROPS[*]}"
    exit 1
  fi

  local props_file="$TANVRIT_ROOT/${PRODUCT_PROPS[$product]}"
  if [ ! -f "$props_file" ]; then
    echo "Error: gradle.properties not found at: $props_file"
    echo "Ensure TANVRIT_ROOT is set correctly (current: $TANVRIT_ROOT)"
    exit 1
  fi

  # Parse version components
  local major minor patch
  IFS='.' read -r major minor patch <<< "$version"
  if [ -z "$major" ] || [ -z "$minor" ] || [ -z "$patch" ]; then
    echo "Error: Invalid version format '$version'. Expected MAJOR.MINOR.PATCH (e.g. 1.2.0)"
    exit 1
  fi

  local build_code=$(( major * 10000 + minor * 100 + patch ))

  # Show current version
  local current_version=$(grep "^VERSION_NAME=" "$props_file" 2>/dev/null | cut -d= -f2 || echo "not set")
  echo "Bumping $product: $current_version → $version (build $build_code)"

  # Update or add VERSION_NAME
  if grep -q "^VERSION_NAME=" "$props_file"; then
    sed -i.bak "s/^VERSION_NAME=.*/VERSION_NAME=$version/" "$props_file"
  else
    echo "VERSION_NAME=$version" >> "$props_file"
  fi

  # Update or add VERSION_CODE
  if grep -q "^VERSION_CODE=" "$props_file"; then
    sed -i.bak "s/^VERSION_CODE=.*/VERSION_CODE=$build_code/" "$props_file"
  else
    echo "VERSION_CODE=$build_code" >> "$props_file"
  fi

  # Remove sed backup files
  rm -f "${props_file}.bak"

  echo "✓ Updated $props_file"

  # Also update catalog.json in this repo
  local catalog_file="$(dirname "$0")/../manifests/catalog.json"
  if [ -f "$catalog_file" ] && command -v jq &> /dev/null; then
    local today=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local updated=$(jq \
      --arg product "$product" \
      --arg version "$version" \
      --argjson build "$build_code" \
      --arg now "$today" \
      '.updated_at = $now | .products[$product].version = $version | .products[$product].build = $build | .products[$product].released_at = $now' \
      "$catalog_file")
    echo "$updated" > "$catalog_file"
    echo "✓ Updated manifests/catalog.json"
  fi

  echo ""
  echo "Next steps:"
  echo "  1. Review changes: git diff $props_file"
  echo "  2. Commit: git commit -am \"chore: bump $product to v$version\""
  echo "  3. Tag: git tag ${product}-v${version}"
  echo "  4. Push: git push && git push --tags"
}

# ── Main ─────────────────────────────────────────────────────────

case "${1:-}" in
  --list|-l)
    list_versions
    ;;
  "")
    echo "Usage: ./bump-version.sh <product> <version>"
    echo "       ./bump-version.sh --list"
    echo ""
    echo "Products: friendly, desipops, mandee, swyft, bharat-bandhu, school, wedding, control"
    exit 1
    ;;
  *)
    if [ $# -lt 2 ]; then
      echo "Error: Version required"
      echo "Usage: ./bump-version.sh <product> <version>"
      exit 1
    fi
    bump_product "$1" "$2"
    ;;
esac
