"""Bounded data-only Debian library extraction. Never run package scripts."""

import os
from pathlib import Path, PurePosixPath
import re
import tarfile

MAX_TAR_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 192 * 1024 * 1024
MAX_LIBRARY_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10000
LIBRARY = re.compile(r"(?:lib[A-Za-z0-9_+.-]+|ld-linux-aarch64)\.so(?:\.[A-Za-z0-9_+.-]+)*")
PREFIXES = {PurePosixPath("usr/lib/aarch64-linux-gnu"), PurePosixPath("lib/aarch64-linux-gnu")}


class PayloadError(Exception):
    def __init__(self, reason, diagnostics=None):
        super().__init__(reason)
        self.diagnostics = diagnostics or {}


class BoundedReader:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0

    def read(self, size=-1):
        size = min(MAX_TAR_BYTES - self.count + 1, size if size >= 0 else MAX_TAR_BYTES + 1)
        data = self.stream.read(size)
        self.count += len(data)
        if self.count > MAX_TAR_BYTES:
            raise PayloadError("uncompressed_payload_limit")
        return data


def extract_tar_stream(stream, destination, seen):
    reader = BoundedReader(stream)
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        raise PayloadError("unsafe_library_directory")
    count = 0
    extracted = 0
    try:
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                count += 1
                name = PurePosixPath(member.name)
                library_name = name.name if len(name.name) <= 160 and LIBRARY.fullmatch(name.name) else None
                diagnostics = {"observedSize": member.size, "memberCount": count,
                               "memberSizeLimit": MAX_MEMBER_BYTES, "memberCountLimit": MAX_MEMBERS,
                               "selectedLibrary": bool(name.parent in PREFIXES and library_name)}
                if library_name and diagnostics["selectedLibrary"]:
                    diagnostics["library"] = library_name
                if count > MAX_MEMBERS:
                    raise PayloadError("payload_member_count_limit", diagnostics)
                if member.size > MAX_MEMBER_BYTES:
                    raise PayloadError("payload_member_size_limit", diagnostics)
                if name.is_absolute() or ".." in name.parts or "\x00" in member.name:
                    raise PayloadError("unsafe_payload_path")
                if member.islnk() or not (member.isdir() or member.isreg() or member.issym()):
                    raise PayloadError("unsupported_payload_member")
                if name.parent not in PREFIXES or not LIBRARY.fullmatch(name.name):
                    continue
                if not (member.isreg() or member.issym()):
                    raise PayloadError("invalid_library_member")
                target = destination / name.name
                if name.name in seen or target.exists() or target.is_symlink():
                    raise PayloadError("library_collision")
                seen.add(name.name)
                if member.issym():
                    link = PurePosixPath(member.linkname)
                    if len(link.parts) != 1 or not LIBRARY.fullmatch(member.linkname):
                        raise PayloadError("unsafe_library_symlink")
                    target.symlink_to(member.linkname)
                else:
                    total = sum(path.stat().st_size for path in destination.iterdir()
                                if not path.is_symlink() and path.is_file())
                    if total + member.size > MAX_LIBRARY_BYTES:
                        raise PayloadError("extracted_library_limit")
                    data = archive.extractfile(member).read(member.size + 1)
                    if len(data) != member.size:
                        raise PayloadError("truncated_library")
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(target, flags, 0o600)
                    with os.fdopen(descriptor, "wb") as file:
                        file.write(data)
                    extracted += 1
        # Consume trailing tar padding/data too, so an unselected/trailing
        # decompression bomb cannot evade the total uncompressed-byte bound.
        while reader.read(65536):
            pass
    except PayloadError:
        raise
    except (tarfile.TarError, OSError, ValueError):
        raise PayloadError("invalid_payload_stream") from None
    return extracted


def finish_library_links(destination):
    root = Path(destination).resolve(strict=True)
    for path in root.iterdir():
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
                target.relative_to(root)
                if not target.is_file():
                    raise ValueError()
            except (OSError, ValueError, RuntimeError):
                raise PayloadError("unresolved_library_symlink") from None
