"""Cached adapter for Google's unofficial client=gtx web endpoint."""

from __future__ import annotations

import hashlib
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, final

import httpx2
from pydantic import RootModel, ValidationError

from epub_bilingual_translator.models import (
    LanguagePair,
    ProviderResponseError,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    import types

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_ENDPOINT: Final = "https://translate.googleapis.com/translate_a/single"
_TRANSIENT_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
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


class _GooglePayload(RootModel[list[JsonValue]]):
    pass


@dataclass(frozen=True, slots=True)
class GoogleConfig:
    """Provider cache and retry settings."""

    cache_dir: Path
    retries: int = 4


@final
class _TranslationCache:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._path: Path = path

    def get(self, cache_key: str) -> str | None:
        path = self._path / cache_key
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ProviderUnavailableError(
                detail=f"cache read failed: {error}",
            ) from error

    def put(self, cache_key: str, translated: str) -> None:
        destination = self._path / cache_key
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
            _ = temporary_path.replace(destination)
        except OSError as error:
            raise ProviderUnavailableError(
                detail=f"cache write failed: {error}",
            ) from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


@final
class GoogleWebTranslator:
    """Synchronous cached translator using the unofficial Google web API."""

    engine_id: str = "google-web-gtx-v1"

    def __init__(self, config: GoogleConfig) -> None:
        self._config: GoogleConfig = config
        self._cache: _TranslationCache = _TranslationCache(config.cache_dir)
        transport = httpx2.HTTPTransport(
            http2=True,
            retries=3,
            limits=_LIMITS,
            socket_options=_SOCKET_OPTIONS,
        )
        self._client: httpx2.Client = httpx2.Client(
            transport=transport,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "epub-bilingual-translator/0.1"},
        )

    def translate_html(self, value: str, pair: LanguagePair) -> str:
        """Translate one fragment, reusing only successful cached responses."""
        cache_key = self._cache_key(value, pair)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        translated = self._request_with_retries(value, pair)
        self._cache.put(cache_key, translated)
        return translated

    def _request_with_retries(self, value: str, pair: LanguagePair) -> str:
        last_detail = "no request attempted"
        for attempt in range(self._config.retries + 1):
            try:
                response = self._client.get(
                    _ENDPOINT,
                    params={
                        "client": "gtx",
                        "sl": pair.source,
                        "tl": pair.target,
                        "dt": "t",
                        "q": value,
                    },
                )
                _ = response.raise_for_status()
                return parse_google_translation(response.text)
            except httpx2.HTTPStatusError as error:
                last_detail = str(error)
                if error.response.status_code not in _TRANSIENT_STATUSES:
                    break
            except (httpx2.RequestError, ProviderResponseError) as error:
                last_detail = str(error)
            if attempt < self._config.retries:
                time.sleep(float(min(1 << attempt, 8)))
        raise ProviderUnavailableError(detail=last_detail)

    def _cache_key(self, value: str, pair: LanguagePair) -> str:
        material = f"{self.engine_id}\0{pair.source}\0{pair.target}\0{value}"
        return hashlib.sha256(material.encode()).hexdigest()

    def close(self) -> None:
        """Release the provider HTTP connection pool."""
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
