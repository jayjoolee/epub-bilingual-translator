"""Cached adapter for Google's unofficial client=gtx web endpoint."""

from __future__ import annotations

import hashlib
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, Self, final

import httpx2
from pydantic import RootModel, ValidationError

from epub_bilingual_translator.cache import TranslationCache
from epub_bilingual_translator.models import (
    LanguagePair,
    ProviderResponseError,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    import types
    from contextlib import AbstractContextManager
    from pathlib import Path

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_ENDPOINT: Final = "https://translate.googleapis.com/translate_a/single"
_TRANSIENT_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
MAX_PROVIDER_REQUEST_BYTES: Final = 32 * 1024
MAX_PROVIDER_RESPONSE_BYTES: Final = 1024 * 1024
_LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)
_SOCKET_OPTIONS: Final[list[tuple[int, int, int]]] = [
    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
]
type Sleeper = Callable[[float], None]


class _GooglePayload(RootModel[list[JsonValue]]):
    pass


@dataclass(frozen=True, slots=True)
class GoogleConfig:
    """Provider cache and retry settings."""

    cache_dir: Path
    retries: int = 4


class HttpClient(Protocol):
    """Narrow synchronous client seam for deterministic provider tests."""

    def stream(
        self,
        method: str,
        url: httpx2.URL,
    ) -> AbstractContextManager[httpx2.Response]:
        """Open one streaming translation endpoint response."""
        ...

    def close(self) -> None:
        """Release client resources."""
        ...


@final
class GoogleWebTranslator:
    """Synchronous cached translator using the unofficial Google web API."""

    engine_id: str = "google-web-gtx-v1"

    def __init__(
        self,
        config: GoogleConfig,
        client: HttpClient | None = None,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._config: GoogleConfig = config
        self._cache: TranslationCache = TranslationCache(config.cache_dir)
        self._sleeper: Sleeper = sleeper
        self._client: HttpClient = client or _create_http_client()

    def translate_html(self, value: str, pair: LanguagePair) -> str:
        """Translate one fragment, reusing only successful cached responses."""
        request = _provider_request(value, pair)
        if len(str(request.url).encode()) > MAX_PROVIDER_REQUEST_BYTES:
            detail = "serialized request exceeds provider byte limit"
            raise ProviderUnavailableError(detail=detail)
        cache_key = self._cache_key(value, pair)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        translated = self._request_with_retries(request)
        self._cache.put(cache_key, translated)
        return translated

    def _request_with_retries(self, request: httpx2.Request) -> str:
        last_detail = "no request attempted"
        for attempt in range(self._config.retries + 1):
            try:
                with self._client.stream(
                    "GET",
                    request.url,
                ) as response:
                    _ = response.raise_for_status()
                    body = _read_bounded_response(response)
                return parse_google_translation(_decode_response(body))
            except httpx2.HTTPStatusError as error:
                status_code = error.response.status_code
                last_detail = f"HTTP {status_code} from translation provider"
                if status_code not in _TRANSIENT_STATUSES:
                    break
            except httpx2.RequestError as error:
                last_detail = f"network request failed ({type(error).__name__})"
            except ProviderResponseError:
                last_detail = "translation provider returned an invalid response"
                break
            if attempt < self._config.retries:
                self._sleeper(float(min(1 << attempt, 8)))
        raise ProviderUnavailableError(detail=last_detail)

    def _cache_key(self, value: str, pair: LanguagePair) -> str:
        material = f"{self.engine_id}\0{pair.source}\0{pair.target}\0{value}"
        return hashlib.sha256(material.encode()).hexdigest()

    def close(self) -> None:
        """Release the provider HTTP connection pool."""
        try:
            self._cache.close()
        finally:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        del error_type, error, traceback
        self.close()


def _create_http_client() -> httpx2.Client:
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=_LIMITS,
        socket_options=_SOCKET_OPTIONS,
    )
    return httpx2.Client(
        transport=transport,
        timeout=_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": "epub-bilingual-translator/0.1"},
    )


def _provider_request(value: str, pair: LanguagePair) -> httpx2.Request:
    return httpx2.Request(
        "GET",
        _ENDPOINT,
        params={
            "client": "gtx",
            "sl": pair.source,
            "tl": pair.target,
            "dt": "t",
            "q": value,
        },
    )


def _read_bounded_response(response: httpx2.Response) -> bytes | None:
    declared_lengths = response.headers.get_list("content-length")
    if len(declared_lengths) > 1:
        return None
    if declared_lengths:
        try:
            declared_bytes = int(declared_lengths[0])
        except ValueError:
            return None
        if declared_bytes < 0 or declared_bytes > MAX_PROVIDER_RESPONSE_BYTES:
            return None
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(chunk) > MAX_PROVIDER_RESPONSE_BYTES - len(body):
            return None
        body.extend(chunk)
    return bytes(body)


def _decode_response(body: bytes | None) -> str:
    if body is None:
        detail = "response exceeded provider byte limit"
        raise ProviderResponseError(detail=detail)
    try:
        return body.decode()
    except UnicodeDecodeError as error:
        detail = "response was not valid UTF-8"
        raise ProviderResponseError(detail=detail) from error


def parse_google_translation(value: str) -> str:
    """Parse translated segments from a validated Google response."""
    try:
        payload = _GooglePayload.model_validate_json(value)
    except ValidationError as error:
        raise ProviderResponseError(detail="response was not valid JSON") from error
    if not payload.root:
        raise ProviderResponseError(detail="response was empty")
    segments = payload.root[0]
    if not isinstance(segments, list):
        raise ProviderResponseError(detail="translation segments were missing")
    pieces = [
        segment[0]
        for segment in segments
        if isinstance(segment, list) and segment and isinstance(segment[0], str)
    ]
    if not pieces:
        raise ProviderResponseError(detail="translated text was missing")
    return "".join(pieces)
