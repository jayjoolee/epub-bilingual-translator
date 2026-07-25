"""Bound EPUB work to 10k members, 2 GiB total, and conservative ZIP ratios."""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from epub_bilingual_translator.models import ArchiveError, OutputPathError

MIMETYPE_BYTES: Final = b"application/epub+zip"
MAX_MEMBER_COUNT: Final = 10_000
MAX_MEMBER_BYTES: Final = 512 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_XHTML_BYTES: Final = 16 * 1024 * 1024
MAX_XML_METADATA_BYTES: Final = 1024 * 1024
MAX_COMPRESSION_RATIO: Final = 200.0
COPY_CHUNK_BYTES: Final = 1024 * 1024
XHTML_SUFFIXES: Final = (".xhtml", ".html", ".htm")
SUPPORTED_COMPRESSION_METHODS: Final = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
)


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """Validated central-directory entries in source order."""

    entries: tuple[zipfile.ZipInfo, ...]


def validate_archive(source: zipfile.ZipFile) -> ArchivePlan:
    """Validate EPUB structure and resource bounds before reading members."""
    entries = tuple(source.infolist())
    if not entries or len(entries) > MAX_MEMBER_COUNT:
        raise ArchiveError(detail="invalid member count")
    first = entries[0]
    if (
        first.filename != "mimetype"
        or first.compress_type != zipfile.ZIP_STORED
        or first.file_size != len(MIMETYPE_BYTES)
    ):
        raise ArchiveError(detail="first member must be the stored EPUB mimetype")
    names: set[str] = set()
    total_bytes = 0
    for info in entries:
        _validate_member_name(info.filename)
        if info.filename in names:
            raise ArchiveError(detail=f"duplicate ZIP member: {info.filename}")
        names.add(info.filename)
        _validate_member_bounds(info)
        total_bytes += info.file_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ArchiveError(detail="aggregate uncompressed size exceeds limit")
    if "META-INF/container.xml" not in names:
        raise ArchiveError(detail="META-INF/container.xml is required")
    if read_member_bounded(source, first, len(MIMETYPE_BYTES)) != MIMETYPE_BYTES:
        raise ArchiveError(detail="invalid EPUB mimetype value")
    return ArchivePlan(entries=entries)


def read_member_bounded(
    source: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    limit: int,
) -> bytes:
    """Read one already-validated member without trusting its size blindly."""
    result = bytearray()
    with source.open(info) as reader:
        while chunk := reader.read(COPY_CHUNK_BYTES):
            result.extend(chunk)
            if len(result) > limit:
                raise ArchiveError(detail=f"member exceeds read limit: {info.filename}")
    if len(result) != info.file_size:
        raise ArchiveError(detail=f"member size mismatch: {info.filename}")
    return bytes(result)


def copy_member_streaming(
    source: zipfile.ZipFile,
    destination: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> None:
    """Copy one member with bounded memory and observed byte accounting."""
    copied = 0
    with (
        source.open(info) as reader,
        destination.open(
            info,
            mode="w",
            force_zip64=True,
        ) as writer,
    ):
        while chunk := reader.read(COPY_CHUNK_BYTES):
            copied += len(chunk)
            if copied > info.file_size or copied > MAX_MEMBER_BYTES:
                raise ArchiveError(detail=f"member size mismatch: {info.filename}")
            written = writer.write(chunk)
            if written != len(chunk):
                raise ArchiveError(detail=f"short ZIP write: {info.filename}")
    if copied != info.file_size:
        raise ArchiveError(detail=f"member size mismatch: {info.filename}")


def write_transformed_member(
    destination: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    data: bytes,
) -> None:
    """Write one bounded transformed XHTML member with original metadata."""
    if len(data) > MAX_XHTML_BYTES:
        raise ArchiveError(detail=f"translated XHTML exceeds limit: {info.filename}")
    destination.writestr(info, data)


def create_temporary_output(output_path: Path) -> Path:
    """Create a same-directory temporary file for atomic publication."""
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}-",
        suffix=".epub.tmp",
        dir=output_path.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def publish_no_clobber(temporary: Path, output_path: Path) -> None:
    """Atomically publish through a same-filesystem hard link without overwrite."""
    try:
        os.link(temporary, output_path, follow_symlinks=False)
    except FileExistsError as error:
        raise OutputPathError(
            output_path,
            "output appeared during translation",
        ) from error
    except OSError as error:
        raise ArchiveError(detail=f"could not publish output: {error}") from error


def verify_output(path: Path) -> None:
    """Exercise CRC checks for every generated archive member."""
    with zipfile.ZipFile(path) as archive:
        broken = archive.testzip()
    if broken is not None:
        raise ArchiveError(detail=f"corrupt output member: {broken}")


def _validate_member_name(name: str) -> None:
    trimmed = name.removesuffix("/")
    segments = trimmed.split("/")
    path = PurePosixPath(name)
    if (
        not trimmed
        or "\\" in name
        or path.is_absolute()
        or any(segment in {"", ".", ".."} for segment in segments)
        or ":" in segments[0]
    ):
        raise ArchiveError(detail=f"unsafe ZIP member name: {name}")


def _validate_member_bounds(info: zipfile.ZipInfo) -> None:
    if info.compress_type not in SUPPORTED_COMPRESSION_METHODS:
        raise ArchiveError(
            detail=f"unsupported ZIP compression method: {info.filename}",
        )
    if info.flag_bits & 0x1:
        raise ArchiveError(detail=f"ZIP-encrypted member refused: {info.filename}")
    if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
        raise ArchiveError(detail=f"member exceeds size limit: {info.filename}")
    if (
        info.filename.casefold().endswith(XHTML_SUFFIXES)
        and info.file_size > MAX_XHTML_BYTES
    ):
        raise ArchiveError(detail=f"XHTML exceeds size limit: {info.filename}")
    if info.file_size == 0:
        return
    ratio = info.file_size / max(info.compress_size, 1)
    if ratio > MAX_COMPRESSION_RATIO:
        raise ArchiveError(detail=f"suspicious compression ratio: {info.filename}")
