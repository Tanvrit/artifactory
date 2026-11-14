#!/usr/bin/env bash
set -euo pipefail
: "${ANDROID_HOME:?ANDROID_HOME is required}"
: "${COMPILE_SDK:?COMPILE_SDK is required}"
[[ "$COMPILE_SDK" =~ ^[1-9][0-9]*(\.(0|[1-9][0-9]*))?$ ]] || {
  echo '::error::compile-sdk must be an API level such as 36, 37 or 37.1'; exit 1;
}
platform="android-$COMPILE_SDK"
if [[ "$COMPILE_SDK" == *.* ]]; then
  # An explicit minor level requires that exact platform, never another 37.*.
  if [ -f "$ANDROID_HOME/platforms/$platform/android.jar" ]; then
    echo "Android API $COMPILE_SDK is installed"; exit 0
  fi
else
  # Preserve legacy integer inputs and their installed-platform reuse behavior.
  for dir in "$ANDROID_HOME/platforms/$platform" "$ANDROID_HOME/platforms/$platform."*; do
    if [ -d "$dir" ]; then echo "Android API $COMPILE_SDK is installed"; exit 0; fi
  done
  if [ "$COMPILE_SDK" -ge 37 ]; then platform="$platform.0"; fi
fi
sdkmanager "platforms;$platform"
if [[ "$COMPILE_SDK" == *.* ]] && [ ! -f "$ANDROID_HOME/platforms/$platform/android.jar" ]; then
  echo "::error::sdkmanager did not install the required Android API $COMPILE_SDK android.jar"; exit 1
fi
