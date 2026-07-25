from __future__ import annotations

from typing import TYPE_CHECKING, Final, final, override

import httpx2
import pytest

from epub_bilingual_translator import epub, models
from tests.helpers import write_epub
from tests.test_epub import options
from tests.test_google import (
    PAIR,
    SENTINEL,
    FakeHttpClient,
    cache_entry,
    response,
    translator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

FRAGMENT_BYTE_LIMIT: Final = 32 * 1024
RESPONSE_BYTE_LIMIT: Final = 1024 * 1024


@final
class TrackingStream(httpx2.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks: tuple[bytes, ...] = chunks
        self.iterations: int = 0
        self.closed: bool = False

    @override
    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.iterations += 1
            yield chunk

    @override
    def close(self) -> None:
        self.closed = True


def streamed_response(
    stream: TrackingStream,
    *,
    content_length: int | None = None,
) -> httpx2.Response:
    headers = (
        {"Content-Length": str(content_length)} if content_length is not None else None
    )
    request = httpx2.Request(
        "GET",
        f"https://translate.invalid/?q={SENTINEL}",
    )
    return httpx2.Response(
        200,
        headers=headers,
        stream=stream,
        request=request,
    )


def translation_body_at_size(size: int) -> bytes:
    prefix = b'[[["'
    suffix = b'","hello",null,null,1]],null,"en"]'
    return prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix


def test_oversized_serialized_fragment_is_rejected_before_cache_or_http(
    tmp_path: Path,
) -> None:
    # Given
    value = f'<span title="{"x" * FRAGMENT_BYTE_LIMIT}">visible</span>'
    client = FakeHttpClient([response(200, '[[["ok"]]]')])
    cache_dir = tmp_path / "cache"

    # When
    with (
        translator(cache_dir, client) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(value, PAIR)

    # Then
    assert client.calls == 0
    assert "fragment" in str(caught.value)
    assert value not in str(caught.value)
    assert not cache_entry(cache_dir, value).exists()


def test_oversized_serialized_fragment_removes_epub_output_and_temp(
    tmp_path: Path,
) -> None:
    # Given
    source = tmp_path / "large-attribute.epub"
    output = tmp_path / "translated.epub"
    paragraph = f'<span title="{"x" * FRAGMENT_BYTE_LIMIT}">visible</span>'
    write_epub(source, paragraph)
    client = FakeHttpClient([response(200, '[[["ok"]]]')])

    # When
    with (
        translator(tmp_path / "cache", client) as provider,
        pytest.raises(models.EpubTranslationError),
    ):
        _ = epub.translate_epub(options(source, output), provider)

    # Then
    assert client.calls == 0
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_normal_unicode_fragment_under_serialized_limit_remains_viable(
    tmp_path: Path,
) -> None:
    # Given
    value = "한" * 2_800
    client = FakeHttpClient([response(200, '[[["정상"]]]')])

    # When
    with translator(tmp_path / "cache", client) as provider:
        translated = provider.translate_html(value, PAIR)

    # Then
    assert translated == "정상"
    assert client.calls == 1


def test_declared_oversized_response_is_permanent_closed_and_not_read(
    tmp_path: Path,
) -> None:
    # Given
    stream = TrackingStream((b"not-read",))
    client = FakeHttpClient(
        [streamed_response(stream, content_length=RESPONSE_BYTE_LIMIT + 1)],
    )
    sleeps: list[float] = []
    cache_dir = tmp_path / "cache"

    # When
    with (
        translator(
            cache_dir,
            client,
            retries=3,
            sleeper=sleeps.append,
        ) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(SENTINEL, PAIR)

    # Then
    assert client.calls == 1
    assert sleeps == []
    assert stream.iterations == 0
    assert stream.closed
    assert SENTINEL not in str(caught.value)
    assert "translate.invalid" not in str(caught.value)
    assert not cache_entry(cache_dir, SENTINEL).exists()


def test_chunked_oversized_response_is_permanent_closed_and_not_cached(
    tmp_path: Path,
) -> None:
    # Given
    stream = TrackingStream((b"x" * RESPONSE_BYTE_LIMIT, b"x"))
    client = FakeHttpClient([streamed_response(stream)])
    sleeps: list[float] = []
    cache_dir = tmp_path / "cache"

    # When
    with (
        translator(
            cache_dir,
            client,
            retries=3,
            sleeper=sleeps.append,
        ) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(SENTINEL, PAIR)

    # Then
    assert client.calls == 1
    assert sleeps == []
    assert stream.iterations == 2
    assert stream.closed
    assert SENTINEL not in str(caught.value)
    assert not cache_entry(cache_dir, SENTINEL).exists()


def test_response_exactly_at_byte_limit_is_decoded_cached_and_closed(
    tmp_path: Path,
) -> None:
    # Given
    body = translation_body_at_size(RESPONSE_BYTE_LIMIT)
    stream = TrackingStream((body[:500_000], body[500_000:]))
    client = FakeHttpClient(
        [streamed_response(stream, content_length=len(body))],
    )
    cache_dir = tmp_path / "cache"

    # When
    with translator(cache_dir, client) as provider:
        translated = provider.translate_html("hello", PAIR)

    # Then
    assert len(translated) == RESPONSE_BYTE_LIMIT - 38
    assert stream.iterations == 2
    assert stream.closed
    assert cache_entry(cache_dir, "hello").read_text(encoding="utf-8") == translated
