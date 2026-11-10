"""Closed runtime-profile regressions. Mock every native/APT operation."""
import ast
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
DIRECTORY = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("profile_job_local_native", DIRECTORY / "prepare.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
PRIVATE = "private-token-path-never-report"
JVM_PACKAGES = {"libgl1": "libGL.so.1", "libegl1": "libEGL.so.1", "libxi6": "libXi.so.6",
                "libxrender1": "libXrender.so.1", "libxtst6": "libXtst.so.6"}
JVM_IMPORTS = ["libGL.so.1", "libX11.so.6", "libfontconfig.so.1", "libEGL.so.1", "libstdc++.so.6",
               "libm.so.6", "libc.so.6", "ld-linux-aarch64.so.1", "libXi.so.6", "libXrender.so.1", "libXtst.so.6"]
# Pinned PW1.64.1 nativeDeps.ts Ubuntu24.04 ARM64 Chromium runtime, excluding tools/fonts/Xvfb.
CHROMIUM_PACKAGE_NAMES = ["libasound2t64", "libatk-bridge2.0-0t64", "libatk1.0-0t64", "libatspi2.0-0t64",
                          "libcairo2", "libcups2t64", "libdbus-1-3", "libdrm2", "libgbm1", "libglib2.0-0t64",
                          "libnspr4", "libnss3", "libpango-1.0-0", "libx11-6", "libxcb1", "libxcomposite1",
                          "libxdamage1", "libxext6", "libxfixes3", "libxkbcommon0", "libxrandr2"]
CHROMIUM_IMPORTS = ["libasound.so.2", "libatk-bridge-2.0.so.0", "libatk-1.0.so.0", "libatspi.so.0",
                    "libcairo.so.2", "libcups.so.2", "libdbus-1.so.3", "libdrm.so.2", "libgbm.so.1",
                    "libgio-2.0.so.0", "libglib-2.0.so.0", "libgmodule-2.0.so.0", "libgobject-2.0.so.0",
                    "libnspr4.so", "libnss3.so", "libnssutil3.so", "libsmime3.so", "libpango-1.0.so.0",
                    "libX11.so.6", "libxcb.so.1", "libXcomposite.so.1", "libXdamage.so.1", "libXext.so.6",
                    "libXfixes.so.3", "libxkbcommon.so.0", "libXrandr.so.2"]
MEASURED_OWNERS = {"libxcomposite1": "libXcomposite.so.1", "libxdamage1": "libXdamage.so.1",
                   "libxfixes3": "libXfixes.so.3", "libxrandr2": "libXrandr.so.2",
                   "libatk1.0-0t64": "libatk-1.0.so.0", "libatspi2.0-0t64": "libatspi.so.0", "libgbm1": "libgbm.so.1"}


def result(code=0, stdout=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr="")


class RuntimeProfileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.env_file = self.root / "environment"
        self.env_file.write_text("")
        self.runner = Mock()
        self.runner.environment = {"RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(self.env_file)}

    def test_default_jvm_profile_keeps_literal_packages_imports_and_child_program(self):
        packages, imports = PREPARE.runtime_profile("jvm")
        self.assertEqual(packages, JVM_PACKAGES)
        self.assertEqual(imports, JVM_IMPORTS)
        self.assertIs(packages, PREPARE.PACKAGES)
        self.assertIs(imports, PREPARE.IMPORTS)
        self.runner.run.return_value = result(stdout='{"missing":[]}')
        self.assertEqual(PREPARE.probe_imports(self.runner, self.runner.environment), [])
        self.assertEqual(self.runner.run.call_args.args[0][-1], PREPARE.CHILD_PROBE)
        self.assertEqual(PREPARE.CHILD_PROBE, PREPARE.CHILD_PROBE_TEMPLATE.replace("NAMES", repr(JVM_IMPORTS)))

    def test_default_ready_receipt_stays_exact_and_creates_no_payload(self):
        with patch.object(PREPARE, "Runner", return_value=self.runner), \
                patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "probe_imports", return_value=[]), \
                patch.object(PREPARE, "private_apt") as apt:
            report = PREPARE.prepare()
        self.assertEqual(report, {"status": "ready", "mode": "existing_runtime", "jvmTestsPassed": False})
        apt.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.env_file])

    def test_unknown_profile_refuses_before_runner_native_apt_or_environment_export(self):
        for runtime in ("", "auto", "Chromium", "chromium --install-deps", "jvm\n" + PRIVATE):
            with self.subTest(runtime=runtime), \
                    patch.object(PREPARE, "Runner") as runner, \
                    patch.object(PREPARE, "require_supported_host") as host, \
                    patch.object(PREPARE, "probe_imports") as native, \
                    patch.object(PREPARE, "private_apt") as apt, \
                    patch.object(PREPARE, "publish_job_environment") as publish:
                with self.assertRaisesRegex(PREPARE.PreparationError, "^runtime_profile_refused$"):
                    PREPARE.prepare(runtime)
                for effect in (runner, host, native, apt, publish):
                    effect.assert_not_called()
        self.assertEqual(self.env_file.read_text(), "")

    def test_chromium_is_exact_upstream_runtime_and_includes_measured_owners(self):
        packages, imports = PREPARE.runtime_profile("chromium")
        self.assertEqual(list(packages), CHROMIUM_PACKAGE_NAMES)
        self.assertEqual(imports, CHROMIUM_IMPORTS)
        self.assertEqual(len(set(imports)), 26)
        for package, soname in MEASURED_OWNERS.items():
            self.assertEqual(packages[package], soname)
        self.assertTrue(set(JVM_PACKAGES) - set(packages))
        self.assertFalse(any("font" in package or "xvfb" in package for package in packages))

    def test_chromium_missing_discovery_queries_only_its_closed_profile(self):
        missing = set(MEASURED_OWNERS)
        self.runner.run.side_effect = lambda command, timeout: result(1) if command[-1] in missing else result(stdout="install ok installed")
        self.assertEqual(PREPARE.missing_packages(self.runner, PREPARE.CHROMIUM_PACKAGES),
                         [name for name in CHROMIUM_PACKAGE_NAMES if name in missing])
        self.assertEqual([call.args[0] for call in self.runner.run.call_args_list],
                         [["/usr/bin/dpkg-query", "-W", "-f=${Status}", name] for name in CHROMIUM_PACKAGE_NAMES])

    def test_default_missing_discovery_still_queries_only_the_original_five(self):
        self.runner.run.return_value = result(stdout="install ok installed")
        self.assertEqual(PREPARE.missing_packages(self.runner), [])
        self.assertEqual([call.args[0][-1] for call in self.runner.run.call_args_list], list(JVM_PACKAGES))

    def test_fresh_chromium_child_uses_exact_imports_and_same_selected_environment(self):
        environment = {"LD_LIBRARY_PATH": "/synthetic/job/lib"}
        self.runner.run.return_value = result(stdout='{"missing":[]}')
        self.assertEqual(PREPARE.probe_imports(self.runner, environment, PREPARE.CHROMIUM_IMPORTS), [])
        command, timeout, observed_env = self.runner.run.call_args.args
        self.assertEqual(command[:4], [sys.executable, "-I", "-B", "-c"])
        self.assertEqual(timeout, 30)
        self.assertIs(observed_env, environment)
        loops = [node for node in ast.parse(command[-1]).body if isinstance(node, ast.For)]
        self.assertEqual(len(loops), 1)
        self.assertEqual(ast.literal_eval(loops[0].iter), CHROMIUM_IMPORTS)
        self.assertNotIn(PRIVATE, command[-1])

    def test_chromium_child_accepts_only_selected_names_and_consistent_exit(self):
        self.runner.run.return_value = result(1, '{"missing":["libgbm.so.1"]}')
        self.assertEqual(PREPARE.probe_imports(self.runner, {}, PREPARE.CHROMIUM_IMPORTS), ["libgbm.so.1"])
        for code, payload in ((1, {"missing": ["libGL.so.1"]}), (1, {"missing": [PRIVATE]}),
                              (0, {"missing": ["libgbm.so.1"]}), (1, {"missing": []})):
            self.runner.run.return_value = result(code, json.dumps(payload))
            with self.subTest(code=code, payload=payload), self.assertRaisesRegex(PREPARE.PreparationError, "native_probe_invalid"):
                PREPARE.probe_imports(self.runner, {}, PREPARE.CHROMIUM_IMPORTS)

    def full_flow(self, post_missing):
        def initialize(prefix, environment):
            (prefix / "lib").mkdir()
            return dict(environment, APT_CONFIG=str(prefix / "private.conf"))
        plan = "Inst libgbm1 (25.2 Ubuntu:24.04/noble [arm64])\n"
        patches = {
            "Runner": patch.object(PREPARE, "Runner", return_value=self.runner),
            "host": patch.object(PREPARE, "require_supported_host"),
            "probe": patch.object(PREPARE, "probe_imports", side_effect=[["libgbm.so.1"], post_missing]),
            "missing": patch.object(PREPARE, "missing_packages", return_value=["libgbm1"]),
            "private_apt": patch.object(PREPARE, "private_apt", side_effect=initialize),
            "apt": patch.object(PREPARE, "apt", side_effect=["", plan, plan, ""]),
            "metadata": patch.object(PREPARE, "signed_archive_metadata", return_value=[{"name": "libgbm1"}]),
            "extract": patch.object(PREPARE, "extract_verified_archives"),
        }
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return {key: stack.enter_context(value) for key, value in patches.items()}

    def test_chromium_exports_only_after_verified_fresh_child_and_never_claims_browser_tests(self):
        mocks = self.full_flow([])
        report = PREPARE.prepare("chromium")
        self.assertEqual(report, {"status": "ready", "mode": "authenticated_job_local_payload", "jvmTestsPassed": False,
                                  "requestedPackages": ["libgbm1"], "verifiedArchives": 1,
                                  "runtime": "chromium", "chromiumImportsLoaded": 26, "browserTestsPassed": False})
        mocks["missing"].assert_called_once_with(self.runner, PREPARE.CHROMIUM_PACKAGES)
        for call in mocks["probe"].call_args_list:
            self.assertEqual(call.args[2], CHROMIUM_IMPORTS)
        for call in mocks["apt"].call_args_list[1:]:
            for required in ("--download-only", "--no-upgrade", "--no-remove"):
                self.assertIn(required, call.args[1])
        child_environment = mocks["probe"].call_args_list[-1].args[1]
        self.assertEqual(self.env_file.read_text(), "LD_LIBRARY_PATH=" + child_environment["LD_LIBRARY_PATH"] + "\n")
        self.assertEqual(next(self.root.glob("tanvrit-chromium-native-*")).stat().st_mode & 0o777, 0o700)

    def test_chromium_failure_cleans_own_payload_and_never_exports_environment(self):
        self.full_flow(["libgbm.so.1"])
        other = self.root / "unrelated"
        other.write_text("preserve")
        with self.assertRaisesRegex(PREPARE.PreparationError, "native_imports_unavailable"):
            PREPARE.prepare("chromium")
        self.assertEqual(self.env_file.read_text(), "")
        self.assertEqual(list(self.root.glob("tanvrit-chromium-native-*")), [])
        self.assertEqual(other.read_text(), "preserve")

    def test_main_routes_only_fixed_runtime_env_and_preserves_default(self):
        for env, expected in (({}, "jvm"), ({"NATIVE_RUNTIME": "chromium"}, "chromium")):
            output = io.StringIO()
            with patch.dict(PREPARE.os.environ, env, clear=True), \
                    patch.object(PREPARE, "prepare", return_value={"status": "ready", "jvmTestsPassed": False}) as prepare, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(PREPARE.main(), 0)
            prepare.assert_called_once_with(expected)

    def test_main_unknown_runtime_refuses_without_raw_input_or_side_effects(self):
        output = io.StringIO()
        with patch.dict(PREPARE.os.environ, {"NATIVE_RUNTIME": PRIVATE}, clear=True), \
                patch.object(PREPARE, "Runner") as runner, contextlib.redirect_stdout(output):
            self.assertEqual(PREPARE.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "refused", "reason": "runtime_profile_refused", "jvmTestsPassed": False})
        self.assertNotIn(PRIVATE, output.getvalue())
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
