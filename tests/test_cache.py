from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

import pytest

from epub_bilingual_translator.cache import TranslationCache
from epub_bilingual_translator.models import ProviderUnavailableError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX dirfd semantics")
def test_cache_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    # Given
    real_ancestor = tmp_path / "real-ancestor"
    real_ancestor.mkdir()
    linked_ancestor = tmp_path / "linked-ancestor"
    linked_ancestor.symlink_to(real_ancestor, target_is_directory=True)

    # When
    with pytest.raises(ProviderUnavailableError):
        _ = TranslationCache(linked_ancestor / "cache")

    # Then
    assert not (real_ancestor / "cache").exists()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX ownership and modes")
def test_cache_final_directory_is_owned_and_private(tmp_path: Path) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o755)

    # When
    cache = TranslationCache(cache_dir)
    cache.close()

    # Then
    metadata = cache_dir.stat()
    assert metadata.st_uid == os.geteuid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX ownership")
def test_cache_rejects_non_owner_before_changing_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(mode=0o755)
    original_mode = stat.S_IMODE(cache_dir.stat().st_mode)
    monkeypatch.setattr(os, "geteuid", lambda: cache_dir.stat().st_uid + 1)

    # When
    with pytest.raises(ProviderUnavailableError):
        _ = TranslationCache(cache_dir)

    # Then
    assert stat.S_IMODE(cache_dir.stat().st_mode) == original_mode


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX dirfd semantics")
def test_cache_operations_remain_anchored_after_directory_path_is_swapped(
    tmp_path: Path,
) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    cache = TranslationCache(cache_dir)
    anchored_dir = tmp_path / "anchored"
    _ = cache_dir.rename(anchored_dir)
    cache_dir.mkdir()

    # When
    try:
        cache.put("entry", "anchored value")
        result = cache.get("entry")
    finally:
        cache.close()

    # Then
    assert result == "anchored value"
    assert (anchored_dir / "entry").read_text(encoding="utf-8") == "anchored value"
    assert not (cache_dir / "entry").exists()


def test_cache_put_invalid_unicode_raises_typed_error(tmp_path: Path) -> None:
    # Given
    cache = TranslationCache(tmp_path / "cache")

    # When
    try:
        with pytest.raises(ProviderUnavailableError, match="cache write failed"):
            cache.put("entry", "\ud800")
    finally:
        cache.close()

    # Then
    assert not (tmp_path / "cache" / "entry").exists()
