#!/usr/bin/env python3
"""Opt-in signing; no credentials in HOME, arguments/logs, outputs or artifacts.

Only the temporary keychain password and P12 password reach security's required
arguments. Child build scripts receive neither signing secret environment value.
All subprocess diagnostics are captured: failures report a fixed stage + status.
"""

import base64
import contextlib
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from provision import (ApiFailure, AppStoreClient, ProvisioningApiError, ProvisioningError, application_profile,
                       authentication_token, install_profile)
from diagnostics import CleanupStage, Diagnostics, Stage
from xcode_diagnostics import XcodeFailureKind, classify


SECRET_NAMES = (
    "IOS_DISTRIBUTION_CERTIFICATE_BASE64", "IOS_P12_PASSWORD",
    "APPSTORE_CONNECT_PRIVATE_KEY", "APPSTORE_CONNECT_KEY_ID",
    "APPSTORE_CONNECT_ISSUER_ID", "APPLE_TEAM_ID",
)


class SigningError(Exception):
    pass


class IpaFailureKind(Enum):
    UNPACK = "ipa-unpack"
    APPLICATION = "ipa-main-application"
    BUNDLE = "ipa-bundle-identity"
    SIGNATURE = "ipa-strict-signature"
    CERTIFICATE = "ipa-distribution-certificate"
    ENTITLEMENTS = "ipa-entitlements-read"
    APP_IDENTIFIER = "ipa-application-identifier"
    TEAM = "ipa-team-identifier"
    DEBUG = "ipa-get-task-allow"


class IpaFailure(SigningError):
    def __init__(self, kind):
        super().__init__("IPA verification failed; private details withheld")
        self.kind = kind


@contextlib.contextmanager
def ipa_check(kind):
    try:
        yield
    except Exception:
        raise IpaFailure(kind) from None


class XcodeFailure(SigningError):
    def __init__(self, kind):
        super().__init__("Xcode failed; captured output withheld")
        self.kind = kind


def run(stage, args, env, timeout=180):
    """Never let a CalledProcessError render secret-bearing argv or output."""
    process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    if process.returncode:
        if args[0] == "xcodebuild" and stage in ("Archive application", "Export signed IPA"):
            raise XcodeFailure(classify(stdout, stderr))
        raise SigningError(f"{stage} failed (exit {process.returncode}); no signing diagnostics emitted")
    return stdout


def required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise SigningError(f"Missing {name} for explicitly enabled portable signing")
    return value


def configuration(env):
    for name in SECRET_NAMES[:-1]:
        required(env, name)
    team = required(env, "PORTABLE_IOS_TEAM_ID")
    if env.get("APPLE_TEAM_ID") and env["APPLE_TEAM_ID"].strip() != team:
        raise SigningError("APPLE_TEAM_ID secret and expected caller team disagree")
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise SigningError("Expected Apple team must contain ten uppercase letters/digits")
    if not re.fullmatch(r"[A-Z0-9]{10}", env["APPSTORE_CONNECT_KEY_ID"]):
        raise SigningError("App Store Connect key ID has invalid shape")
    try:
        uuid.UUID(env["APPSTORE_CONNECT_ISSUER_ID"])
    except ValueError:
        raise SigningError("App Store Connect issuer must be a UUID")
    if required(env, "PORTABLE_IOS_EXPORT_METHOD") not in ("app-store", "app-store-connect"):
        raise SigningError("Portable distribution certificate path supports App Store export only")
    bundle = required(env, "PORTABLE_IOS_BUNDLE_ID")
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle):
        raise SigningError("Expected bundle identifier has invalid shape")
    return team, bundle


@contextlib.contextmanager
def signing_lock(home, timeout=600):
    """Serialize this action's keychain search-list changes across runner agents."""
    path = home / "Library/Caches/tanvrit-portable-ios-signing.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SigningError("Another portable iOS signer holds this user's keychain lock")
                time.sleep(0.25)
        yield


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def application_profile_settings(profile):
    # Xcode expands nested settings per target. Resource bundles/frameworks
    # resolve an unset suffix to empty instead of receiving an app-only profile.
    return [
        "PROVISIONING_PROFILE_SPECIFIER=$(PORTABLE_PROFILE_$(WRAPPER_EXTENSION))",
        "PROVISIONING_PROFILE=$(PORTABLE_PROFILE_UUID_$(WRAPPER_EXTENSION))",
        f"PORTABLE_PROFILE_app={profile['Name']}",
        f"PORTABLE_PROFILE_UUID_app={profile['UUID']}",
    ]


def verify_settings(raw, expected):
    rows = json.loads(raw)
    applications = [row["buildSettings"] for row in rows
                    if row.get("buildSettings", {}).get("WRAPPER_EXTENSION") == "app"
                    and row["buildSettings"].get("SKIP_INSTALL") != "YES"]
    if (len(applications) != 1 or applications[0].get("PRODUCT_BUNDLE_IDENTIFIER") != expected
            or any(row.get("buildSettings", {}).get("WRAPPER_EXTENSION") == "appex" for row in rows)):
        raise SigningError("Resolved application identity does not match caller bundle_id; project was not changed")
    return applications[0]


def unpack_ipa(ipa, unpacked, execute, child_env):
    # ditto preserves executable modes and framework links needed by codesign.
    # Reject traversal and link-parent writes before invoking native extraction.
    with zipfile.ZipFile(ipa) as archive:
        links = set()
        paths = []
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise SigningError("IPA archive contains an unsafe entry path")
            paths.append(path)
            if stat.S_ISLNK(info.external_attr >> 16):
                if not 0 < info.file_size <= 4096:
                    raise SigningError("IPA archive contains an unsafe link")
                target = archive.read(info).decode("utf-8", errors="strict")
                value = PurePosixPath(target)
                if not value.parts or value.is_absolute() or ".." in value.parts or "\\" in target:
                    raise SigningError("IPA archive contains an unsafe link")
                links.add(path)
        if any(parent in links for path in paths for parent in path.parents):
            raise SigningError("IPA archive writes through a link")
    unpacked.mkdir()
    execute("Unpack exported IPA", ["ditto", "-x", "-k", str(ipa), str(unpacked)], child_env)


def verify_ipa(ipa, private, team, bundle, certificate, execute, child_env):
    unpacked = private / "verified-ipa"
    with ipa_check(IpaFailureKind.UNPACK):
        unpack_ipa(ipa, unpacked, execute, child_env)
    with ipa_check(IpaFailureKind.APPLICATION):
        apps = list((unpacked / "Payload").glob("*.app"))
        if len(apps) != 1:
            raise SigningError()
    with ipa_check(IpaFailureKind.BUNDLE):
        info = plistlib.loads((apps[0] / "Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != bundle:
            raise SigningError()
    with ipa_check(IpaFailureKind.SIGNATURE):
        execute("Verify exported code signature", ["codesign", "--verify", "--deep", "--strict", str(apps[0])], child_env)
    with ipa_check(IpaFailureKind.CERTIFICATE):
        prefix = private / "exported-certificate-"
        execute("Inspect exported certificate", ["codesign", "-d", "--extract-certificates", str(prefix), str(apps[0])], child_env)
        if hashlib.sha256(Path(str(prefix) + "0").read_bytes()).digest() != hashlib.sha256(certificate).digest():
            raise SigningError()
    with ipa_check(IpaFailureKind.ENTITLEMENTS):
        entitlements = plistlib.loads(execute("Inspect exported signing identity",
                                            ["codesign", "-d", "--entitlements", ":-", str(apps[0])], child_env))
    # Legacy/transferred apps can retain an App ID prefix different to Team ID.
    app_identifier = entitlements.get("application-identifier", "")
    if not re.fullmatch(rf"[A-Z0-9]{{10}}\.{re.escape(bundle)}", app_identifier):
        raise IpaFailure(IpaFailureKind.APP_IDENTIFIER)
    if entitlements.get("com.apple.developer.team-identifier") != team:
        raise IpaFailure(IpaFailureKind.TEAM)
    if entitlements.get("get-task-allow") is not False:
        raise IpaFailure(IpaFailureKind.DEBUG)


def perform(env, private, execute=run, client_factory=AppStoreClient, diagnostics=None):
    diagnostics = diagnostics or Diagnostics()
    diagnostics.begin(Stage.CONFIGURATION)
    team, bundle = configuration(env)
    child_env = {key: value for key, value in env.items() if key not in SECRET_NAMES}
    cert = private / "distribution.p12"
    key = private / f"AuthKey_{env['APPSTORE_CONNECT_KEY_ID']}.p8"
    keychain = private / "signing.keychain-db"
    archive = private / "application.xcarchive"
    export = private / "export"
    options = private / "exportOptions.plist"
    diagnostics.begin(Stage.PRIVATE_INPUTS)
    try:
        cert_bytes = base64.b64decode("".join(env[SECRET_NAMES[0]].split()), validate=True)
    except ValueError:
        raise SigningError("Distribution certificate is not valid base64") from None
    write_private(cert, cert_bytes)
    write_private(key, env["APPSTORE_CONNECT_PRIVATE_KEY"].encode())
    diagnostics.begin(Stage.API_KEY)
    execute("Validate App Store key", ["openssl", "pkey", "-in", str(key), "-check", "-noout"], child_env)
    # Inspect the PUBLIC certificate to prevent accidental use of another team or
    # Developer ID certificate. The private key remains in its encrypted P12.
    public_cert = private / "distribution.pem"
    diagnostics.begin(Stage.CERTIFICATE)
    cert_env = dict(child_env, IOS_P12_PASSWORD=env["IOS_P12_PASSWORD"])
    certificate_args = ["openssl", "pkcs12", "-in", str(cert), "-clcerts", "-nokeys",
                        "-passin", "env:IOS_P12_PASSWORD", "-out", str(public_cert)]
    try:
        execute("Read public distribution certificate", certificate_args, cert_env)
    except SigningError:
        # OpenSSL 3 needs its legacy provider for older valid P12 exports. The
        # retry decodes only the public certificate, and still emits no output.
        execute("Read legacy public distribution certificate", [*certificate_args, "-legacy"], cert_env)
    subject = execute("Check certificate team", ["openssl", "x509", "-in", str(public_cert),
                      "-noout", "-subject", "-nameopt", "RFC2253"], child_env).decode()
    if not re.search(rf"(?:^|,)OU={re.escape(team)}(?:,|$)", subject.strip().removeprefix("subject=")):
        raise SigningError("Distribution certificate belongs to another Apple team")
    if not re.search(r"CN=(?:Apple Distribution|iPhone Distribution):", subject):
        raise SigningError("Certificate is not an Apple iOS distribution certificate")
    execute("Check certificate expiry", ["openssl", "x509", "-in", str(public_cert),
            "-checkend", "0", "-noout"], child_env)
    certificate = execute("Read distribution certificate DER", ["openssl", "x509", "-in", str(public_cert),
                          "-outform", "DER"], child_env)
    diagnostics.begin(Stage.XCODE_IDENTITY)
    project = required(env, "PORTABLE_IOS_PROJECT")
    common = ["xcodebuild", "-project", project, "-scheme", required(env, "PORTABLE_IOS_SCHEME"),
              "-configuration", required(env, "PORTABLE_IOS_CONFIGURATION"), f"DEVELOPMENT_TEAM={team}"]
    settings = verify_settings(execute("Resolve application identity", [*common, "-showBuildSettings", "-json"], child_env), bundle)
    declared = {}
    if settings.get("CODE_SIGN_ENTITLEMENTS"):
        path = Path(settings.get("SRCROOT", str(Path(project).parent))) / settings["CODE_SIGN_ENTITLEMENTS"]
        declared = plistlib.loads(path.read_bytes())
    diagnostics.begin(Stage.API_AUTHENTICATION)
    token = authentication_token(private, key, env["APPSTORE_CONNECT_KEY_ID"], env["APPSTORE_CONNECT_ISSUER_ID"], execute, child_env)
    profile, profile_path = application_profile(client_factory(token), certificate, team, bundle,
                                               private, execute, child_env, declared, diagnostics)
    password = secrets.token_urlsafe(32)
    created = False
    added = False
    installed = []
    try:
        diagnostics.begin(Stage.PROFILE_INSTALL)
        installed = install_profile(profile, profile_path, Path(env.get("HOME", str(Path.home()))), diagnostics)
        diagnostics.begin(Stage.KEYCHAIN)
        execute("Create temporary keychain", ["security", "create-keychain", "-p", password, str(keychain)], child_env)
        created = True
        execute("Set keychain timeout", ["security", "set-keychain-settings", "-lut", "7200", str(keychain)], child_env)
        execute("Unlock temporary keychain", ["security", "unlock-keychain", "-p", password, str(keychain)], child_env)
        execute("Import distribution identity", ["security", "import", str(cert), "-k", str(keychain),
                "-P", env["IOS_P12_PASSWORD"], "-T", "/usr/bin/codesign", "-T", "/usr/bin/security"], child_env)
        cert.unlink()
        execute("Allow codesign access", ["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                "-s", "-k", password, str(keychain)], child_env)
        existing = shlex.split(execute("Read keychain search list", ["security", "list-keychains", "-d", "user"], child_env).decode())
        execute("Add temporary keychain", ["security", "list-keychains", "-d", "user", "-s", str(keychain), *existing], child_env)
        added = True
        auth = ["-authenticationKeyPath", str(key),
                "-authenticationKeyID", env["APPSTORE_CONNECT_KEY_ID"],
                "-authenticationKeyIssuerID", env["APPSTORE_CONNECT_ISSUER_ID"]]
        diagnostics.begin(Stage.ARCHIVE)
        execute("Archive application", [*common, "-destination", "generic/platform=iOS", "-archivePath", str(archive),
                "CODE_SIGN_STYLE=Manual", f"CODE_SIGN_IDENTITY={hashlib.sha1(certificate).hexdigest()}",
                *application_profile_settings(profile),
                f"OTHER_CODE_SIGN_FLAGS=--keychain {shlex.quote(str(keychain))}", "archive"], child_env, timeout=5400)
        diagnostics.begin(Stage.ARCHIVE_VALIDATION)
        info = plistlib.loads((archive / "Info.plist").read_bytes())["ApplicationProperties"]
        if info.get("CFBundleIdentifier") != bundle or info.get("Team") != team:
            raise SigningError("Archived application bundle/team does not match expected identity")
        export_options = {"method": "app-store-connect", "teamID": team, "signingStyle": "manual",
                          "signingCertificate": hashlib.sha1(certificate).hexdigest(),
                          "provisioningProfiles": {bundle: profile["UUID"]},
                          "destination": "export", "manageAppVersionAndBuildNumber": False}
        options.write_bytes(plistlib.dumps(export_options))
        export_args = ["xcodebuild", "-exportArchive", "-archivePath", str(archive),
                       "-exportOptionsPlist", str(options)]
        diagnostics.begin(Stage.EXPORT)
        execute("Export signed IPA", [*export_args, "-exportPath", str(export)], child_env, timeout=1800)
        diagnostics.begin(Stage.IPA_VALIDATION)
        ipas = list(export.glob("*.ipa"))
        if len(ipas) != 1:
            raise SigningError("Export did not produce exactly one IPA")
        verify_ipa(ipas[0], private, team, bundle, certificate, execute, child_env)
        diagnostics.begin(Stage.IPA_OUTPUT)
        destination = Path(required(env, "RUNNER_TEMP")) / "export"
        destination.mkdir(exist_ok=True)
        if any(destination.iterdir()):
            raise SigningError("Refusing to reuse a nonempty IPA output directory")
        shutil.copy2(ipas[0], destination / ipas[0].name)
        if env.get("PORTABLE_IOS_UPLOAD_TESTFLIGHT") == "true":
            diagnostics.begin(Stage.TESTFLIGHT)
            export_options.update(destination="upload", testFlightInternalTestingOnly=True)
            options.write_bytes(plistlib.dumps(export_options))
            execute("Upload internal-only TestFlight build", [*export_args, *auth], child_env, timeout=1800)
    except BaseException:
        diagnostics.failed()
        raise
    finally:
        # Remove only OUR keychain; preserve keychains added by other runner jobs.
        # The lock serializes this action, while legacy signing jobs may still
        # share the same login user and must not lose their search-list entries.
        try:
            if added:
                with diagnostics.cleaning(CleanupStage.SEARCH_LIST):
                    current = shlex.split(execute("Read cleanup search list", ["security", "list-keychains", "-d", "user"], child_env).decode())
                    execute("Remove temporary keychain", ["security", "list-keychains", "-d", "user", "-s",
                            *[item for item in current if item != str(keychain)]], child_env)
        finally:
            try:
                if created:
                    with diagnostics.cleaning(CleanupStage.KEYCHAIN):
                        execute("Delete temporary keychain", ["security", "delete-keychain", str(keychain)], child_env)
            finally:
                with diagnostics.cleaning(CleanupStage.PROFILES):
                    for path in installed:
                        path.unlink(missing_ok=True)


@contextlib.contextmanager
def private_directory(diagnostics, runner_temp):
    directory = tempfile.TemporaryDirectory(prefix="portable-ios-", dir=runner_temp)
    try:
        yield directory.name
    except BaseException:
        diagnostics.failed()
        raise
    finally:
        with diagnostics.cleaning(CleanupStage.PRIVATE_DIRECTORY):
            directory.cleanup()
        diagnostics.cleaned()


def main():
    diagnostics = Diagnostics()
    def interrupted(_signal, _frame):
        raise SigningError("Portable signing interrupted; cleaning temporary credentials")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        diagnostics.begin(Stage.CONFIGURATION)
        configuration(os.environ)
        diagnostics.begin(Stage.SIGNING_LOCK)
        with signing_lock(Path.home()):
            diagnostics.begin(Stage.PRIVATE_INPUTS)
            with private_directory(diagnostics, required(os.environ, "RUNNER_TEMP")) as directory:
                perform(os.environ, Path(directory), diagnostics=diagnostics)
        diagnostics.cleaned()
        diagnostics.begin(Stage.COMPLETED)
        print("Portable iOS signing/export completed; temporary credentials removed.")
    except Exception as error:
        diagnostics.failed()
        print("::error::Portable iOS signing failed; diagnostics withheld.")
        if type(error) is ProvisioningApiError and type(error.category) is ApiFailure:
            print("::error title=Portable signing API diagnostic::" + error.category.value)
        if type(error) is XcodeFailure and type(error.kind) is XcodeFailureKind:
            print("::error title=Portable signing Xcode diagnostic::" + error.kind.value)
        if type(error) is IpaFailure and type(error.kind) is IpaFailureKind:
            print("::error title=Portable signing IPA diagnostic::" + error.kind.value)
        return 1
    finally:
        diagnostics.publish(os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
