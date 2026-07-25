"""Typed values shared across the translation boundaries."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, Self, override

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import types


@dataclass(frozen=True, slots=True)
class LanguagePair:
    """Source and target language codes sent to the provider."""

    source: str
    target: str


class Options(BaseModel):
    """Validated settings for one EPUB translation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    input_path: pathlib.Path
    output_path: pathlib.Path
    pair: LanguagePair
    max_chars: int = Field(default=2_800, ge=200, le=5_000)


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Observable result of a completed translation."""

    output_path: pathlib.Path
    translated_blocks: int
    translated_chunks: int


class Translator(Protocol):
    """Translate one balanced inline-markup fragment."""

    engine_id: str

    def translate_html(self, value: str, pair: LanguagePair) -> str:
        """Return translated markup for one fragment."""
        ...


class ManagedTranslator(Translator, Protocol):
    """Translator whose I/O resources have an explicit lifetime."""

    def __enter__(self) -> Self:
        """Open the provider lifetime."""
        ...

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Close the provider lifetime."""
        ...


class EpubTranslationError(RuntimeError):
    """Base error safe to show at the CLI boundary."""


@dataclass(frozen=True, slots=True)
class InputPathError(EpubTranslationError):
    """Input path does not identify a readable EPUB."""

    path: pathlib.Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.path}"


@dataclass(frozen=True, slots=True)
class OutputPathError(EpubTranslationError):
    """Output path would overwrite or cannot accept a new EPUB."""

    path: pathlib.Path
    reason: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.path}"


@dataclass(frozen=True, slots=True)
class DrmProtectedError(EpubTranslationError):
    """EPUB encryption metadata is present and will not be bypassed."""

    member: str

    @override
    def __str__(self) -> str:
        return f"encrypted EPUB refused ({self.member}); DRM is not bypassed"


@dataclass(frozen=True, slots=True)
class ArchiveError(EpubTranslationError):
    """EPUB archive or XHTML content could not be processed safely."""

    detail: str

    @override
    def __str__(self) -> str:
        return f"invalid EPUB: {self.detail}"


@dataclass(frozen=True, slots=True)
class ProviderUnavailableError(EpubTranslationError):
    """Unofficial provider failed without pretending translation succeeded."""

    detail: str

    @override
    def __str__(self) -> str:
        return (
            "Google's unofficial client=gtx endpoint failed; "
            f"the source was preserved and no output was created: {self.detail}"
        )


@dataclass(frozen=True, slots=True)
class ProviderResponseError(EpubTranslationError):
    """Provider response did not contain a valid translation."""

    detail: str

    @override
    def __str__(self) -> str:
        return f"invalid Google translation response: {self.detail}"
