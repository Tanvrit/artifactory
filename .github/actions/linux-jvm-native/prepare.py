#!/usr/bin/env python3
"""Narrow, idempotent Ubuntu 24.04 ARM64 prerequisites for offscreen JVM tests.

No display server, graphics driver, repository, package upgrade, removal or
reboot is requested. Apt uses its existing signed repositories and locks. Only
missing fixed packages and their required new dependencies may be installed.
"""

import ctypes
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time


# Ubuntu noble/arm64 official file lists verify these exact five SONAME owners.
PACKAGES = {
    "libgl1": "libGL.so.1",
    "libegl1": "libEGL.so.1",
    "libxi6": "libXi.so.6",
    "libxrender1": "libXrender.so.1",
    "libxtst6": "libXtst.so.6",
}
APT_OPTIONS = [
    "-o", "DPkg::Lock::Timeout=60", "-o", "Acquire::Retries=2",
    "-o", "Acquire::http::Timeout=30", "-o", "Acquire::https::Timeout=30",
    "-o", "APT::Update::Error-Mode=any",
]
INSTALL_OPTIONS = ["--no-install-recommends", "--no-upgrade", "--no-remove"]
LOCK_FAILURE = re.compile(r"(?:Could not get lock|Unable to acquire.*lock|Unable to lock directory)", re.I)


class PreparationError(Exception):
    """Only fixed reason codes from this type may reach the Actions console."""


def run(command, timeout):
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise PreparationError("command_timeout") from None
    except OSError:
        raise PreparationError("command_unavailable") from None


def require_supported_host():
    if platform.system() != "Linux" or platform.machine() not in {"aarch64", "arm64"}:
        raise PreparationError("ubuntu_24_04_arm64_required")
    release = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"ID", "VERSION_ID"}:
                release[key] = value.strip().strip('"\'')
    except OSError:
        raise PreparationError("os_release_unavailable") from None
    if release != {"ID": "ubuntu", "VERSION_ID": "24.04"}:
        raise PreparationError("ubuntu_24_04_arm64_required")
    result = run(["dpkg", "--print-architecture"], 10)
    if result.returncode or result.stdout.strip() != "arm64":
        raise PreparationError("dpkg_arm64_required")


def missing_packages():
    missing = []
    for package in PACKAGES:
        result = run(["dpkg-query", "-W", "-f=${Status}", package], 10)
        if result.returncode not in {0, 1}:
            raise PreparationError("package_status_unavailable")
        if result.returncode or result.stdout.strip() != "install ok installed":
            missing.append(package)
    return missing


def verify_libraries():
    missing = []
    for name in PACKAGES.values():
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    if missing:
        # These are fixed names, never a raw loader exception or arbitrary path.
        raise PreparationError("runtime_library_verification_failed:" + ",".join(missing))


def privilege_prefix():
    if os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if sudo is None:
        raise PreparationError("noninteractive_sudo_required")
    result = run([sudo, "-n", "--", "true"], 10)
    if result.returncode:
        raise PreparationError("noninteractive_sudo_required")
    return [sudo, "-n", "--"]


def apt(prefix, arguments, timeout):
    # Apt owns the host's package locks. DPkg's lock wait is bounded explicitly;
    # older apt list locks fail immediately, so only those specific busy errors
    # get another bounded attempt. Permission/network/package errors do not.
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreparationError("apt_lock_timeout")
        # The privileged GNU timeout owns the apt process group, including dpkg.
        # Putting it inside sudo also works when sudo uses a separate PTY/session;
        # runner-user signals alone cannot reliably retire root descendants.
        # Do not use --foreground, which would exclude command descendants.
        command = [*prefix, "/usr/bin/timeout", "--signal=TERM", "--kill-after=10s",
                   str(math.ceil(remaining)) + "s", "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive",
                   "NEEDRESTART_MODE=l", "/usr/bin/apt-get", *APT_OPTIONS, *arguments]
        result = run(command, remaining + 15)
        if result.returncode in {124, 137, -9}:
            raise PreparationError("apt_operation_timed_out")
        if result.returncode == 0:
            return result.stdout
        if not LOCK_FAILURE.search(result.stdout + "\n" + result.stderr):
            raise PreparationError("apt_operation_failed")
        if deadline - time.monotonic() <= 2:
            raise PreparationError("apt_lock_timeout")
        time.sleep(2)


def new_installation_plan(output, requested):
    """Reject upgrades/removals before any package installation mutation."""
    pinned = []
    names = set()
    for line in output.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            raise PreparationError("package_removal_refused")
        if line.startswith(("Conflicts:", "Conflicts ")):
            raise PreparationError("package_conflict_refused")
        if not line.startswith("Inst "):
            continue
        # Existing packages have [old-version] before (new-version); that form
        # must never be accepted as a fresh installation. Pin the complete new
        # dependency closure from the checked plan for the actual invocation.
        match = re.fullmatch(
            r"Inst ([a-z0-9][a-z0-9+.-]*(?::(?:arm64|all))?) "
            r"\(([A-Za-z0-9.+:~_-]{1,100}) [^\r\n]*\[(arm64|all)\]\)", line)
        if match is None:
            raise PreparationError("package_upgrade_or_unknown_plan_refused")
        name, version, architecture = match.groups()
        base, separator, suffix = name.partition(":")
        if separator and suffix != architecture:
            raise PreparationError("package_architecture_mismatch_refused")
        if base in names:
            raise PreparationError("duplicate_package_plan_refused")
        names.add(base)
        pinned.append(name + "=" + version)
    if pinned and not set(requested).issubset({name.split(":", 1)[0] for name in names}):
        raise PreparationError("requested_package_plan_incomplete")
    return pinned


def prepare():
    require_supported_host()
    missing = missing_packages()
    if not missing:
        verify_libraries()
        return {"status": "ready", "requestedPackages": [], "jvmTestsPassed": False}
    prefix = privilege_prefix()
    apt(prefix, ["update"], 180)
    # Another runner may have finished the same bounded provisioning meanwhile.
    missing = missing_packages()
    if not missing:
        verify_libraries()
        return {"status": "ready", "requestedPackages": [], "jvmTestsPassed": False}
    simulated = apt(prefix, ["--simulate", *INSTALL_OPTIONS, "install", "--", *missing], 60)
    pinned = new_installation_plan(simulated, missing)
    if pinned:
        # Recheck the exact pinned arguments that will be applied, not only the
        # initial unpinned discovery. Every dependency is now explicit, so the
        # no-upgrade switch also protects that complete new dependency closure.
        checked = new_installation_plan(apt(
            prefix, ["--simulate", *INSTALL_OPTIONS, "install", "--", *pinned], 60), missing)
        if sorted(checked) != sorted(pinned):
            raise PreparationError("package_plan_changed_refused")
        apt(prefix, ["-y", *INSTALL_OPTIONS, "install", "--", *pinned], 240)
    if missing_packages():
        raise PreparationError("package_postverification_failed")
    verify_libraries()
    return {"status": "ready", "requestedPackages": missing,
            "plannedNewPackages": len(pinned), "jvmTestsPassed": False}


def main():
    try:
        report = prepare()
    except PreparationError as error:
        print(json.dumps({"status": "refused", "reason": str(error), "jvmTestsPassed": False}))
        return 1
    except Exception:
        print(json.dumps({"status": "refused", "reason": "unexpected_preparation_failure",
                          "jvmTestsPassed": False}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
