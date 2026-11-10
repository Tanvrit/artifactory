"""Offline orchestration tests. Never run APT, native imports or package scripts."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DIRECTORY = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("job_local_native", DIRECTORY / "prepare.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
PRIVATE = "private-token-or-repository-url-never-log"


def result(code=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def plan_text(name="libgl1", version="1.7.0-1build1", arch="arm64"):
    return f"Inst {name} ({version} Ubuntu:24.04/noble [{arch}])\n"


class PrepareJobLocalTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.runner = Mock()
        self.runner.environment = {"RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(self.root / "environment")}
        (self.root / "environment").write_text("")

    def test_wrong_host_refuses_before_any_apt_or_native_operation(self):
        for system, arch in [("Darwin", "arm64"), ("Linux", "x86_64")]:
            with patch.object(PREPARE.platform, "system", return_value=system), \
                    patch.object(PREPARE.platform, "machine", return_value=arch):
                with self.assertRaises(PREPARE.PreparationError):
                    PREPARE.require_supported_host(self.runner)
        self.runner.run.assert_not_called()

    def test_ubuntu_version_and_dpkg_architecture_are_both_fenced(self):
        with patch.object(PREPARE.platform, "system", return_value="Linux"), \
                patch.object(PREPARE.platform, "machine", return_value="aarch64"), \
                patch.object(Path, "read_text", return_value='ID=ubuntu\nVERSION_ID="24.04"'), \
                patch.object(self.runner, "run", return_value=result(stdout="amd64")):
            with self.assertRaisesRegex(PREPARE.PreparationError, "dpkg_arm64_required"):
                PREPARE.require_supported_host(self.runner)

    def test_existing_runtime_is_noop_without_creating_prefix_or_export(self):
        with patch.object(PREPARE, "Runner", return_value=self.runner), \
                patch.object(PREPARE, "require_supported_host"), \
                patch.object(PREPARE, "probe_imports", return_value=[]), \
                patch.object(PREPARE, "private_apt") as apt:
            report = PREPARE.prepare()
        self.assertEqual(report["mode"], "existing_runtime")
        self.assertFalse(report["jvmTestsPassed"])
        apt.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.root / "environment"])

    def test_missing_status_queries_are_exact_fixed_packages(self):
        self.runner.run.side_effect = [result(stdout="install ok installed"), result(1), result(stdout="config-files"),
                                       result(stdout="install ok installed"), result(stdout="install ok installed")]
        self.assertEqual(PREPARE.missing_packages(self.runner), ["libegl1", "libxi6"])
        self.assertEqual([c.args[0][-1] for c in self.runner.run.call_args_list], list(PREPARE.PACKAGES))
        self.assertTrue(all(c.args[0][0] == "/usr/bin/dpkg-query" for c in self.runner.run.call_args_list))

    def source_files(self, text, extension=".sources"):
        sources = self.root / "sources"
        sources.mkdir(exist_ok=True)
        (sources / ("ubuntu" + extension)).write_text(text)
        return patch.multiple(PREPARE, SOURCES_LIST=self.root / "absent.list", SOURCES_PARTS=sources)

    def test_existing_authenticated_sources_are_accepted_without_copying_or_edits(self):
        text = "Types: deb\nURIs: https://ports.ubuntu.com/ubuntu-ports\nSuites: noble\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        with self.source_files(text):
            PREPARE.check_source_security()
        self.assertEqual((self.root / "sources/ubuntu.sources").read_text(), text)

    def test_deb822_and_bracket_auth_or_time_overrides_are_refused(self):
        values = [("Types: deb\nTrusted: yes\n", ".sources"),
                  ("Types: deb\nTrusted: on\n", ".sources"),
                  ("Types: deb\nTrusted:\n yes\n", ".sources"),
                  ("Types: deb\nAllow-Insecure: true\n", ".sources"),
                  ("Types: deb\nCheck-Valid-Until: no\n", ".sources"),
                  ("deb [trusted=yes] https://private noble main\n", ".list"),
                  ("deb [trusted=on] https://private noble main\n", ".list"),
                  ("deb [trusted='yes'] https://private noble main\n", ".list"),
                  ("deb [allow-weak=yes] https://private noble main\n", ".list"),
                  ("deb [check-date=no] https://private noble main\n", ".list")]
        for text, extension in values:
            with self.subTest(text=text), self.source_files(text, extension), self.assertRaises(PREPARE.PreparationError):
                PREPARE.check_source_security()
            for file in (self.root / "sources").iterdir():
                file.unlink()

    def test_private_apt_config_disables_host_hooks_and_confines_writes(self):
        status = self.root / "host-status"
        status.write_bytes(b"inert installed-package metadata")
        prefix = self.root / "prefix"
        prefix.mkdir()
        with patch.object(PREPARE, "check_source_security"), patch.object(PREPARE, "DPKG_STATUS", status):
            environment = PREPARE.private_apt(prefix, {"APT_CONFIG": PRIVATE})
        config = Path(environment["APT_CONFIG"]).read_text()
        self.assertEqual((prefix / "state/status").read_bytes(), status.read_bytes())
        self.assertIn('Dir::Etc::parts "' + str(prefix / "etc/empty-parts") + '";', config)
        self.assertIn('Dir::Etc::main "' + str(prefix / "etc/empty.conf") + '";', config)
        for key in ["Dir::State", "Dir::State::status", "Dir::State::lists", "Dir::Cache", "Dir::Cache::archives", "Dir::Log"]:
            self.assertRegex(config, key + ' "' + str(prefix))
        for key in ["APT::Get::AllowUnauthenticated", "Acquire::AllowInsecureRepositories", "Acquire::AllowDowngradeToInsecureRepositories"]:
            self.assertIn(key + ' "false";', config)
        self.assertIn('APT::Get::Download-Only "true";', config)
        self.assertNotIn(PRIVATE, config)

    def test_apt_install_without_download_only_never_executes(self):
        with self.assertRaisesRegex(PREPARE.PreparationError, "host_install_command_refused"):
            PREPARE.apt(self.runner, ["install", "libgl1"], 10)
        self.runner.run.assert_not_called()

    def test_apt_errors_are_classified_without_raw_output(self):
        for text, reason in [("NO_PUBKEY " + PRIVATE, "authentication"), ("Permission denied " + PRIVATE, "permission"),
                             ("Failed to fetch " + PRIVATE, "network"), ("No space left " + PRIVATE, "space")]:
            self.runner.run.return_value = result(100, stderr=text)
            with self.subTest(reason=reason), self.assertRaises(PREPARE.PreparationError) as error:
                PREPARE.apt(self.runner, ["update"], 10)
            self.assertEqual(str(error.exception), "private_apt_" + reason + "_failed")
            self.assertNotIn(PRIVATE, str(error.exception))

    def test_private_lock_wait_is_bounded(self):
        self.runner.run.return_value = result(100, stderr="Could not get lock " + PRIVATE)
        with patch.object(PREPARE.time, "monotonic", side_effect=[0, 0, 9]), \
                patch.object(PREPARE.time, "sleep") as sleep:
            with self.assertRaisesRegex(PREPARE.PreparationError, "private_apt_lock_timeout"):
                PREPARE.apt(self.runner, ["update"], 10)
        sleep.assert_not_called()

    def test_plans_pin_only_complete_new_arm64_or_all_closure(self):
        plan = PREPARE.parse_plan(plan_text() + plan_text("libglvnd0:arm64"), ["libgl1"])
        self.assertEqual([p["pin"] for p in plan], ["libgl1=1.7.0-1build1", "libglvnd0:arm64=1.7.0-1build1"])
        for unsafe in ["Remv libc6 [2.39]\n", "Inst libgl1 [1.0] (2.0 Ubuntu [arm64])\n",
                       plan_text(arch="amd64"), plan_text("libgl1:arm64", arch="all"), "Conflicts: libgl1\n"]:
            with self.subTest(unsafe=unsafe), self.assertRaises(PREPARE.PreparationError):
                PREPARE.parse_plan(unsafe, ["libgl1"])

    def test_signed_metadata_requires_exact_identity_hash_size_and_architecture(self):
        plan = PREPARE.parse_plan(plan_text(), ["libgl1"])
        valid = "Package: libgl1\nVersion: 1.7.0-1build1\nArchitecture: arm64\nSHA256: " + "a" * 64 + "\nSize: 42\n"
        self.runner.run.return_value = result(stdout=valid)
        self.assertEqual(PREPARE.signed_archive_metadata(self.runner, plan)[0]["size"], 42)
        self.runner.run.return_value = result(stdout=valid + "\n" + valid)
        self.assertEqual(len(PREPARE.signed_archive_metadata(self.runner, plan)), 1)
        for invalid in [valid.replace("arm64", "amd64"), valid.replace("a" * 64, "missing"),
                        valid.replace("Size: 42", "Size: 9999999999"), valid + "\n" + valid.replace("a" * 64, "b" * 64)]:
            self.runner.run.return_value = result(stdout=invalid)
            with self.subTest(invalid=invalid), self.assertRaises(PREPARE.PreparationError):
                PREPARE.signed_archive_metadata(self.runner, plan)

    def archive_fixture(self):
        archives = self.root / "archives"
        archives.mkdir()
        data = b"synthetic archive bytes; never a real library"
        archive = archives / "fixture.deb"
        archive.write_bytes(data)
        record = {"name": "libgl1", "version": "1.0", "architecture": "arm64", "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}
        library = self.root / "lib"
        library.mkdir()
        return archives, archive, record, library

    def test_archive_hash_is_verified_before_dpkg_parser_or_extraction(self):
        archives, archive, record, library = self.archive_fixture()
        record["sha256"] = "a" * 64
        with patch.object(PREPARE, "extract_deb") as extract:
            with self.assertRaisesRegex(PREPARE.PreparationError, "signed_archive_hash_mismatch"):
                PREPARE.extract_verified_archives(self.runner, archives, [record], library)
        self.runner.run.assert_not_called()
        extract.assert_not_called()

    def test_signed_archive_identity_is_rechecked_and_only_data_stream_is_extracted(self):
        archives, archive, record, library = self.archive_fixture()
        self.runner.run.return_value = result(stdout="Package: libgl1\nVersion: 1.0\nArchitecture: arm64\n")
        with patch.object(PREPARE, "extract_deb") as extract, patch.object(PREPARE, "finish_library_links"):
            PREPARE.extract_verified_archives(self.runner, archives, [record], library)
        extract.assert_called_once()
        self.assertIn("--field", self.runner.run.call_args.args[0])
        self.runner.run.return_value = result(stdout="Package: libgl1\nVersion: 1.0\nArchitecture: amd64\n")
        with self.assertRaisesRegex(PREPARE.PreparationError, "deb_metadata_mismatch"):
            PREPARE.extract_verified_archives(self.runner, archives, [record], library)

    def test_archive_symlink_cannot_read_outside_private_cache(self):
        archives, archive, record, library = self.archive_fixture()
        target = self.root / "other"
        archive.rename(target)
        archive.symlink_to(target)
        with self.assertRaisesRegex(PREPARE.PreparationError, "unsafe_archive"):
            PREPARE.extract_verified_archives(self.runner, archives, [record], library)

    def test_loader_environment_is_exactly_preserved_and_exported_inside_job(self):
        library = self.root / "prefix/lib"
        environment = PREPARE.loader_environment({**self.runner.environment, "LD_LIBRARY_PATH": "/existing/lib"}, library)
        self.assertEqual(environment["LD_LIBRARY_PATH"], str(library) + ":/existing/lib")
        PREPARE.publish_job_environment(self.root, environment)
        self.assertEqual((self.root / "environment").read_text(), "LD_LIBRARY_PATH=" + environment["LD_LIBRARY_PATH"] + "\n")
        with self.assertRaisesRegex(PREPARE.PreparationError, "unsafe_loader_environment"):
            PREPARE.loader_environment({"LD_LIBRARY_PATH": "path\nEVIL=1"}, library)

    def test_environment_export_rejects_symlink_and_outside_job(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "file"
            outside.write_text("")
            with self.assertRaises(PREPARE.PreparationError):
                PREPARE.publish_job_environment(self.root, {"GITHUB_ENV": str(outside), "LD_LIBRARY_PATH": "safe"})
            (self.root / "environment").unlink()
            (self.root / "environment").symlink_to(outside)
            with self.assertRaises(PREPARE.PreparationError):
                PREPARE.publish_job_environment(self.root, {**self.runner.environment, "LD_LIBRARY_PATH": "safe"})
            self.assertEqual(outside.read_text(), "")

    def test_fresh_child_probe_rejects_arbitrary_data_and_uses_same_loader_env(self):
        self.runner.run.return_value = result(stdout='{"missing":[]}')
        environment = {"LD_LIBRARY_PATH": "/private/job/lib:/existing/lib"}
        self.assertEqual(PREPARE.probe_imports(self.runner, environment), [])
        self.assertIs(self.runner.run.call_args.args[2], environment)
        self.assertEqual(self.runner.run.call_args.args[0][1:4], ["-I", "-B", "-c"])
        self.runner.run.return_value = result(1, stdout=json.dumps({"missing": [PRIVATE]}))
        with self.assertRaisesRegex(PREPARE.PreparationError, "native_probe_invalid") as error:
            PREPARE.probe_imports(self.runner, environment)
        self.assertNotIn(PRIVATE, str(error.exception))

    def test_process_timeout_wrapper_is_owned_bounded_noninteractive_and_no_sudo(self):
        runner = PREPARE.Runner({})
        with patch.object(PREPARE.subprocess, "run", return_value=result()) as execute:
            runner.run(["/usr/bin/apt-get", "update"], 10)
        command = execute.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/timeout", "--signal=TERM", "--kill-after=10s"])
        self.assertEqual(execute.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("sudo", command)
        self.assertNotIn("shell", execute.call_args.kwargs)
        self.assertEqual(execute.call_args.kwargs["timeout"], 25)

    def test_unknown_error_cannot_leak_into_console(self):
        output = io.StringIO()
        with patch.object(PREPARE, "prepare", side_effect=OSError(PRIVATE)), contextlib.redirect_stdout(output):
            self.assertEqual(PREPARE.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["reason"], "unexpected_preparation_failure")
        self.assertNotIn(PRIVATE, output.getvalue())

    def full_flow(self, post_missing):
        def initialize(prefix, environment):
            (prefix / "lib").mkdir()
            return dict(environment, APT_CONFIG=str(prefix / "private.conf"))
        plan = plan_text()
        patches = [patch.object(PREPARE, "Runner", return_value=self.runner),
                   patch.object(PREPARE, "require_supported_host"),
                   patch.object(PREPARE, "probe_imports", side_effect=[["libGL.so.1"], post_missing]),
                   patch.object(PREPARE, "missing_packages", return_value=["libgl1"]),
                   patch.object(PREPARE, "private_apt", side_effect=initialize),
                   patch.object(PREPARE, "apt", side_effect=["", plan, plan, ""]),
                   patch.object(PREPARE, "signed_archive_metadata", return_value=[{"name": "libgl1"}]),
                   patch.object(PREPARE, "extract_verified_archives")]
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        return [stack.enter_context(p) for p in patches]

    def test_full_flow_downloads_only_and_exports_exact_child_verified_environment(self):
        mocks = self.full_flow([])
        report = PREPARE.prepare()
        self.assertEqual(report["mode"], "authenticated_job_local_payload")
        self.assertFalse(report["jvmTestsPassed"])
        apt_calls = mocks[5].call_args_list
        self.assertEqual(apt_calls[0].args[1], ["update"])
        for call in apt_calls[1:]:
            self.assertIn("--download-only", call.args[1])
            self.assertIn("--no-upgrade", call.args[1])
            self.assertIn("--no-remove", call.args[1])
        child_environment = mocks[2].call_args_list[-1].args[1]
        self.assertEqual((self.root / "environment").read_text(),
                         "LD_LIBRARY_PATH=" + child_environment["LD_LIBRARY_PATH"] + "\n")
        prefix = next(self.root.glob("tanvrit-jvm-native-*"))
        self.assertEqual(prefix.stat().st_mode & 0o777, 0o700)

    def test_failed_child_imports_remove_only_own_prefix_and_never_export(self):
        self.full_flow(["libEGL.so.1"])
        unrelated = self.root / "unrelated"
        unrelated.write_text("preserve")
        with self.assertRaisesRegex(PREPARE.PreparationError, "native_imports_unavailable"):
            PREPARE.prepare()
        self.assertEqual(list(self.root.glob("tanvrit-jvm-native-*")), [])
        self.assertEqual((self.root / "environment").read_text(), "")
        self.assertEqual(unrelated.read_text(), "preserve")

    def test_owned_extraction_timeout_signals_only_owned_group_and_is_bounded(self):
        process = Mock(pid=98765)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("owned", 2), None]
        with patch.object(PREPARE.os, "killpg") as kill:
            PREPARE.stop_owned(process)
        self.assertEqual([c.args[0] for c in kill.call_args_list], [98765, 98765])
        self.assertEqual([c.args[1] for c in kill.call_args_list], [PREPARE.signal.SIGTERM, PREPARE.signal.SIGKILL])
        self.assertEqual([c.kwargs["timeout"] for c in process.wait.call_args_list], [2, 2])


if __name__ == "__main__":
    unittest.main()
