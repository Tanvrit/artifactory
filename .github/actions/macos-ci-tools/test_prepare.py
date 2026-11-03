import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name("prepare.sh")


class PrepareMacTest(unittest.TestCase):
    def run_fixture(self, **overrides):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / "bin"
            binary.mkdir()
            fixture_sdk = root / "sdk-fixture/Library/Android/sdk"
            (fixture_sdk / "platforms").mkdir(parents=True)
            prefix = root / "prefix"
            for formula in ["bash", "coreutils", "findutils", "grep", "jq", "openssl@3"]:
                (prefix / formula / "libexec/gnubin").mkdir(parents=True)
                (prefix / formula / "bin").mkdir()
            scripts = {
                "uname": '#!/bin/sh\nif [ "$1" = -s ]; then echo "${TEST_OS:-Darwin}"; else echo arm64; fi\n',
                "brew": r"""#!/bin/sh
echo "$*" >> "$FIXTURE/brew-calls"
case "$1" in
  list) [ "$3" != "${TEST_MISSING:-}" ] || [ -f "$FIXTURE/installed-$3" ] ;;
  install)
    if [ "${TEST_BUSY:-}" = true ] &&
        { [ ! -f "$FIXTURE/busy-seen" ] || [ "${TEST_ALWAYS_BUSY:-}" = true ]; }; then
      touch "$FIXTURE/busy-seen"; echo "A brew process has already locked this package"; exit 1
    fi
    if [ "${TEST_STALE_BREW:-}" = true ] &&
        { [ ! -f "$FIXTURE/brew-updated" ] || [ "${TEST_STILL_STALE:-}" = true ]; }; then
      printf "Error: ncurses: undefined method \`compatibility_version' for #<Class:0x123>\\n"
      exit 1
    fi
    if [ "${TEST_OTHER_DSL:-}" = true ]; then
      echo "Error: ncurses: undefined method 'different_method' for #<Class:0x123>"; exit 1
    fi
    if [ "${TEST_INSTALL_FAIL:-}" = true ]; then echo "Permission denied"; exit 1; fi
    touch "$FIXTURE/installed-$2"; echo "$2" >> "$FIXTURE/installed" ;;
  update)
    if [ "${TEST_UPDATE_FAIL:-}" = true ]; then echo "Synthetic Homebrew update failed"; exit 1; fi
    touch "$FIXTURE/brew-updated" ;;
  --prefix) echo "$FIXTURE/prefix/$2" ;;
  *) exit 2 ;;
esac
""",
                "docker": '#!/bin/sh\nif [ "$1" = context ]; then echo "${TEST_DOCKER_CONTEXT:-desktop-linux}"; exit 0; fi\nif [ "${TEST_DOCKER_FAIL:-}" = true ] && [ ! -f "$FIXTURE/engine-started" ]; then exit 1; fi\nif [ "$2" = --format ]; then echo "${TEST_DOCKER_OS:-linux}"; fi\n',
                "open": '#!/bin/sh\necho "$@" >> "$FIXTURE/started"\n[ "${TEST_ENGINE_NEVER_STARTS:-}" = true ] || touch "$FIXTURE/engine-started"\n',
                "sleep": "#!/bin/sh\nexit 0\n",
                "launchctl": '#!/bin/sh\nexit 1\n',
            }
            for tool in ["sha256sum", "timeout", "jq", "openssl"]:
                scripts[tool] = "#!/bin/sh\nexit 0\n"
            if overrides.pop("TEST_COLIMA", "") == "true":
                scripts["colima"] = '#!/bin/sh\necho "colima $*" >> "$FIXTURE/started"\n[ "${TEST_ENGINE_NEVER_STARTS:-}" = true ] || touch "$FIXTURE/engine-started"\n'
            applications = root / "Applications"
            applications.mkdir()
            if overrides.pop("TEST_DOCKER_DESKTOP", "") == "true":
                (applications / "Docker.app").mkdir()
            for name, text in scripts.items():
                path = binary / name
                path.write_text(text)
                path.chmod(0o755)
            env = dict(os.environ, RUNNER_TEMP=directory, FIXTURE=directory, ANDROID_HOME=str(fixture_sdk), ANDROID_SDK_ROOT=str(fixture_sdk), PATH=os.pathsep.join([str(binary), "/usr/bin", "/bin"]), MACOS_CI_APPLICATION_DIRS=str(applications), GITHUB_PATH=str(root / "paths"), GITHUB_ENV=str(root / "environment"))
            env.update(overrides)
            result = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            files = {name: (root / name).read_text() if (root / name).exists() else "" for name in ["paths", "environment", "installed", "started", "brew-calls"]}
            return result, files

    def test_prepares_gnu_paths_and_reuses_the_existing_mac_sdk(self):
        result, files = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coreutils/libexec/gnubin", files["paths"])
        self.assertIn("ANDROID_HOME=", files["environment"])
        self.assertIn("Library/Android/sdk", files["environment"])
        self.assertEqual(files["installed"], "")

    def test_installs_only_the_missing_formula(self):
        result, files = self.run_fixture(TEST_MISSING="findutils")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files["installed"].strip(), "findutils")

    def test_waits_for_a_concurrent_homebrew_install(self):
        result, files = self.run_fixture(TEST_MISSING="findutils", TEST_BUSY="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files["installed"].strip(), "findutils")
        self.assertIn("Waiting for concurrent Homebrew", result.stdout)

    def test_does_not_retry_a_permission_failure(self):
        result, files = self.run_fixture(TEST_MISSING="findutils", TEST_INSTALL_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Permission denied", result.stdout)
        self.assertNotIn("Waiting for concurrent Homebrew", result.stdout)
        self.assertNotIn("update", files["brew-calls"].splitlines())

    def test_updates_stale_brew_once_and_retries_the_missing_formula(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_STALE_BREW="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = files["brew-calls"].splitlines()
        self.assertEqual(calls.count("update"), 1)
        self.assertEqual(calls.count("install bash"), 2)
        self.assertEqual(files["installed"].strip(), "bash")
        self.assertFalse(any(call.startswith(("upgrade", "update-reset")) for call in calls))

    def test_does_not_update_again_if_the_known_dsl_error_persists(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_STALE_BREW="true", TEST_STILL_STALE="true")
        self.assertNotEqual(result.returncode, 0)
        calls = files["brew-calls"].splitlines()
        self.assertEqual(calls.count("update"), 1)
        self.assertEqual(calls.count("install bash"), 2)
        self.assertEqual(files["installed"], "")

    def test_a_failed_update_stops_without_retrying_the_install(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_STALE_BREW="true", TEST_UPDATE_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        calls = files["brew-calls"].splitlines()
        self.assertEqual(calls.count("update"), 1)
        self.assertEqual(calls.count("install bash"), 1)
        self.assertIn("Synthetic Homebrew update failed", result.stdout)

    def test_an_unrelated_undefined_method_error_is_fatal_without_updating(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_OTHER_DSL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("update", files["brew-calls"].splitlines())
        self.assertEqual(files["brew-calls"].splitlines().count("install bash"), 1)

    def test_busy_install_still_recovers_before_the_stale_brew_retry(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_BUSY="true", TEST_STALE_BREW="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = files["brew-calls"].splitlines()
        self.assertEqual(calls.count("update"), 1)
        self.assertEqual(calls.count("install bash"), 3)
        self.assertIn("Waiting for concurrent Homebrew", result.stdout)

    def test_busy_install_keeps_its_existing_sixty_attempt_bound(self):
        result, files = self.run_fixture(TEST_MISSING="bash", TEST_BUSY="true", TEST_ALWAYS_BUSY="true")
        self.assertNotEqual(result.returncode, 0)
        calls = files["brew-calls"].splitlines()
        self.assertEqual(calls.count("install bash"), 60)
        self.assertNotIn("update", calls)
        self.assertIn("Timed out waiting for Homebrew", result.stdout)

    def test_rejects_a_non_mac_host(self):
        result, files = self.run_fixture(TEST_OS="Linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(files["installed"], "")

    def test_requires_a_running_docker_engine_when_requested(self):
        result, _ = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker CLI alone is insufficient", result.stdout)

    def test_starts_an_installed_docker_desktop_and_waits_for_it(self):
        result, files = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true", TEST_DOCKER_DESKTOP="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("-g -a", files["started"])
        self.assertIn("Docker.app", files["started"])

    def test_starts_the_colima_profile_behind_the_active_context(self):
        result, files = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true", TEST_COLIMA="true", TEST_DOCKER_CONTEXT="colima-ci")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(files["started"].strip(), "colima start --profile ci")

    def test_starts_the_default_colima_profile(self):
        result, files = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true", TEST_COLIMA="true", TEST_DOCKER_CONTEXT="colima")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(files["started"].strip(), "colima start")

    def test_fails_when_a_started_engine_never_becomes_ready(self):
        result, files = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true", TEST_DOCKER_DESKTOP="true", TEST_ENGINE_NEVER_STARTS="true", CI_DOCKER_START_TIMEOUT="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker.app", files["started"])
        self.assertIn("Docker CLI alone is insufficient", result.stdout)

    def test_does_not_start_an_engine_when_one_is_running(self):
        result, files = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_DESKTOP="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files["started"], "")

    def test_requires_linux_containers(self):
        result, _ = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_OS="windows")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Linux containers", result.stdout)

    def test_accepts_a_running_linux_docker_engine(self):
        result, _ = self.run_fixture(CI_REQUIRE_DOCKER="true")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_gui_tests_without_a_runner_gui_session(self):
        result, _ = self.run_fixture(CI_REQUIRE_GUI="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LaunchAgent", result.stdout)


if __name__ == "__main__":
    unittest.main()
