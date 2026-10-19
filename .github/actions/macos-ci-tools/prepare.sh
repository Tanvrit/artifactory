#!/usr/bin/env bash
set -euo pipefail

[ "$(uname -s)" = Darwin ] || { echo "::error::macos-ci-tools requires macOS"; exit 1; }
: "${GITHUB_PATH:?GITHUB_PATH is required}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"

brew_bin=$(command -v brew || true)
if [ -z "$brew_bin" ]; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then brew_bin=$candidate; break; fi
  done
fi
if [ -z "$brew_bin" ]; then
  echo "::error::Provision Homebrew for the macOS runner user before starting CI."
  exit 1
fi
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
for formula in bash coreutils findutils gnu-sed grep jq openssl@3; do
  if ! "$brew_bin" list --versions "$formula" >/dev/null 2>&1; then
    "$brew_bin" install "$formula"
  fi
  prefix=$("$brew_bin" --prefix "$formula")
  for dir in "$prefix/libexec/gnubin" "$prefix/bin"; do
    if [ -d "$dir" ]; then
      printf '%s
' "$dir" >> "$GITHUB_PATH"
      export PATH="$dir:$PATH"
    fi
  done
done

# Reuse the runner user's SDK when present; jobs needing installation use android-sdk.
for sdk in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}" "$HOME/Library/Android/sdk" "$HOME/Android/Sdk"; do
  if [ -n "$sdk" ] && [ -d "$sdk/platforms" ]; then
    printf 'ANDROID_HOME=%s
ANDROID_SDK_ROOT=%s
' "$sdk" "$sdk" >> "$GITHUB_ENV"
    printf '%s
' "$sdk/platform-tools" >> "$GITHUB_PATH"
    break
  fi
done

if [ "${CI_REQUIRE_DOCKER:-false}" = true ]; then
  if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "::error::This job needs a running Linux Docker engine. Start Docker Desktop or Colima for the runner user, or configure its Docker context. A Docker CLI alone is insufficient."
    exit 1
  fi
  [ "$(docker info --format '{{.OSType}}')" = linux ] || { echo "::error::CI requires Linux containers"; exit 1; }
fi
if [ "${CI_REQUIRE_GUI:-false}" = true ]; then
  console_user=$(/usr/bin/stat -f '%Su' /dev/console)
  if [ "$console_user" != "$(id -un)" ] || ! launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
    echo "::error::Compose UI tests need an active GUI login owned by the runner user. Run the agent as that user's LaunchAgent, not a system LaunchDaemon; keep the console session logged in."
    exit 1
  fi
fi
for tool in bash sha256sum timeout find sed grep jq openssl; do
  command -v "$tool" >/dev/null || { echo "::error::Missing CI tool: $tool"; exit 1; }
done
echo "macOS CI tools ready on ${RUNNER_ARCH:-$(uname -m)}"
