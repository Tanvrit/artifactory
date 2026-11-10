import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("prepare_linux_jvm", Path(__file__).with_name("prepare.py"))
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
PRIVATE = "private-token-path-or-url-never-log"


def result(code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class PrepareLinuxJvmTest(unittest.TestCase):
    def test_wrong_os_arch_or_distro_refuses_before_package_operation(self):
        for system, machine, release in [
            ("Darwin", "arm64", ""), ("Linux", "x86_64", ""),
            ("Linux", "aarch64", 'ID=debian\nVERSION_ID="12"'),
            ("Linux", "aarch64", 'ID=ubuntu\nVERSION_ID="22.04"'),
        ]:
            with self.subTest(system=system, machine=machine, release=release), \
                    patch.object(PREPARE.platform, "system", return_value=system), \
                    patch.object(PREPARE.platform, "machine", return_value=machine), \
                    patch.object(Path, "read_text", return_value=release), \
                    patch.object(PREPARE, "run") as execute:
                with self.assertRaisesRegex(PREPARE.PreparationError, "ubuntu_24_04_arm64_required"):
                    PREPARE.require_supported_host()
                execute.assert_not_called()

    def test_actual_dpkg_architecture_is_required_as_well_as_uname(self):
        with patch.object(PREPARE.platform, "system", return_value="Linux"), \
                patch.object(PREPARE.platform, "machine", return_value="aarch64"), \
                patch.object(Path, "read_text", return_value='ID=ubuntu\nVERSION_ID="24.04"'), \
                patch.object(PREPARE, "run", return_value=result(stdout="amd64\n")):
            with self.assertRaisesRegex(PREPARE.PreparationError, "dpkg_arm64_required"):
                PREPARE.require_supported_host()

    def test_ready_host_never_asks_for_sudo_or_apt(self):
        with patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "missing_packages", return_value=[]), \
                patch.object(PREPARE, "verify_libraries") as verify, \
                patch.object(PREPARE, "privilege_prefix") as privilege, \
                patch.object(PREPARE, "apt") as apt:
            report = PREPARE.prepare()
        self.assertEqual(report["status"], "ready")
        self.assertFalse(report["jvmTestsPassed"])
        verify.assert_called_once()
        privilege.assert_not_called()
        apt.assert_not_called()

    def test_only_fixed_package_statuses_are_queried_and_partial_is_missing(self):
        with patch.object(PREPARE, "run", side_effect=[
            result(stdout="install ok installed"), result(1), result(stdout="deinstall ok config-files"),
            result(stdout="install ok installed"), result(stdout="install ok installed"),
        ]) as execute:
            self.assertEqual(PREPARE.missing_packages(), ["libegl1", "libxi6"])
        self.assertEqual([call.args[0][-1] for call in execute.call_args_list], list(PREPARE.PACKAGES))

    def test_package_query_error_is_not_assumed_missing(self):
        with patch.object(PREPARE, "run", return_value=result(2, stderr=PRIVATE)):
            with self.assertRaisesRegex(PREPARE.PreparationError, "package_status_unavailable"):
                PREPARE.missing_packages()

    def test_root_never_invokes_sudo(self):
        with patch.object(PREPARE.os, "geteuid", return_value=0), patch.object(PREPARE, "run") as execute:
            self.assertEqual(PREPARE.privilege_prefix(), [])
            execute.assert_not_called()

    def test_nonroot_requires_existing_noninteractive_sudo(self):
        with patch.object(PREPARE.os, "geteuid", return_value=1000), \
                patch.object(PREPARE.shutil, "which", return_value="/usr/bin/sudo"), \
                patch.object(PREPARE, "run", return_value=result()) as execute:
            self.assertEqual(PREPARE.privilege_prefix(), ["/usr/bin/sudo", "-n", "--"])
            self.assertEqual(execute.call_args.args[0], ["/usr/bin/sudo", "-n", "--", "true"])
        for sudo, code in [(None, 0), ("/usr/bin/sudo", 1)]:
            with patch.object(PREPARE.os, "geteuid", return_value=1000), \
                    patch.object(PREPARE.shutil, "which", return_value=sudo), \
                    patch.object(PREPARE, "run", return_value=result(code, stderr=PRIVATE)):
                with self.assertRaisesRegex(PREPARE.PreparationError, "noninteractive_sudo_required"):
                    PREPARE.privilege_prefix()

    def test_apt_lock_retry_is_bounded_and_keeps_same_command(self):
        with patch.object(PREPARE.time, "monotonic", side_effect=[0, 0, 1, 2]), \
                patch.object(PREPARE.time, "sleep") as sleep, \
                patch.object(PREPARE, "run", side_effect=[
                    result(100, stderr="E: Could not get lock /private/" + PRIVATE), result(stdout="done"),
                ]) as execute:
            self.assertEqual(PREPARE.apt([], ["update"], 10), "done")
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(execute.call_args_list[0].args[0][4:], execute.call_args_list[1].args[0][4:])
        self.assertEqual([call.args[1] for call in execute.call_args_list], [25, 23])
        sleep.assert_called_once_with(2)
        command = execute.call_args.args[0]
        self.assertEqual(command[:8], ["/usr/bin/timeout", "--signal=TERM", "--kill-after=10s", "8s",
                                      "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive",
                                      "NEEDRESTART_MODE=l", "/usr/bin/apt-get"])
        self.assertIn("DPkg::Lock::Timeout=60", command)
        self.assertIn("Acquire::Retries=2", command)
        self.assertIn("DEBIAN_FRONTEND=noninteractive", command)
        self.assertIn("NEEDRESTART_MODE=l", command)

    def test_busy_lock_eventually_fails_and_never_waits_unboundedly(self):
        with patch.object(PREPARE.time, "monotonic", side_effect=[0, 0, 9]), \
                patch.object(PREPARE.time, "sleep") as sleep, \
                patch.object(PREPARE, "run", return_value=result(100, stderr="Unable to acquire the dpkg frontend lock")) as execute:
            with self.assertRaisesRegex(PREPARE.PreparationError, "apt_lock_timeout"):
                PREPARE.apt([], ["update"], 10)
        self.assertEqual(execute.call_count, 1)
        sleep.assert_not_called()

    def test_nonlock_permission_or_network_error_is_not_retried(self):
        with patch.object(PREPARE, "run", return_value=result(100, stderr=PRIVATE)) as execute, \
                patch.object(PREPARE.time, "sleep") as sleep:
            with self.assertRaisesRegex(PREPARE.PreparationError, "apt_operation_failed"):
                PREPARE.apt([], ["update"], 10)
        self.assertEqual(execute.call_count, 1)
        sleep.assert_not_called()

    def test_privileged_timeout_owns_apt_group_and_gets_cleanup_margin(self):
        with patch.object(PREPARE.time, "monotonic", return_value=0), \
                patch.object(PREPARE, "run", return_value=result(124)) as execute:
            with self.assertRaisesRegex(PREPARE.PreparationError, "apt_operation_timed_out"):
                PREPARE.apt(["/usr/bin/sudo", "-n", "--"], ["update"], 180)
        command, outer_timeout = execute.call_args.args
        self.assertEqual(command[:7], ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout",
                                      "--signal=TERM", "--kill-after=10s", "180s"])
        self.assertEqual(outer_timeout, 195)
        self.assertNotIn("--foreground", command)
        self.assertEqual(execute.call_count, 1)

    def test_plan_accepts_only_new_dependency_closure_and_pins_versions(self):
        plan = "Inst libgl1 (1.7.0-1build1 Ubuntu:24.04/noble [arm64])\nInst libglvnd0:arm64 (1.7.0-1build1 Ubuntu:24.04/noble [arm64])\n"
        self.assertEqual(PREPARE.new_installation_plan(plan, ["libgl1"]),
                         ["libgl1=1.7.0-1build1", "libglvnd0:arm64=1.7.0-1build1"])

    def test_upgrade_removal_other_architecture_and_malformed_plans_refuse(self):
        for plan in ["Remv libc6 [2.39]\n", "Purg libgl1\n", "Conflicts: libgl1\n", "Inst libgl1 [1.0] (2.0 Ubuntu [arm64])\n",
                     "Inst libgl1:amd64 (1.0 Ubuntu [amd64])\n", "Inst libgl1 (1.0 Ubuntu [amd64])\n",
                     "Inst libgl1:arm64 (1.0 Ubuntu [all])\n", "Inst libgl1 " + PRIVATE + "\n"]:
            with self.subTest(plan=plan), self.assertRaises(PREPARE.PreparationError):
                PREPARE.new_installation_plan(plan, ["libgl1"])

    def test_plan_cannot_omit_requested_missing_package(self):
        with self.assertRaisesRegex(PREPARE.PreparationError, "requested_package_plan_incomplete"):
            PREPARE.new_installation_plan("Inst libglvnd0 (1.0 Ubuntu [arm64])\n", ["libgl1"])

    def test_full_flow_installs_only_missing_and_verifies_afterward(self):
        plan = "Inst libgl1 (1.7.0-1build1 Ubuntu:24.04/noble [arm64])\n"
        with patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "missing_packages", side_effect=[["libgl1"], ["libgl1"], []]), \
                patch.object(PREPARE, "privilege_prefix", return_value=["sudo", "-n", "--"]), \
                patch.object(PREPARE, "apt", side_effect=["", plan, plan, ""]) as apt, \
                patch.object(PREPARE, "verify_libraries") as verify:
            report = PREPARE.prepare()
        self.assertEqual(report["requestedPackages"], ["libgl1"])
        self.assertEqual(apt.call_args_list[0].args[1], ["update"])
        self.assertEqual(apt.call_args_list[1].args[1], ["--simulate", *PREPARE.INSTALL_OPTIONS, "install", "--", "libgl1"])
        exact = [*PREPARE.INSTALL_OPTIONS, "install", "--", "libgl1=1.7.0-1build1"]
        self.assertEqual(apt.call_args_list[2].args[1], ["--simulate", *exact])
        self.assertEqual(apt.call_args_list[3].args[1], ["-y", *exact])
        verify.assert_called_once()

    def test_concurrent_runner_install_is_reused_without_install_command(self):
        with patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "missing_packages", side_effect=[["libgl1"], []]), \
                patch.object(PREPARE, "privilege_prefix", return_value=[]), \
                patch.object(PREPARE, "apt", return_value="") as apt, \
                patch.object(PREPARE, "verify_libraries"):
            PREPARE.prepare()
        self.assertEqual(apt.call_count, 1)
        self.assertEqual(apt.call_args.args[1], ["update"])

    def test_still_missing_after_install_refuses(self):
        plan = "Inst libgl1 (1.0 Ubuntu [arm64])\n"
        with patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "missing_packages", return_value=["libgl1"]), \
                patch.object(PREPARE, "privilege_prefix", return_value=[]), \
                patch.object(PREPARE, "apt", side_effect=["", plan, plan, ""]):
            with self.assertRaisesRegex(PREPARE.PreparationError, "package_postverification_failed"):
                PREPARE.prepare()

    def test_final_pinned_plan_cannot_gain_a_dependency_or_upgrade(self):
        first = "Inst libgl1 (1.0 Ubuntu [arm64])\n"
        for changed in [first + "Inst libc6 (2.39 Ubuntu [arm64])\n",
                        "Inst libgl1 [0.9] (1.0 Ubuntu [arm64])\n"]:
            with self.subTest(changed=changed), \
                    patch.object(PREPARE, "require_supported_host"), \
                    patch.object(PREPARE, "missing_packages", return_value=["libgl1"]), \
                    patch.object(PREPARE, "privilege_prefix", return_value=[]), \
                    patch.object(PREPARE, "apt", side_effect=["", first, changed]) as apt:
                with self.assertRaises(PREPARE.PreparationError):
                    PREPARE.prepare()
                self.assertEqual(apt.call_count, 3)

    def test_fixed_sonames_are_actually_loaded_and_raw_errors_are_not_printed(self):
        with patch.object(PREPARE.ctypes, "CDLL", side_effect=OSError(PRIVATE)) as load:
            with self.assertRaises(PREPARE.PreparationError) as error:
                PREPARE.verify_libraries()
        self.assertEqual([call.args[0] for call in load.call_args_list], list(PREPARE.PACKAGES.values()))
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertIn("libEGL.so.1", str(error.exception))

    def test_processes_cannot_prompt_or_use_shell_and_timeouts_are_sanitized(self):
        with patch.object(PREPARE.subprocess, "run") as execute:
            PREPARE.run(["apt-get", "update"], 10)
        self.assertEqual(execute.call_args.kwargs["stdin"], PREPARE.subprocess.DEVNULL)
        self.assertNotIn("shell", execute.call_args.kwargs)
        with patch.object(PREPARE.subprocess, "run", side_effect=PREPARE.subprocess.TimeoutExpired(PRIVATE, 10)):
            with self.assertRaisesRegex(PREPARE.PreparationError, "command_timeout") as error:
                PREPARE.run(["apt-get", "update"], 10)
        self.assertNotIn(PRIVATE, str(error.exception))

    def test_unknown_error_cannot_leak_into_actions_console(self):
        output = io.StringIO()
        with patch.object(PREPARE, "prepare", side_effect=OSError(PRIVATE)), contextlib.redirect_stdout(output):
            self.assertEqual(PREPARE.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["reason"], "unexpected_preparation_failure")
        self.assertNotIn(PRIVATE, output.getvalue())


if __name__ == "__main__":
    unittest.main()
