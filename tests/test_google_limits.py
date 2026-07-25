from __future__ import annotations

from typing import TYPE_CHECKING, Final, final, override

import httpx2
import pytest

from epub_bilingual_translator import epub, google, models
from epub_bilingual_translator.cache import TranslationCache
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


def serialized_google_url(value: str) -> httpx2.URL:
    return httpx2.Request(
        "GET",
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": value},
    ).url


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
    assert "request" in str(caught.value)
    assert value not in str(caught.value)
    assert not cache_entry(cache_dir, value).exists()


def test_raw_under_limit_but_percent_encoded_url_over_limit_skips_cache_and_http(
    tmp_path: Path,
) -> None:
    # Given
    value = "&" * (FRAGMENT_BYTE_LIMIT // 2)
    serialized_url = serialized_google_url(value)
    client = FakeHttpClient([])
    cache_dir = tmp_path / "cache"
    seed_cache = TranslationCache(cache_dir)
    seed_cache.put(cache_entry(cache_dir, value).name, "cached translation")
    seed_cache.close()

    # When
    with (
        translator(cache_dir, client) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(value, PAIR)

    # Then
    assert len(value.encode()) < FRAGMENT_BYTE_LIMIT
    assert len(str(serialized_url).encode()) > FRAGMENT_BYTE_LIMIT
    assert client.calls == 0
    assert value not in str(caught.value)


def test_request_uses_verified_serialized_url(tmp_path: Path) -> None:
    # Given
    value = "한글 & markup?"
    expected_url = serialized_google_url(value)
    client = FakeHttpClient([response(200, '[[["정상"]]]')])

    # When
    with translator(tmp_path / "cache", client) as provider:
        translated = provider.translate_html(value, PAIR)

    # Then
    assert translated == "정상"
    assert client.urls == [expected_url]


def test_request_at_exact_serialized_limit_is_allowed(tmp_path: Path) -> None:
    # Given
    empty_request_bytes = len(str(serialized_google_url("")).encode())
    value = "x" * (FRAGMENT_BYTE_LIMIT - empty_request_bytes)
    expected_url = serialized_google_url(value)
    client = FakeHttpClient([response(200, '[[["정상"]]]')])

    # When
    with translator(tmp_path / "cache", client) as provider:
        translated = provider.translate_html(value, PAIR)

    # Then
    assert len(str(expected_url).encode()) == FRAGMENT_BYTE_LIMIT
    assert translated == "정상"
    assert client.urls == [expected_url]


def test_request_one_byte_over_serialized_limit_is_rejected(tmp_path: Path) -> None:
    # Given
    empty_request_bytes = len(str(serialized_google_url("")).encode())
    value = "x" * (FRAGMENT_BYTE_LIMIT - empty_request_bytes + 1)
    client = FakeHttpClient([])

    # When
    with (
        translator(tmp_path / "cache", client) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(value, PAIR)

    # Then
    assert len(str(serialized_google_url(value)).encode()) == FRAGMENT_BYTE_LIMIT + 1
    assert client.calls == 0
    assert value not in str(caught.value)


def test_redirect_to_oversized_url_is_not_followed_or_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    value = "redirect me"
    oversized_url = f"https://translate.invalid/?q={'x' * FRAGMENT_BYTE_LIMIT}"
    observed_urls: list[httpx2.URL] = []

    def handle_request(request: httpx2.Request) -> httpx2.Response:
        observed_urls.append(request.url)
        return httpx2.Response(
            302,
            headers={"Location": oversized_url},
            request=request,
        )

    transport = httpx2.MockTransport(handle_request)

    def create_mock_transport(
        *,
        http2: bool,
        retries: int,
        limits: httpx2.Limits,
        socket_options: list[tuple[int, int, int]],
    ) -> httpx2.MockTransport:
        del http2, retries, limits, socket_options
        return transport

    monkeypatch.setattr(
        httpx2,
        "HTTPTransport",
        create_mock_transport,
    )
    cache_dir = tmp_path / "cache"

    # When
    with (
        google.GoogleWebTranslator(
            google.GoogleConfig(cache_dir=cache_dir, retries=0),
        ) as provider,
        pytest.raises(models.ProviderUnavailableError, match="HTTP 302"),
    ):
        _ = provider.translate_html(value, PAIR)

    # Then
    assert len(observed_urls) == 1
    assert len(str(observed_urls[0]).encode()) < FRAGMENT_BYTE_LIMIT
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
