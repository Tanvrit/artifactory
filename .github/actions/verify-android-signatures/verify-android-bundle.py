#!/usr/bin/env python3
"""Verify every bundle payload signature and match it to the release APK signer.

Only fixed status text is emitted. Certificate metadata and tool diagnostics stay
in memory; no signing key, password, trust-store change or network is required.
"""
import argparse
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


class SignatureVerificationError(Exception):
    pass


def tool_environment():
    allowed = ("PATH", "JAVA_HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")
    return {**{key: os.environ[key] for key in allowed if key in os.environ},
            "LANG": "C", "LC_ALL": "C"}


def run_tool(arguments):
    try:
        result = subprocess.run(arguments, env=tool_environment(), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SignatureVerificationError() from None
    if result.returncode:
        raise SignatureVerificationError()
    return result.stdout


def java_executable():
    return str(Path(os.environ["JAVA_HOME"]) / "bin/java") if os.environ.get("JAVA_HOME") else "java"


def bundle_signers(bundle):
    with tempfile.TemporaryDirectory(prefix="bundle-signature-check-") as directory:
        source = Path(directory) / "BundleSignatureCheck.java"
        source.write_text(JAVA_SOURCE)
        source.chmod(0o600)
        output = run_tool([java_executable(), "--source", "21", str(source), str(bundle)])
    digests = output.splitlines()
    if not digests or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests):
        raise SignatureVerificationError()
    return frozenset(digests)


def apk_signers(apk, signer):
    output = run_tool([str(signer), "verify", "--print-certs", str(apk)])
    digests = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$", output, re.MULTILINE)
    if not digests:
        raise SignatureVerificationError()
    return frozenset(digest.lower() for digest in digests)


def release_files(directory, suffix):
    root = Path.cwd().resolve()
    path = Path(directory)
    try:
        path.resolve().relative_to(root)
        files = sorted(path.rglob("*" + suffix))
        if not files:
            raise SignatureVerificationError()
        for artifact in files:
            artifact.resolve().relative_to(root)
            if artifact.is_symlink() or not artifact.is_file():
                raise SignatureVerificationError()
        return files
    except (OSError, ValueError):
        raise SignatureVerificationError() from None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--apk-dir", required=True)
    parser.add_argument("--apksigner", required=True)
    arguments = parser.parse_args()
    try:
        apks = release_files(arguments.apk_dir, ".apk")
        bundles = release_files(arguments.bundle_dir, ".aab")
        signers = {apk_signers(apk, Path(arguments.apksigner)) for apk in apks}
        if len(signers) != 1:
            raise SignatureVerificationError()
        expected = next(iter(signers))
        if any(bundle_signers(bundle) != expected for bundle in bundles):
            raise SignatureVerificationError()
    except (SignatureVerificationError, OSError, ValueError):
        print("::error::Android release signature verification failed.", file=sys.stderr)
        return 1
    print("Android bundle payload signatures match the verified release APK signer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
