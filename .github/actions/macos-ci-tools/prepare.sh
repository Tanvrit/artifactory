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
brew_updated=false
for formula in bash coreutils findutils grep jq openssl@3; do
  if ! "$brew_bin" list --versions "$formula" >/dev/null 2>&1; then
    # Several runner registrations can share one Mac and Homebrew prefix.
    # Homebrew owns the package locks; wait only for its explicit busy errors.
    log_file=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/brew-ci.XXXXXX")
    ready=false
    for attempt in $(seq 1 60); do
      if "$brew_bin" install "$formula" > "$log_file" 2>&1; then
        cat "$log_file"
        ready=true
        break
      fi
      # An older Brew can fetch a newer ncurses formula whose DSL it cannot parse.
      # Refresh Brew/tap metadata once for this exact failure, without upgrading packages.
      if [ "$brew_updated" = false ] &&
          grep -Eq 'ncurses: undefined method [[:punct:]]compatibility_version[[:punct:]] for #<Class:' "$log_file"; then
        cat "$log_file"
        brew_updated=true
        echo "Updating Homebrew once to recognize ncurses compatibility_version"
        if ! "$brew_bin" update > "$log_file" 2>&1; then
          cat "$log_file"
          rm -f "$log_file"
          exit 1
        fi
        cat "$log_file"
        if "$brew_bin" install "$formula" > "$log_file" 2>&1; then
          cat "$log_file"
          ready=true
          break
        fi
      fi
      if "$brew_bin" list --versions "$formula" >/dev/null 2>&1; then
        ready=true
        break
      fi
      if ! grep -Eiq 'already locked|already holds|another.*Homebrew|already running' "$log_file"; then
        cat "$log_file"
        rm -f "$log_file"
        exit 1
      fi
      echo "Waiting for concurrent Homebrew provisioning: $formula (attempt $attempt/60)"
      sleep 5
    done
    if [ "$ready" != true ]; then
      cat "$log_file"
      rm -f "$log_file"
      echo "::error::Timed out waiting for Homebrew to provision $formula"
      exit 1
    fi
    rm -f "$log_file"
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

# Docker Desktop and Colima stop with a logout, reboot, update or idle quit, so
# a runner whose engine is installed but stopped would fail every Docker job
# until someone noticed. Start the engine behind the active context and wait
# for it; a host with no engine still fails with the provisioning error.
docker_ready() { docker info >/dev/null 2>&1; }
start_docker_engine() {
  local context colima_bin profile app="" dir deadline
  context=$(docker context show 2>/dev/null || true)
  colima_bin=$(command -v colima || true)
  case "$context" in
    colima|colima-*)
      [ -n "$colima_bin" ] || return 1
      profile=${context#colima}; profile=${profile#-}
      echo "Starting Colima${profile:+ profile $profile} for the Docker context $context"
      if [ -n "$profile" ]; then
        "$colima_bin" start --profile "$profile" || true
      else
        "$colima_bin" start || true
      fi
      ;;
    *)
      for dir in ${MACOS_CI_APPLICATION_DIRS:-/Applications $HOME/Applications}; do
        if [ -d "$dir/Docker.app" ]; then app="$dir/Docker.app"; break; fi
      done
      if [ -n "$app" ]; then
        echo "Starting Docker Desktop ($app) for the Docker context ${context:-default}"
        open -g -a "$app" || return 1
      elif [ -n "$colima_bin" ]; then
        echo "Starting Colima for the Docker context ${context:-default}"
        "$colima_bin" start || true
      else
        return 1
      fi
      ;;
  esac
  deadline=$(( $(date +%s) + ${CI_DOCKER_START_TIMEOUT:-240} ))
  until docker_ready; do
    [ "$(date +%s)" -lt "$deadline" ] || return 1
    echo "Waiting for the Docker engine to accept connections"
    sleep 5
  done
}
if [ "${CI_REQUIRE_DOCKER:-false}" = true ]; then
  if ! command -v docker >/dev/null 2>&1 || { ! docker_ready && ! start_docker_engine; }; then
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
