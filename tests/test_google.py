from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never, final

import httpx2
import pytest

from epub_bilingual_translator import google, models

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping

PAIR = models.LanguagePair(source="en", target="ko")
SENTINEL = "PRIVATE_FRAGMENT_SENTINEL"
SUCCESS_BODY = '[[["안녕","hello",null,null,1]],null,"en"]'

type HttpOutcome = httpx2.Response | httpx2.RequestError


@final
class FakeHttpClient:
    def __init__(self, outcomes: list[HttpOutcome]) -> None:
        self.outcomes: list[HttpOutcome] = outcomes
        self.calls: int = 0
        self.closed: bool = False

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str],
    ) -> Generator[httpx2.Response]:
        del method, url, params
        response = self._next_response()
        try:
            yield response
        finally:
            response.close()

    def _next_response(self) -> httpx2.Response:
        self.calls += 1
        if not self.outcomes:
            detail = "unexpected HTTP request"
            raise AssertionError(detail)
        outcome = self.outcomes.pop(0)
        match outcome:
            case httpx2.Response():
                return outcome
            case httpx2.RequestError():
                raise outcome
            case unreachable:
                assert_never(unreachable)

    def close(self) -> None:
        self.closed = True


def response(status_code: int, body: str = "") -> httpx2.Response:
    request = httpx2.Request(
        "GET",
        f"https://translate.invalid/?q={SENTINEL}",
    )
    return httpx2.Response(status_code, content=body, request=request)


def cache_entry(cache_dir: Path, value: str) -> Path:
    material = f"google-web-gtx-v1\0{PAIR.source}\0{PAIR.target}\0{value}"
    return cache_dir / hashlib.sha256(material.encode()).hexdigest()


def translator(
    cache_dir: Path,
    client: FakeHttpClient,
    *,
    retries: int = 0,
    sleeper: Callable[[float], None] | None = None,
) -> google.GoogleWebTranslator:
    return google.GoogleWebTranslator(
        google.GoogleConfig(cache_dir=cache_dir, retries=retries),
        client=client,
        sleeper=sleeper or no_sleep,
    )


def no_sleep(seconds: float) -> None:
    del seconds


def test_cache_miss_writes_success_and_next_instance_hits_cache(
    tmp_path: Path,
) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    first_client = FakeHttpClient([response(200, SUCCESS_BODY)])

    # When
    with translator(cache_dir, first_client) as first:
        first_result = first.translate_html("hello", PAIR)
    second_client = FakeHttpClient([])
    with translator(cache_dir, second_client) as second:
        second_result = second.translate_html("hello", PAIR)

    # Then
    assert first_result == "안녕"
    assert second_result == "안녕"
    assert first_client.calls == 1
    assert second_client.calls == 0
    assert cache_entry(cache_dir, "hello").read_text(encoding="utf-8") == "안녕"
    if os.name == "posix":
        assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(cache_entry(cache_dir, "hello").stat().st_mode) == 0o600


def test_malformed_provider_response_is_typed_and_not_cached(
    tmp_path: Path,
) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    client = FakeHttpClient([response(200, "not-json")])
    sleeps: list[float] = []

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
    assert SENTINEL not in str(caught.value)
    assert not cache_entry(cache_dir, SENTINEL).exists()


def test_permanent_http_failure_is_sanitized_without_retry(
    tmp_path: Path,
) -> None:
    # Given
    client = FakeHttpClient([response(403)])
    sleeps: list[float] = []

    # When
    with (
        translator(
            tmp_path / "cache", client, retries=3, sleeper=sleeps.append
        ) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(SENTINEL, PAIR)

    # Then
    assert client.calls == 1
    assert sleeps == []
    assert "HTTP 403" in str(caught.value)
    assert SENTINEL not in str(caught.value)
    assert "translate.invalid" not in str(caught.value)


def test_transient_retry_exhaustion_is_sanitized_without_real_sleep(
    tmp_path: Path,
) -> None:
    # Given
    client = FakeHttpClient([response(429), response(429), response(429)])
    sleeps: list[float] = []

    # When
    with (
        translator(
            tmp_path / "cache", client, retries=2, sleeper=sleeps.append
        ) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(SENTINEL, PAIR)

    # Then
    assert client.calls == 3
    assert sleeps == [1.0, 2.0]
    assert "HTTP 429" in str(caught.value)
    assert SENTINEL not in str(caught.value)


def test_request_error_detail_is_sanitized(tmp_path: Path) -> None:
    # Given
    request = httpx2.Request(
        "GET",
        f"https://translate.invalid/?q={SENTINEL}",
    )
    error = httpx2.ConnectError(f"connect failed: {SENTINEL}", request=request)
    client = FakeHttpClient([error])

    # When
    with (
        translator(tmp_path / "cache", client) as provider,
        pytest.raises(models.ProviderUnavailableError) as caught,
    ):
        _ = provider.translate_html(SENTINEL, PAIR)

    # Then
    assert "ConnectError" in str(caught.value)
    assert SENTINEL not in str(caught.value)
    assert "translate.invalid" not in str(caught.value)


@pytest.mark.parametrize("entry_kind", ["invalid-utf8", "symlink", "directory"])
def test_corrupt_cache_entry_returns_typed_error(
    tmp_path: Path,
    entry_kind: Literal["invalid-utf8", "symlink", "directory"],
) -> None:
    # Given
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    entry = cache_entry(cache_dir, "hello")
    match entry_kind:
        case "invalid-utf8":
            _ = entry.write_bytes(b"\xff\xfe")
        case "symlink":
            outside = tmp_path / "outside"
            _ = outside.write_text("secret", encoding="utf-8")
            entry.symlink_to(outside)
        case "directory":
            entry.mkdir()
        case unreachable:
            assert_never(unreachable)
    client = FakeHttpClient([])

    # When
    with (
        translator(cache_dir, client) as provider,
        pytest.raises(models.ProviderUnavailableError),
    ):
        _ = provider.translate_html("hello", PAIR)

    # Then
    assert client.calls == 0


def test_unsafe_cache_directory_is_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.chdir(tmp_path)

    # When
    with (
        pytest.raises(models.ProviderUnavailableError),
        translator(Path("relative-cache"), FakeHttpClient([])),
    ):
        pass


def test_symlink_cache_directory_is_typed(tmp_path: Path) -> None:
    # Given
    real_dir = tmp_path / "real-cache"
    real_dir.mkdir()
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(real_dir, target_is_directory=True)

    # When
    with (
        pytest.raises(models.ProviderUnavailableError),
        translator(cache_link, FakeHttpClient([])),
    ):
        pass
