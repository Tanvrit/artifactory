#!/usr/bin/env bash
#
# scaffold-iosapp.sh — Generate iosApp/ Xcode wrapper for a Tanvrit platform.
#
# Codifies the cp + sed pattern that was hand-executed for swyft, wedding,
# school, admin, automator on 2026-05-13. After this script, the platform's
# release-ios.yml caller workflow stops graceful-skipping at the iosApp/
# guard and starts attempting a real xcodebuild archive.
#
# Usage:
#   scripts/scaffold-iosapp.sh <product>
#     --bundle-id com.tanvrit.<product>
#     [--source-platform friendly]   # template platform to clone from
#     [--shape composeApp|app]        # gradle module shape (default: composeApp)
#     [--swift-fn MainViewController] # Kotlin function name exposed to Swift
#     [--dry-run]
#
# Example — most common case (composeApp shape, uppercase MainViewController):
#   scripts/scaffold-iosapp.sh wedding --bundle-id com.tanvrit.wedding
#
# Example — app-shape platform (school, admin, control):
#   scripts/scaffold-iosapp.sh admin \
#     --bundle-id com.tanvrit.admin \
#     --shape app \
#     --swift-fn mainViewController
#
# Pre-conditions:
#   - $TANVRIT_ROOT (env var, default /Users/viveksingh/Developer/tanvrit) is
#     the path to the monorepo.
#   - $TANVRIT_ROOT/platforms/<product>/ exists.
#   - $TANVRIT_ROOT/platforms/<product>/<shape>/build.gradle.kts declares
#     iosArm64() and iosSimulatorArm64() targets.
#
# Post-conditions:
#   - $TANVRIT_ROOT/platforms/<product>/iosApp/ contains a fully-rebranded
#     copy of the source platform's iosApp/.
#   - The pbxproj's Compile Kotlin Framework shellScript invokes the right
#     gradle module (`:composeApp:` or `:app:`).
#   - The Swift ContentView.swift calls MainViewControllerKt.<swift-fn>().
#   - Config.xcconfig has PRODUCT_NAME=<Product> and bundle id <bundle-id>.
#
# What it does NOT do:
#   - Generate a branded 1024×1024 app icon (you copy the placeholder).
#   - Set TEAM_ID in Config.xcconfig (left blank for CI to fill).
#   - Update the platform's release-ios.yml gradle_module input (manual edit
#     for app-shape platforms; composeApp shape needs no change).
#   - Commit or push (intentional — review the diff first).
#
# Forward-only: never modifies an existing iosApp/. If one is already in
# place, the script exits with an error.

set -euo pipefail

TANVRIT_ROOT="${TANVRIT_ROOT:-/Users/viveksingh/Developer/tanvrit}"
SOURCE_PLATFORM="friendly"
SHAPE="composeApp"
SWIFT_FN="MainViewController"
BUNDLE_ID=""
DRY_RUN=""
PRODUCT=""

# ── Arg parsing ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle-id)        BUNDLE_ID="$2";        shift 2 ;;
    --source-platform)  SOURCE_PLATFORM="$2";  shift 2 ;;
    --shape)            SHAPE="$2";            shift 2 ;;
    --swift-fn)         SWIFT_FN="$2";         shift 2 ;;
    --dry-run)          DRY_RUN="1";           shift ;;
    -h|--help)
      sed -n '/^# /,/^$/p' "$0" | sed 's/^# //; s/^#//'
      exit 0
      ;;
    -*)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
    *)
      if [[ -z "$PRODUCT" ]]; then
        PRODUCT="$1"
      else
        echo "Multiple positional args: $PRODUCT, $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

# ── Validation ────────────────────────────────────────────────────────────
if [[ -z "$PRODUCT" ]]; then
  echo "Usage: $0 <product> --bundle-id <id> [options]" >&2
  exit 2
fi
if [[ -z "$BUNDLE_ID" ]]; then
  echo "ERROR: --bundle-id is required (e.g. com.tanvrit.$PRODUCT)" >&2
  exit 2
fi
case "$SHAPE" in
  composeApp|app) ;;
  *)
    echo "ERROR: --shape must be 'composeApp' or 'app' (got: $SHAPE)" >&2
    exit 2
    ;;
esac

PLATFORM_DIR="$TANVRIT_ROOT/platforms/$PRODUCT"
SOURCE_DIR="$TANVRIT_ROOT/platforms/$SOURCE_PLATFORM/iosApp"
DEST_DIR="$PLATFORM_DIR/iosApp"

if [[ ! -d "$PLATFORM_DIR" ]]; then
  echo "ERROR: Platform directory not found: $PLATFORM_DIR" >&2
  exit 2
fi
if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "ERROR: Source iosApp/ not found at $SOURCE_DIR — check --source-platform" >&2
  exit 2
fi
if [[ -d "$DEST_DIR" ]]; then
  echo "ERROR: iosApp/ already exists at $DEST_DIR — forward-only, won't overwrite" >&2
  echo "       Delete it manually if you really want to re-scaffold from scratch." >&2
  exit 2
fi
if [[ ! -f "$PLATFORM_DIR/$SHAPE/build.gradle.kts" ]]; then
  echo "ERROR: $SHAPE/build.gradle.kts not found in $PLATFORM_DIR — wrong shape?" >&2
  exit 2
fi
if ! grep -qE 'iosArm64\s*\(' "$PLATFORM_DIR/$SHAPE/build.gradle.kts"; then
  echo "ERROR: $SHAPE/build.gradle.kts doesn't declare iosArm64() — add the iOS" >&2
  echo "       framework block first, then re-run this script." >&2
  exit 2
fi

# Derive PRODUCT_NAME (capitalized) for the xcconfig
PRODUCT_NAME="$(echo "${PRODUCT:0:1}" | tr '[:lower:]' '[:upper:]')${PRODUCT:1}"

echo ""
echo "Scaffolding iosApp/ for '$PRODUCT'"
echo "  Source:       $SOURCE_DIR"
echo "  Destination:  $DEST_DIR"
echo "  Bundle ID:    $BUNDLE_ID"
echo "  Shape:        $SHAPE"
echo "  Swift fn:     $SWIFT_FN"
echo "  PRODUCT_NAME: $PRODUCT_NAME"
if [[ -n "$DRY_RUN" ]]; then
  echo "  DRY-RUN — no files written"
fi
echo ""

# ── Execution ─────────────────────────────────────────────────────────────
run() {
  if [[ -n "$DRY_RUN" ]]; then
    echo "  [dry] $*"
  else
    eval "$@"
  fi
}

# 1. Copy the template
run cp -r "$SOURCE_DIR" "$DEST_DIR"

# 2. Drop user-specific dirs that don't belong in git
run rm -rf "$DEST_DIR/iosApp.xcodeproj/xcuserdata"
run rm -rf "$DEST_DIR/iosApp.xcodeproj/project.xcworkspace/xcuserdata"
run rm -rf "$DEST_DIR/build"

# In dry-run, skip the rest — sed targets don't exist yet
if [[ -n "$DRY_RUN" ]]; then
  echo ""
  echo "DRY-RUN complete. Pass without --dry-run to actually scaffold."
  exit 0
fi

# 3. Determine source product name (for sed)
SOURCE_PRODUCT_NAME="$(echo "${SOURCE_PLATFORM:0:1}" | tr '[:lower:]' '[:upper:]')${SOURCE_PLATFORM:1}"
SOURCE_BUNDLE_PATTERN="com\.${SOURCE_PLATFORM}\.${SOURCE_PRODUCT_NAME}"

# 4. Rewrite Config.xcconfig
sed -i '' \
  -e "s|PRODUCT_NAME=${SOURCE_PRODUCT_NAME}|PRODUCT_NAME=${PRODUCT_NAME}|g" \
  -e "s|PRODUCT_BUNDLE_IDENTIFIER=${SOURCE_BUNDLE_PATTERN}|PRODUCT_BUNDLE_IDENTIFIER=${BUNDLE_ID}|g" \
  "$DEST_DIR/Configuration/Config.xcconfig"

# 5. Rewrite pbxproj — Friendly.app → <Product>.app + maybe :composeApp: → :app:
sed -i '' \
  -e "s|${SOURCE_PRODUCT_NAME}\.app|${PRODUCT_NAME}.app|g" \
  "$DEST_DIR/iosApp.xcodeproj/project.pbxproj"

if [[ "$SHAPE" == "app" ]]; then
  sed -i '' \
    -e "s|:composeApp:embedAndSignAppleFrameworkForXcode|:app:embedAndSignAppleFrameworkForXcode|g" \
    "$DEST_DIR/iosApp.xcodeproj/project.pbxproj"
fi

# 6. Rewrite ContentView.swift — swap the Swift bridge fn if needed
if [[ "$SWIFT_FN" != "MainViewController" ]]; then
  # Pattern from friendly's template: MainViewControllerKt.MainViewController()
  # New target: MainViewControllerKt.<swift-fn>()  OR  MainKt.<swift-fn>() etc.
  if [[ "$SWIFT_FN" =~ ^(MainKt\.|MainViewControllerKt\.) ]]; then
    # Caller passed a qualified Swift call like "MainKt.mainViewController"
    sed -i '' \
      -e "s|MainViewControllerKt\.MainViewController()|${SWIFT_FN}()|g" \
      "$DEST_DIR/iosApp/ContentView.swift"
  else
    # Plain function name — keep the MainViewControllerKt class prefix
    sed -i '' \
      -e "s|MainViewControllerKt\.MainViewController()|MainViewControllerKt.${SWIFT_FN}()|g" \
      "$DEST_DIR/iosApp/ContentView.swift"
  fi
fi

echo "Done. Next steps:"
echo "  1. Verify via:    xcodebuild -list -project $DEST_DIR/iosApp.xcodeproj"
echo "  2. Verify via:    plutil -lint $DEST_DIR/iosApp/Info.plist"
if [[ "$SHAPE" == "app" ]]; then
  echo "  3. Edit:          $PLATFORM_DIR/.github/workflows/release-ios.yml"
  echo "                    Set: gradle_module: app"
fi
echo "  4. Replace placeholder icon at:"
echo "       $DEST_DIR/iosApp/Assets.xcassets/AppIcon.appiconset/app-icon-1024.png"
echo "       (currently a copy of $SOURCE_PLATFORM's icon)"
echo ""
echo "  5. Stage iosApp/ + commit:"
echo "       cd $PLATFORM_DIR && git add iosApp/ && git commit"
echo ""
