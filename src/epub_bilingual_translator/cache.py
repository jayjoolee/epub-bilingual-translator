"""Store successful fragments in private regular files without following links."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import final

from epub_bilingual_translator.models import ProviderUnavailableError


@final
class TranslationCache:
    """Private file cache with typed corruption and filesystem failures."""

    def __init__(self, path: Path) -> None:
        """Validate and prepare an absolute private cache directory."""
        self._path: Path = _prepare_directory(path)

    def get(self, cache_key: str) -> str | None:
        """Return a regular-file cache value without following symbolic links."""
        path = self._path / cache_key
        try:
            expected = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            detail = "cache metadata read failed"
            raise _cache_error(detail, error) from error
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise ProviderUnavailableError(detail="cache entry is not a regular file")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY)
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
            detail = "cache entry is not valid UTF-8"
            raise _cache_error(detail, error) from error
        except OSError as error:
            detail = "cache read failed"
            raise _cache_error(detail, error) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def put(self, cache_key: str, translated: str) -> None:
        """Atomically write one successful translation with user-only access."""
        destination = self._path / cache_key
        _validate_existing_destination(destination)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path,
                delete=False,
            ) as temporary:
                _ = temporary.write(translated)
                temporary_path = Path(temporary.name)
            if os.name == "posix":
                temporary_path.chmod(0o600, follow_symlinks=False)
            _ = temporary_path.replace(destination)
        except OSError as error:
            detail = "cache write failed"
            raise _cache_error(detail, error) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as error:
                    detail = "cache cleanup failed"
                    raise _cache_error(detail, error) from error


def _prepare_directory(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        detail = "cache directory must be an absolute path"
        raise ProviderUnavailableError(detail=detail)
    try:
        if path.is_symlink():
            raise ProviderUnavailableError(detail="cache directory cannot be a symlink")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ProviderUnavailableError(detail="cache path is not a directory")
        if os.name == "posix":
            path.chmod(0o700, follow_symlinks=False)
    except OSError as error:
        detail = "cache directory setup failed"
        raise _cache_error(detail, error) from error
    return path


def _validate_existing_destination(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        detail = "cache metadata read failed"
        raise _cache_error(detail, error) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProviderUnavailableError(detail="cache entry is not a regular file")


def _cache_error(
    prefix: str,
    error: OSError | UnicodeError,
) -> ProviderUnavailableError:
    return ProviderUnavailableError(detail=f"{prefix} ({type(error).__name__})")
