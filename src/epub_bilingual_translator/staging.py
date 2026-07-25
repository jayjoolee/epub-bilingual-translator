"""Publish a generated EPUB from retained POSIX directory descriptors."""

from __future__ import annotations

import os
import secrets
import stat
import zipfile
from contextlib import suppress
from typing import TYPE_CHECKING, BinaryIO, Final, final

from epub_bilingual_translator.models import ArchiveError, OutputPathError

if TYPE_CHECKING:
    import types
    from pathlib import Path

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS: Final = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_PRIVATE_DIRECTORY_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_STAGE_ATTEMPTS: Final = 32
_STAGED_FILE_NAME: Final = "translated.epub"


@final
class StagedOutput:
    """Own a private staged EPUB until atomic no-clobber publication."""

    def __init__(self, output_path: Path) -> None:
        """Create private staging entries beneath the destination parent."""
        if os.name != "posix":
            raise ArchiveError(detail="secure output staging requires POSIX")
        self._output_path: Path = output_path
        self._parent_fd: int = -1
        self._stage_fd: int = -1
        self._stage_name: str | None = None
        self._stage_identity: tuple[int, int] | None = None
        self._stream: BinaryIO | None = None
        descriptor = -1
        try:
            parent = output_path.parent.resolve(strict=True)
            self._parent_fd = os.open(parent, _DIRECTORY_FLAGS)
            self._stage_name = _create_stage_directory(
                self._parent_fd,
                output_path.stem,
            )
            self._stage_fd = os.open(
                self._stage_name,
                _DIRECTORY_FLAGS,
                dir_fd=self._parent_fd,
            )
            self._stage_identity = _verify_stage_directory(
                self._parent_fd,
                self._stage_fd,
                self._stage_name,
            )
            descriptor = os.open(
                _STAGED_FILE_NAME,
                _FILE_FLAGS,
                _PRIVATE_FILE_MODE,
                dir_fd=self._stage_fd,
            )
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            self._stream = os.fdopen(descriptor, "w+b")
            descriptor = -1
        except (OSError, ArchiveError):
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = -1
            with suppress(ArchiveError):
                self.close()
            raise

    @property
    def stream(self) -> BinaryIO:
        """Return the retained binary file object used by ``ZipFile``."""
        if self._stream is None:
            raise ArchiveError(detail="staged output is closed")
        return self._stream

    def verify(self) -> None:
        """Flush and exercise CRC checks through the retained file object."""
        stream = self.stream
        stream.flush()
        os.fsync(stream.fileno())
        _ = stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            broken = archive.testzip()
        if broken is not None:
            raise ArchiveError(detail=f"corrupt output member: {broken}")

    def publish(self) -> None:
        """Atomically hard-link the retained staged file without overwrite."""
        if self._stage_fd < 0 or self._parent_fd < 0:
            raise ArchiveError(detail="staged output is closed")
        try:
            os.link(
                _STAGED_FILE_NAME,
                self._output_path.name,
                src_dir_fd=self._stage_fd,
                dst_dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise OutputPathError(
                self._output_path,
                "output appeared during translation",
            ) from error
        except OSError as error:
            raise ArchiveError(detail=f"could not publish output: {error}") from error

    def close(self) -> None:
        """Release descriptors and remove only the unchanged staging entry."""
        cleanup_errors: list[OSError] = []
        operations = (
            self._close_stream,
            self._unlink_stage_file,
            self._remove_unchanged_stage_directory,
            self._close_stage_directory,
            self._close_parent_directory,
        )
        for operation in operations:
            try:
                operation()
            except OSError as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            error = cleanup_errors[0]
            detail = f"output staging cleanup failed ({type(error).__name__})"
            raise ArchiveError(detail=detail) from error

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.close()

    def _unlink_stage_file(self) -> None:
        if self._stage_fd >= 0:
            with suppress(FileNotFoundError):
                os.unlink(_STAGED_FILE_NAME, dir_fd=self._stage_fd)

    def _close_stage_directory(self) -> None:
        if self._stage_fd >= 0:
            try:
                os.close(self._stage_fd)
            finally:
                self._stage_fd = -1

    def _close_parent_directory(self) -> None:
        if self._parent_fd >= 0:
            try:
                os.close(self._parent_fd)
            finally:
                self._parent_fd = -1

    def _remove_unchanged_stage_directory(self) -> None:
        if (
            self._parent_fd < 0
            or self._stage_name is None
            or self._stage_identity is None
        ):
            return
        try:
            metadata = os.stat(
                self._stage_name,
                dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        identity = (metadata.st_dev, metadata.st_ino)
        if stat.S_ISDIR(metadata.st_mode) and identity == self._stage_identity:
            os.rmdir(self._stage_name, dir_fd=self._parent_fd)

    def __enter__(self) -> StagedOutput:
        """Keep the staged output open for ZIP generation."""
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Remove safe staging entries and release retained descriptors."""
        del error, traceback
        try:
            self.close()
        except ArchiveError:
            if error_type is None:
                raise


def _create_stage_directory(parent_fd: int, output_stem: str) -> str:
    prefix = output_stem[:32]
    for _ in range(_STAGE_ATTEMPTS):
        name = f".{prefix}-{secrets.token_hex(16)}.epub.tmp"
        try:
            os.mkdir(name, mode=_PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name
    raise ArchiveError(detail="output staging directory creation failed")


def _verify_stage_directory(
    parent_fd: int,
    stage_fd: int,
    stage_name: str,
) -> tuple[int, int]:
    expected = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
    observed = os.fstat(stage_fd)
    expected_identity = (expected.st_dev, expected.st_ino)
    observed_identity = (observed.st_dev, observed.st_ino)
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or expected_identity != observed_identity
        or observed.st_uid != os.geteuid()
    ):
        raise ArchiveError(detail="output staging directory changed during creation")
    os.fchmod(stage_fd, _PRIVATE_DIRECTORY_MODE)
    return observed_identity
