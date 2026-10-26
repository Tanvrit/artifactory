#!/usr/bin/env python3
"""Verify every bundle payload signature and match it to the release APK signer.

Only fixed status text is emitted. Certificate metadata and tool diagnostics stay
in memory; no signing key, password, trust-store change or network is required.
An optional public certificate pin also enforces the approved release identity.
"""
import argparse
from enum import Enum
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


JAVA_SOURCE = r"""
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Set;
import java.util.TreeSet;
import java.util.jar.JarFile;

class BundleSignatureCheck {
    public static void main(String[] args) {
        try (var jar = new JarFile(Path.of(args[0]).toFile(), true)) {
            Set<String> expected = null;
            Set<String> names = new TreeSet<>();
            int payloads = 0;
            var entries = jar.entries();
            byte[] buffer = new byte[65536];
            while (entries.hasMoreElements()) {
                var entry = entries.nextElement();
                if (!names.add(entry.getName())) throw new SecurityException();
                if (entry.isDirectory()) continue;
                try (var stream = jar.getInputStream(entry)) {
                    while (stream.read(buffer) != -1) { }
                }
                String name = entry.getName().toUpperCase(java.util.Locale.ROOT);
                if (name.equals("META-INF/MANIFEST.MF") ||
                    name.matches("META-INF/[^/]+\\.(SF|RSA|DSA|EC)") ||
                    name.matches("META-INF/SIG-[^/]+")) continue;
                var signers = entry.getCodeSigners();
                if (signers == null || signers.length == 0) throw new SecurityException();
                Set<String> current = new TreeSet<>();
                for (var signer : signers) {
                    var certificate = signer.getSignerCertPath().getCertificates().get(0);
                    current.add(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded())));
                }
                if (expected != null && !expected.equals(current)) throw new SecurityException();
                expected = current;
                payloads++;
            }
            if (payloads == 0 || expected == null) throw new SecurityException();
            for (String digest : expected) System.out.println(digest);
        } catch (Exception failure) {
            System.exit(1);
        }
    }
}
"""


class FailureStage(Enum):
    ARTIFACT_DISCOVERY = "artifact-discovery"
    APK_TOOL_VERIFICATION = "apk-tool-verification"
    APK_CERTIFICATE_FORMAT = "apk-certificate-format"
    MULTIPLE_APK_SIGNERS = "multiple-apk-signers"
    APPROVED_PIN_FORMAT = "approved-pin-format"
    APPROVED_PIN_MISMATCH = "approved-pin-mismatch"
    AAB_PAYLOAD_OR_TOOL_VERIFICATION = "aab-payload-or-tool-verification"
    AAB_SIGNER_FORMAT = "aab-signer-format"
    AAB_SIGNER_MISMATCH = "aab-signer-mismatch"


class SignatureVerificationError(Exception):
    def __init__(self, stage):
        if not isinstance(stage, FailureStage):
            raise ValueError() from None
        self.stage = stage
        super().__init__()


def write_stage_metadata(stage):
    """Publish only a source-owned enum; diagnostics never change verification."""
    if isinstance(stage, FailureStage):
        value = stage.value
    elif stage is None:
        value = "success"
    else:
        return
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    try:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"verification_stage={value}\n")
    except (OSError, ValueError):
        # An unavailable diagnostic must not mask the verifier's real result.
        return


def tool_environment():
    allowed = ("PATH", "JAVA_HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")
    return {**{key: os.environ[key] for key in allowed if key in os.environ},
            "LANG": "C", "LC_ALL": "C"}


def run_tool(arguments, failure_stage):
    try:
        result = subprocess.run(arguments, env=tool_environment(), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SignatureVerificationError(failure_stage) from None
    if result.returncode:
        raise SignatureVerificationError(failure_stage)
    return result.stdout


def java_executable():
    return str(Path(os.environ["JAVA_HOME"]) / "bin/java") if os.environ.get("JAVA_HOME") else "java"


def bundle_signers(bundle):
    with tempfile.TemporaryDirectory(prefix="bundle-signature-check-") as directory:
        source = Path(directory) / "BundleSignatureCheck.java"
        source.write_text(JAVA_SOURCE)
        source.chmod(0o600)
        output = run_tool([java_executable(), "--source", "21", str(source), str(bundle)],
                          FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION)
    digests = output.splitlines()
    if not digests or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests):
        raise SignatureVerificationError(FailureStage.AAB_SIGNER_FORMAT)
    return frozenset(digests)


def apk_signers(apk, signer):
    output = run_tool([str(signer), "verify", "--print-certs", str(apk)],
                      FailureStage.APK_TOOL_VERIFICATION)
    # apksigner uses scheme/range labels for v3 signers in newer build tools.
    # Inspect every certificate line so an unknown/rotated signer cannot be
    # silently omitted while another recognized signer satisfies the pin.
    sdk_range = r"\(minSdkVersion=\d+(?: \(dev release=true\))?, maxSdkVersion=\d+\)"
    label = rf"(?:Signer #[1-9]\d*|Signer:|Signer {sdk_range}|V3\.[01] Signer:(?: {sdk_range})?)"
    pattern = re.compile(rf"{label} certificate SHA-256 digest: ([0-9a-fA-F]{{64}})[ \t]*")
    digests = []
    for line in output.splitlines():
        if " certificate SHA-256 digest:" not in line:
            continue
        if re.fullmatch(r"Source Stamp Signer:? certificate SHA-256 digest: [0-9a-fA-F]{64}[ \t]*", line):
            continue  # A source stamp is not the APK's signing identity.
        match = pattern.fullmatch(line)
        if match is None:
            raise SignatureVerificationError(FailureStage.APK_CERTIFICATE_FORMAT)
        digests.append(match.group(1))
    if not digests:
        raise SignatureVerificationError(FailureStage.APK_CERTIFICATE_FORMAT)
    return frozenset(digest.lower() for digest in digests)


def expected_certificate(value):
    if not value:
        return None
    value = value.strip()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{64}|(?:[0-9a-fA-F]{2}:){31}[0-9a-fA-F]{2})", value):
        raise SignatureVerificationError(FailureStage.APPROVED_PIN_FORMAT)
    return frozenset([value.replace(":", "").lower()])


def release_files(directory, suffix):
    root = Path.cwd().resolve()
    path = Path(directory)
    try:
        path.resolve().relative_to(root)
        files = sorted(path.rglob("*" + suffix))
        if not files:
            raise SignatureVerificationError(FailureStage.ARTIFACT_DISCOVERY)
        for artifact in files:
            artifact.resolve().relative_to(root)
            if artifact.is_symlink() or not artifact.is_file():
                raise SignatureVerificationError(FailureStage.ARTIFACT_DISCOVERY)
        return files
    except (OSError, ValueError):
        raise SignatureVerificationError(FailureStage.ARTIFACT_DISCOVERY) from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--apk-dir", required=True)
    parser.add_argument("--apksigner", required=True)
    parser.add_argument("--expected-certificate-sha256", default="")
    arguments = parser.parse_args()
    stage = FailureStage.ARTIFACT_DISCOVERY
    try:
        apks = release_files(arguments.apk_dir, ".apk")
        bundles = release_files(arguments.bundle_dir, ".aab")
        stage = FailureStage.APK_TOOL_VERIFICATION
        signers = {apk_signers(apk, Path(arguments.apksigner)) for apk in apks}
        if len(signers) != 1:
            raise SignatureVerificationError(FailureStage.MULTIPLE_APK_SIGNERS)
        actual = next(iter(signers))
        stage = FailureStage.APPROVED_PIN_FORMAT
        approved = expected_certificate(arguments.expected_certificate_sha256)
        if approved is not None and actual != approved:
            raise SignatureVerificationError(FailureStage.APPROVED_PIN_MISMATCH)
        stage = FailureStage.AAB_PAYLOAD_OR_TOOL_VERIFICATION
        if any(bundle_signers(bundle) != actual for bundle in bundles):
            raise SignatureVerificationError(FailureStage.AAB_SIGNER_MISMATCH)
    except (SignatureVerificationError, OSError, ValueError) as failure:
        if isinstance(failure, SignatureVerificationError):
            stage = failure.stage
        write_stage_metadata(stage)
        print("::error::Android release signature verification failed.", file=sys.stderr)
        print(f"::error::Android release signature failure stage: {stage.value}.", file=sys.stderr)
        return 1
    write_stage_metadata(None)
    if approved is not None:
        print("Android release APK and bundle signatures match the approved certificate.")
    else:
        print("Android bundle payload signatures match the verified release APK signer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
