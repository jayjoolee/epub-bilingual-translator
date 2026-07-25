"""Store successful fragments in private regular files without following links."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final, final

from epub_bilingual_translator.models import ProviderUnavailableError

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_ENTRY_READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW
_TEMPORARY_WRITE_FLAGS: Final = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_TEMPORARY_ATTEMPTS: Final = 32
_PRIVATE_DIRECTORY_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_CACHE_CLEANUP_FAILED: Final = "cache cleanup failed"
_CACHE_DIRECTORY_CLOSE_FAILED: Final = "cache directory close failed"
_CACHE_DIRECTORY_SETUP_FAILED: Final = "cache directory setup failed"
_CACHE_ENTRY_NOT_UTF8: Final = "cache entry is not valid UTF-8"
_CACHE_METADATA_READ_FAILED: Final = "cache metadata read failed"
_CACHE_READ_FAILED: Final = "cache read failed"
_CACHE_WRITE_FAILED: Final = "cache write failed"


@final
class TranslationCache:
    """Private file cache with typed corruption and filesystem failures."""

    def __init__(self, path: Path) -> None:
        """Validate and prepare an absolute private cache directory."""
        self._directory_fd: int | None = _prepare_directory(path)

    def get(self, cache_key: str) -> str | None:
        """Return a regular-file cache value without following symbolic links."""
        entry_name = _entry_name(cache_key)
        directory_fd = self._require_directory_fd()
        try:
            expected = os.stat(
                entry_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise _cache_error(_CACHE_METADATA_READ_FAILED, error) from error
        if not stat.S_ISREG(expected.st_mode):
            raise ProviderUnavailableError(detail="cache entry is not a regular file")
        descriptor = -1
        try:
            descriptor = os.open(
                entry_name,
                _ENTRY_READ_FLAGS,
                dir_fd=directory_fd,
            )
            observed = os.fstat(descriptor)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_dev != expected.st_dev
                or observed.st_ino != expected.st_ino
            ):
                raise ProviderUnavailableError(detail="cache entry changed during read")
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                return stream.read()
        except UnicodeError as error:
            raise _cache_error(_CACHE_ENTRY_NOT_UTF8, error) from error
        except OSError as error:
            raise _cache_error(_CACHE_READ_FAILED, error) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def put(self, cache_key: str, translated: str) -> None:
        """Atomically write one successful translation with user-only access."""
        entry_name = _entry_name(cache_key)
        directory_fd = self._require_directory_fd()
        _validate_existing_destination(directory_fd, entry_name)
        temporary_name: str | None = None
        descriptor = -1
        try:
            temporary_name, descriptor = _create_temporary_file(directory_fd)
            os.fchmod(descriptor, _PRIVATE_FILE_MODE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                _ = stream.write(translated)
            os.replace(
                temporary_name,
                entry_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except (OSError, UnicodeError) as error:
            raise _cache_error(_CACHE_WRITE_FAILED, error) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name is not None:
                try:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError as error:
                    raise _cache_error(_CACHE_CLEANUP_FAILED, error) from error

    def close(self) -> None:
        """Close the anchored cache directory descriptor."""
        descriptor = self._directory_fd
        self._directory_fd = None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError as error:
            raise _cache_error(_CACHE_DIRECTORY_CLOSE_FAILED, error) from error

    def _require_directory_fd(self) -> int:
        if self._directory_fd is None:
            raise ProviderUnavailableError(detail="cache is closed")
        return self._directory_fd


def _prepare_directory(path: Path) -> int:
    if os.name != "posix":
        raise ProviderUnavailableError(detail="private cache requires POSIX")
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        detail = "cache directory must be an absolute path"
        raise ProviderUnavailableError(detail=detail)
    descriptor = -1
    prepared = False
    try:
        descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
        for component in path.parts[1:]:
            next_descriptor = _open_or_create_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        _verify_private_directory(descriptor)
    except OSError as error:
        raise _cache_error(_CACHE_DIRECTORY_SETUP_FAILED, error) from error
    else:
        prepared = True
        return descriptor
    finally:
        if descriptor >= 0 and not prepared:
            os.close(descriptor)


def _open_or_create_directory(parent_fd: int, component: str) -> int:
    with suppress(FileExistsError):
        os.mkdir(component, mode=_PRIVATE_DIRECTORY_MODE, dir_fd=parent_fd)
    return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def _verify_private_directory(directory_fd: int) -> None:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProviderUnavailableError(detail="cache path is not a directory")
    _require_effective_user_owner(metadata)
    os.fchmod(directory_fd, _PRIVATE_DIRECTORY_MODE)
    metadata = os.fstat(directory_fd)
    _require_effective_user_owner(metadata)
    if stat.S_IMODE(metadata.st_mode) != _PRIVATE_DIRECTORY_MODE:
        raise ProviderUnavailableError(detail="cache directory is not private")


def _require_effective_user_owner(metadata: os.stat_result) -> None:
    if metadata.st_uid != os.geteuid():
        detail = "cache directory is not owned by this user"
        raise ProviderUnavailableError(detail=detail)


def _entry_name(cache_key: str) -> str:
    if (
        not cache_key
        or cache_key in {".", ".."}
        or os.sep in cache_key
        or (os.altsep is not None and os.altsep in cache_key)
    ):
        raise ProviderUnavailableError(detail="cache key is not valid")
    return cache_key


def _validate_existing_destination(directory_fd: int, entry_name: str) -> None:
    try:
        metadata = os.stat(entry_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise _cache_error(_CACHE_METADATA_READ_FAILED, error) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ProviderUnavailableError(detail="cache entry is not a regular file")


def _create_temporary_file(directory_fd: int) -> tuple[str, int]:
    for _ in range(_TEMPORARY_ATTEMPTS):
        temporary_name = f".cache-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                _TEMPORARY_WRITE_FLAGS,
                _PRIVATE_FILE_MODE,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return temporary_name, descriptor
    raise ProviderUnavailableError(detail="cache temporary file creation failed")


def _cache_error(
    prefix: str,
    error: OSError | UnicodeError,
) -> ProviderUnavailableError:
    return ProviderUnavailableError(detail=f"{prefix} ({type(error).__name__})")
