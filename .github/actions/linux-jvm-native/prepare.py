#!/usr/bin/env python3
"""Authenticated job-local native libraries; no sudo, host install or scripts."""

import hashlib
import json
import math
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from payload import PayloadError, extract_tar_stream, finish_library_links

PACKAGES = {"libgl1": "libGL.so.1", "libegl1": "libEGL.so.1", "libxi6": "libXi.so.6",
            "libxrender1": "libXrender.so.1", "libxtst6": "libXtst.so.6"}
SKIKO_IMPORTS = ["libGL.so.1", "libX11.so.6", "libfontconfig.so.1", "libEGL.so.1",
                 "libstdc++.so.6", "libm.so.6", "libc.so.6", "ld-linux-aarch64.so.1"]
IMPORTS = list(dict.fromkeys([*SKIKO_IMPORTS, *PACKAGES.values()]))
PACKAGE_FLAGS = ["-y", "--download-only", "--no-install-recommends", "--no-upgrade", "--no-remove"]
MAX_ARCHIVES = 32
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVES_BYTES = 128 * 1024 * 1024
MAX_STATUS_BYTES = 32 * 1024 * 1024
DPKG_STATUS = Path("/var/lib/dpkg/status")
SOURCES_LIST = Path("/etc/apt/sources.list")
SOURCES_PARTS = Path("/etc/apt/sources.list.d")
LOCK_FAILURE = re.compile(r"Could not get lock|Unable to acquire.*lock|Unable to lock directory", re.I)
CHILD_PROBE = """import ctypes,json
missing=[]
for name in NAMES:
 try: ctypes.CDLL(name)
 except OSError: missing.append(name)
print(json.dumps({'missing':missing}))
raise SystemExit(bool(missing))
""".replace("NAMES", repr(IMPORTS))


class PreparationError(Exception):
    pass


class Runner:
    def __init__(self, environment=None):
        self.environment = dict(os.environ if environment is None else environment)
        self.deadline = time.monotonic() + 600

    def bounded(self, command, timeout):
        duration = min(timeout, self.deadline - time.monotonic() - 15)
        if duration <= 0:
            raise PreparationError("preparation_deadline")
        return (["/usr/bin/timeout", "--signal=TERM", "--kill-after=10s", str(math.ceil(duration)) + "s", *command],
                duration + 15)

    def run(self, command, timeout=30, environment=None):
        command, limit = self.bounded(command, timeout)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=limit, check=False,
                                    env=self.environment if environment is None else environment)
        except (OSError, subprocess.TimeoutExpired):
            raise PreparationError("bounded_command_failed") from None
        if result.returncode in {124, 137, -9}:
            raise PreparationError("bounded_command_timeout")
        if len(result.stdout) + len(result.stderr) > 4 * 1024 * 1024:
            raise PreparationError("command_output_limit")
        return result


def require_supported_host(runner):
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        raise PreparationError("ubuntu_24_04_arm64_required")
    fields = parse_fields(Path("/etc/os-release").read_text().replace("=", ": "))
    if fields.get("ID", "").strip('"') != "ubuntu" or fields.get("VERSION_ID", "").strip('"') != "24.04":
        raise PreparationError("ubuntu_24_04_arm64_required")
    result = runner.run(["/usr/bin/dpkg", "--print-architecture"], 10)
    if result.returncode or result.stdout.strip() != "arm64":
        raise PreparationError("dpkg_arm64_required")


def parse_fields(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_-]+):[ \t]*(.*)", line)
        if match:
            if match[1] in result:
                raise PreparationError("duplicate_metadata_field")
            result[match[1]] = match[2]
    return result


def missing_packages(runner):
    missing = []
    for name in PACKAGES:
        result = runner.run(["/usr/bin/dpkg-query", "-W", "-f=${Status}", name], 10)
        if result.returncode not in {0, 1}:
            raise PreparationError("package_status_unavailable")
        if result.returncode or result.stdout.strip() != "install ok installed":
            missing.append(name)
    return missing


def probe_imports(runner, environment):
    result = runner.run([sys.executable, "-I", "-B", "-c", CHILD_PROBE], 30, environment)
    try:
        report = json.loads(result.stdout)
        if set(report) != {"missing"} or not isinstance(report["missing"], list):
            raise ValueError()
        if any(name not in IMPORTS for name in report["missing"]):
            raise ValueError()
        if bool(result.returncode) != bool(report["missing"]):
            raise ValueError()
        return report["missing"]
    except (ValueError, TypeError):
        raise PreparationError("native_probe_invalid") from None


def check_source_security():
    sources = [SOURCES_LIST]
    directory = SOURCES_PARTS
    if directory.is_dir():
        sources += sorted(p for p in directory.iterdir() if p.suffix in {".list", ".sources"})
    found = False
    for source in sources:
        if not source.exists():
            continue
        data = source.read_text()
        if len(data) > 1024 * 1024:
            raise PreparationError("source_configuration_limit")
        data = "\n".join(line.split("#", 1)[0] for line in data.splitlines())
        data = re.sub(r"\n[ \t]+", " ", data)
        for line in data.splitlines():
            found = found or bool(line.strip())
            # Refuse source-level overrides rather than trying to duplicate APT's
            # boolean vocabulary (which also accepts forms such as 'on').
            if re.search(r"\b(?:trusted|allow-insecure|allow-weak|allow-downgrade-to-insecure)\s*[:=]", line, re.I):
                raise PreparationError("source_authentication_override_refused")
            if re.search(r"\b(?:check-valid-until|check-date|valid-until-min|valid-until-max|date-max-future|date-max-past)\s*[:=]", line, re.I):
                raise PreparationError("source_date_override_refused")
    if not found:
        raise PreparationError("existing_apt_sources_required")


def clean_path(value):
    if not value or not Path(value).is_absolute() or any(char in value for char in ('\n', '\r', '\x00')):
        raise PreparationError("unsafe_job_path")
    return Path(value)


def apt_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def private_apt(prefix, environment):
    check_source_security()
    for relative in ("state/lists/partial", "cache/archives/partial", "log", "etc/empty-parts", "lib", "tmp"):
        (prefix / relative).mkdir(parents=True, mode=0o700, exist_ok=True)
    with DPKG_STATUS.open("rb") as file:
        snapshot = file.read(MAX_STATUS_BYTES + 1)
    if len(snapshot) > MAX_STATUS_BYTES:
        raise PreparationError("status_snapshot_limit")
    (prefix / "state/status").write_bytes(snapshot)
    (prefix / "etc/empty.conf").write_text("")
    values = {
        "Dir::Etc::parts": prefix / "etc/empty-parts", "Dir::Etc::main": prefix / "etc/empty.conf",
        "Dir::Etc::sourcelist": SOURCES_LIST, "Dir::Etc::sourceparts": SOURCES_PARTS,
        "Dir::Etc::trusted": "/etc/apt/trusted.gpg", "Dir::Etc::trustedparts": "/etc/apt/trusted.gpg.d",
        "Dir::State": prefix / "state", "Dir::State::status": prefix / "state/status",
        "Dir::State::lists": prefix / "state/lists", "Dir::State::extended_states": prefix / "state/extended_states",
        "Dir::Cache": prefix / "cache", "Dir::Cache::archives": prefix / "cache/archives",
        "Dir::Cache::pkgcache": "", "Dir::Cache::srcpkgcache": "", "Dir::Log": prefix / "log",
        "APT::Architecture": "arm64", "APT::Get::Download-Only": "true",
        "APT::Get::AllowUnauthenticated": "false", "Acquire::AllowInsecureRepositories": "false",
        "Acquire::AllowWeakRepositories": "false",
        "Acquire::AllowDowngradeToInsecureRepositories": "false", "Acquire::Check-Date": "true",
        "Acquire::Check-Valid-Until": "true", "APT::Update::Error-Mode": "any", "Acquire::Languages": "none",
        "Acquire::Retries": "2", "Acquire::http::Timeout": "30", "Acquire::https::Timeout": "30",
        "APT::Sandbox::User": pwd.getpwuid(os.geteuid()).pw_name,
    }
    config = prefix / "etc/apt.conf"
    config.write_text("\n".join(key + " " + apt_quote(value) + ";" for key, value in values.items())
                      + '\nAPT::Architectures { "arm64"; };\n')
    return dict(environment, APT_CONFIG=str(config), DEBIAN_FRONTEND="noninteractive", LC_ALL="C", TMPDIR=str(prefix / "tmp"))


def apt_reason(output):
    for reason, pattern in [
        ("authentication", r"NO_PUBKEY|not signed|unauthenticated|signature.*(?:invalid|couldn't|could not)|Hash Sum mismatch"),
        ("time_validation", r"expired|not valid yet"),
        ("permission", r"Permission denied|permission to access"),
        ("space", r"No space left|not enough free space"),
        ("network", r"Could not resolve|Failed to fetch|Could not connect|Connection timed out"),
        ("dependencies", r"unmet dependencies|Unable to correct problems|no installation candidate|Unable to locate package"),
    ]:
        if re.search(pattern, output, re.I):
            return reason
    return "operation"


def apt(runner, arguments, timeout):
    if "install" in arguments and "--download-only" not in arguments:
        raise PreparationError("host_install_command_refused")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreparationError("private_apt_lock_timeout")
        result = runner.run(["/usr/bin/apt-get", *arguments], remaining)
        if result.returncode == 0:
            return result.stdout
        output = result.stdout + "\n" + result.stderr
        if not LOCK_FAILURE.search(output):
            raise PreparationError("private_apt_" + apt_reason(output) + "_failed")
        if deadline - time.monotonic() <= 2:
            raise PreparationError("private_apt_lock_timeout")
        time.sleep(2)


def parse_plan(output, requested):
    plan = []
    names = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(("Remv ", "Purg ", "Conflicts:", "Conflicts ")):
            raise PreparationError("unsafe_package_plan")
        if not line.startswith("Inst "):
            continue
        match = re.fullmatch(r"Inst ([a-z0-9][a-z0-9+.-]*(?::(?:arm64|all))?) "
                             r"\(([A-Za-z0-9.+:~_-]{1,100}) [^\r\n]*\[(arm64|all)\]\)", line)
        if match is None:
            raise PreparationError("upgrade_or_foreign_plan_refused")
        name, version, architecture = match.groups()
        base, separator, suffix = name.partition(":")
        if base in names or (separator and suffix != architecture):
            raise PreparationError("ambiguous_package_plan")
        names.add(base)
        plan.append({"name": base, "version": version, "architecture": architecture, "pin": name + "=" + version})
    if len(plan) > MAX_ARCHIVES or not set(requested).issubset(names):
        raise PreparationError("package_plan_incomplete_or_large")
    return plan


def signed_archive_metadata(runner, plan):
    records = []
    for expected in plan:
        result = runner.run(["/usr/bin/apt-cache", "--no-all-versions", "show", expected["pin"]], 15)
        if result.returncode:
            raise PreparationError("signed_package_metadata_unavailable")
        candidates = [parse_fields(part) for part in result.stdout.strip().split("\n\n")]
        candidates = [p for p in candidates if p.get("Package") == expected["name"]
                      and p.get("Version") == expected["version"] and p.get("Architecture") == expected["architecture"]]
        unique = {tuple(p.get(key) for key in ("Package", "Version", "Architecture", "Size", "SHA256")) for p in candidates}
        if len(unique) != 1:
            raise PreparationError("signed_package_metadata_ambiguous")
        value = candidates[0]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value.get("SHA256", "")) or not value.get("Size", "").isdigit():
            raise PreparationError("signed_package_digest_required")
        size = int(value["Size"])
        if not 0 < size <= MAX_ARCHIVE_BYTES:
            raise PreparationError("archive_size_limit")
        records.append({**expected, "size": size, "sha256": value["SHA256"].lower()})
    if sum(p["size"] for p in records) > MAX_ARCHIVES_BYTES:
        raise PreparationError("archive_total_limit")
    return records


def stop_owned(process):
    if process.poll() is not None:
        return
    for sig, wait in [(signal.SIGTERM, 2), (signal.SIGKILL, 2)]:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            pass
    raise PreparationError("owned_extraction_cleanup_failed")


def extract_deb(runner, archive, library, seen):
    command, timeout = runner.bounded(["/usr/bin/dpkg-deb", "--fsys-tarfile", str(archive)], 30)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=runner.environment, start_new_session=True)
    try:
        extract_tar_stream(process.stdout, library, seen)
        process.stdout.close()
        if process.wait(timeout=timeout):
            raise PreparationError("deb_data_stream_failed")
    finally:
        process.stdout.close()
        stop_owned(process)


def extract_verified_archives(runner, directory, records, library):
    archives = sorted(directory.glob("*.deb"))
    if len(archives) != len(records):
        raise PreparationError("archive_set_mismatch")
    matched = set()
    seen = set()
    for archive in archives:
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PreparationError("unsafe_archive")
        digest = hashlib.sha256()
        with archive.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        matches = [p for p in records if p["size"] == archive.stat().st_size and p["sha256"] == digest.hexdigest()]
        if len(matches) != 1 or matches[0]["name"] in matched:
            raise PreparationError("signed_archive_hash_mismatch")
        expected = matches[0]
        result = runner.run(["/usr/bin/dpkg-deb", "--field", str(archive), "Package", "Version", "Architecture"], 15)
        actual = parse_fields(result.stdout)
        if result.returncode or actual != {"Package": expected["name"], "Version": expected["version"], "Architecture": expected["architecture"]}:
            raise PreparationError("deb_metadata_mismatch")
        matched.add(expected["name"])
        extract_deb(runner, archive, library, seen)
    finish_library_links(library)


def loader_environment(environment, library):
    existing = environment.get("LD_LIBRARY_PATH", "")
    if any(char in existing for char in ('\n', '\r', '\x00')):
        raise PreparationError("unsafe_loader_environment")
    path = str(library) + (":" + existing if existing else "")
    return dict(environment, LD_LIBRARY_PATH=path)


def publish_job_environment(temporary, environment):
    target = clean_path(environment.get("GITHUB_ENV"))
    if target.is_symlink():
        raise PreparationError("unsafe_environment_file")
    try:
        target.resolve(strict=True).relative_to(temporary)
    except (OSError, ValueError):
        raise PreparationError("environment_file_outside_job") from None
    descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise PreparationError("unsafe_environment_file")
    with os.fdopen(descriptor, "w") as file:
        file.write("LD_LIBRARY_PATH=" + environment["LD_LIBRARY_PATH"] + "\n")


def prepare():
    runner = Runner()
    require_supported_host(runner)
    if not probe_imports(runner, runner.environment):
        return {"status": "ready", "mode": "existing_runtime", "jvmTestsPassed": False}
    missing = missing_packages(runner)
    if not missing:
        raise PreparationError("installed_package_runtime_inconsistent")
    temporary = clean_path(runner.environment.get("RUNNER_TEMP")).resolve(strict=True)
    prefix = Path(tempfile.mkdtemp(prefix="tanvrit-jvm-native-", dir=temporary))
    prefix.chmod(0o700)
    try:
        runner.environment = private_apt(prefix, runner.environment)
        apt(runner, ["update"], 180)
        plan = parse_plan(apt(runner, ["--simulate", *PACKAGE_FLAGS, "install", "--", *missing], 60), missing)
        pins = [p["pin"] for p in plan]
        second = parse_plan(apt(runner, ["--simulate", *PACKAGE_FLAGS, "install", "--", *pins], 60), missing)
        if sorted(plan, key=lambda p: p["name"]) != sorted(second, key=lambda p: p["name"]):
            raise PreparationError("package_plan_changed")
        records = signed_archive_metadata(runner, plan)
        apt(runner, [*PACKAGE_FLAGS, "install", "--", *pins], 240)
        library = prefix / "lib"
        extract_verified_archives(runner, prefix / "cache/archives", records, library)
        child_environment = loader_environment(runner.environment, library)
        remaining = probe_imports(runner, child_environment)
        if remaining:
            raise PreparationError("native_imports_unavailable:" + ",".join(remaining))
        publish_job_environment(temporary, child_environment)
        return {"status": "ready", "mode": "authenticated_job_local_payload",
                "requestedPackages": missing, "verifiedArchives": len(records),
                "skikoImportsLoaded": len(SKIKO_IMPORTS), "awtSystemImportsLoaded": 3, "jvmTestsPassed": False}
    except Exception:
        shutil.rmtree(prefix)
        raise


def main():
    previous = os.umask(0o077)
    try:
        report = prepare()
    except (PreparationError, PayloadError) as error:
        print(json.dumps({"status": "refused", "reason": str(error), "jvmTestsPassed": False}))
        return 1
    except Exception:
        print(json.dumps({"status": "refused", "reason": "unexpected_preparation_failure", "jvmTestsPassed": False}))
        return 1
    finally:
        os.umask(previous)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
