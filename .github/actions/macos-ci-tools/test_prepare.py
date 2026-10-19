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
            home = root / "home"
            (home / "Library/Android/sdk/platforms").mkdir(parents=True)
            prefix = root / "prefix"
            for formula in ["bash", "coreutils", "findutils", "gnu-sed", "grep", "jq", "openssl@3"]:
                (prefix / formula / "libexec/gnubin").mkdir(parents=True)
                (prefix / formula / "bin").mkdir()
            scripts = {
                "uname": '#!/bin/sh\nif [ "$1" = -s ]; then echo "${TEST_OS:-Darwin}"; else echo arm64; fi\n',
                "brew": '#!/bin/sh\ncase "$1" in list) [ "$3" != "${TEST_MISSING:-}" ] ;; install) echo "$2" >> "$FIXTURE/installed" ;; --prefix) echo "$FIXTURE/prefix/$2" ;; *) exit 2 ;; esac\n',
                "docker": '#!/bin/sh\n[ "${TEST_DOCKER_FAIL:-}" != true ] || exit 1\nif [ "$2" = --format ]; then echo "${TEST_DOCKER_OS:-linux}"; fi\n',
                "launchctl": '#!/bin/sh\nexit 1\n',
            }
            for tool in ["sha256sum", "timeout", "jq", "openssl"]:
                scripts[tool] = "#!/bin/sh\nexit 0\n"
            for name, text in scripts.items():
                path = binary / name
                path.write_text(text)
                path.chmod(0o755)
            env = dict(os.environ, FIXTURE=directory, HOME=str(home), PATH=str(binary) + os.pathsep + os.environ["PATH"], GITHUB_PATH=str(root / "paths"), GITHUB_ENV=str(root / "environment"))
            env.pop("ANDROID_HOME", None)
            env.pop("ANDROID_SDK_ROOT", None)
            env.update(overrides)
            result = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            files = {name: (root / name).read_text() if (root / name).exists() else "" for name in ["paths", "environment", "installed"]}
            return result, files

    def test_prepares_gnu_paths_and_reuses_the_existing_mac_sdk(self):
        result, files = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coreutils/libexec/gnubin", files["paths"])
        self.assertIn("ANDROID_HOME=", files["environment"])
        self.assertIn("Library/Android/sdk", files["environment"])
        self.assertEqual(files["installed"], "")

    def test_installs_only_the_missing_formula(self):
        result, files = self.run_fixture(TEST_MISSING="gnu-sed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files["installed"].strip(), "gnu-sed")

    def test_rejects_a_non_mac_host(self):
        result, files = self.run_fixture(TEST_OS="Linux")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(files["installed"], "")

    def test_requires_a_running_docker_engine_when_requested(self):
        result, _ = self.run_fixture(CI_REQUIRE_DOCKER="true", TEST_DOCKER_FAIL="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker CLI alone is insufficient", result.stdout)

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
