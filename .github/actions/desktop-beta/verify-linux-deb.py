#!/usr/bin/env python3
"""Inspect a native ARM64 DEB payload without extracting or installing it."""

import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


class BoundedReader:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0

    def read(self, size=-1):
        limit = 2 * 1024 * 1024 * 1024
        size = min(limit - self.count + 1, size if size >= 0 else limit + 1)
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > limit:
            raise ValueError("DEB uncompressed payload exceeds native package bound")
        return data


def inspect_payload(stream):
    count = total = native = 0
    launcher = runtime = False
    reader = BoundedReader(stream)
    with tarfile.open(fileobj=reader, mode="r|") as archive:
        for member in archive:
            count += 1
            name = PurePosixPath(member.name)
            if (count > 20000 or name.is_absolute() or ".." in name.parts
                    or member.size < 0 or member.size > 512 * 1024 * 1024):
                raise ValueError("DEB payload bounds or path differ")
            if not member.isfile():
                continue
            total += member.size
            if total > 2 * 1024 * 1024 * 1024:
                raise ValueError("DEB payload exceeds native package bound")
            header = archive.extractfile(member).read(min(member.size, 20))
            if header[:4] != b"\x7fELF":
                continue
            if (len(header) != 20 or header[4:6] != b"\x02\x01"
                    or int.from_bytes(header[18:20], "little") != 183):
                raise ValueError("DEB contains a non-AArch64 native executable")
            native += 1
            launcher = launcher or name.parts[-2:] == ("bin", "Jashva")
            runtime = runtime or name.parts[-3:] == ("lib", "server", "libjvm.so")
    while reader.read(65536):
        pass
    if not launcher or not runtime or native < 3:
        raise ValueError("DEB native launcher or bundled runtime is missing")
    return {"schema": 1, "elf_machine": 183, "native_files": native,
            "launcher_verified": launcher, "bundled_jvm_verified": runtime,
            "installed": False, "runtime_launch_verified": False}


def main():
    artifact = Path(sys.argv[1])
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ValueError("DEB artifact must be a bounded regular file")
    with subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(artifact)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as child:
        try:
            result = inspect_payload(child.stdout)
            if child.wait(timeout=30) != 0:
                raise ValueError("DEB data stream failed")
        except BaseException:
            child.kill()
            child.wait(timeout=10)
            raise
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        print("Native ARM64 DEB payload verification failed", file=sys.stderr)
        raise SystemExit(1)
