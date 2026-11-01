"""Closed Xcode failure hints. Never return any captured output or matched text."""

from enum import Enum


class XcodeFailureKind(Enum):
    PROFILE_ON_NON_APP_TARGET = "xcode-profile-on-non-app-target"
    PROFILE_MISSING = "xcode-profile-missing"
    PROFILE_MISMATCH = "xcode-profile-mismatch"
    CODE_SIGNING = "xcode-code-signing"
    PACKAGE_RESOLUTION = "xcode-package-resolution"
    LINKER = "xcode-linker"
    DUPLICATE_OUTPUT = "xcode-duplicate-output"
    SOURCE_COMPILATION = "xcode-source-compilation"
    TOOLCHAIN = "xcode-toolchain"
    UNKNOWN = "xcode-unknown"


def classify(stdout, stderr):
    """Inspect only bounded tails, and produce a hint rather than a root-cause claim."""
    output = (stdout[-1048576:] + b"\n" + stderr[-1048576:]).lower()
    cases = (
        (XcodeFailureKind.PROFILE_ON_NON_APP_TARGET, (b"does not support provisioning profiles",)),
        (XcodeFailureKind.PROFILE_MISSING, (b"requires a provisioning profile", b"no profiles for")),
        (XcodeFailureKind.PROFILE_MISMATCH, (b"doesn't include signing certificate", b"doesn't support the")),
        (XcodeFailureKind.CODE_SIGNING, (b"errsecinternalcomponent", b"codesign failed", b"no signing certificate")),
        (XcodeFailureKind.PACKAGE_RESOLUTION, (b"could not resolve package dependencies", b"failed to resolve dependencies")),
        (XcodeFailureKind.LINKER, (b"undefined symbols", b"duplicate symbols", b"linker command failed")),
        (XcodeFailureKind.DUPLICATE_OUTPUT, (b"multiple commands produce", b"duplicate output file")),
        (XcodeFailureKind.SOURCE_COMPILATION, (b"error: no such module", b"error: cannot find", b"error: cannot convert")),
        (XcodeFailureKind.TOOLCHAIN, (b"unable to find a destination", b"unable to find a specification", b"sdk cannot be located")),
    )
    for kind, markers in cases:
        if any(marker in output for marker in markers):
            return kind
    return XcodeFailureKind.UNKNOWN
