"""Split long inline markup without cutting through active tags."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import assert_never, override

from epub_bilingual_translator.xhtml import utf8_byte_length_bounded


class TokenKind(StrEnum):
    """Kinds emitted by the standard-library HTML tokenizer."""

    START = "start"
    END = "end"
    EMPTY = "empty"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class HtmlToken:
    """Markup token with both serialized and visible representations."""

    kind: TokenKind
    raw: str
    visible: str = ""
    tag: str = ""


@dataclass(frozen=True, slots=True)
class HtmlChunkError(ValueError):
    """Requested fragment size is outside the supported range."""

    max_chars: int

    @override
    def __str__(self) -> str:
        return f"max_chars must be positive, got {self.max_chars}"


@dataclass(frozen=True, slots=True)
class HtmlChunkByteLimitError(ValueError):
    """Balanced fragments would exceed their cumulative byte limit."""

    max_bytes: int

    @override
    def __str__(self) -> str:
        return f"balanced HTML fragments exceed {self.max_bytes} bytes"


class _Tokenizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tokens: list[HtmlToken] = []

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        self.tokens.append(
            HtmlToken(TokenKind.START, self.get_starttag_text() or f"<{tag}>", tag=tag),
        )

    @override
    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        self.tokens.append(
            HtmlToken(
                TokenKind.EMPTY,
                self.get_starttag_text() or f"<{tag}/>",
                tag=tag,
            ),
        )

    @override
    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(HtmlToken(TokenKind.END, f"</{tag}>", tag=tag))

    @override
    def handle_data(self, data: str) -> None:
        self.tokens.append(HtmlToken(TokenKind.TEXT, data, visible=data))

    @override
    def handle_entityref(self, name: str) -> None:
        raw = f"&{name};"
        self.tokens.append(HtmlToken(TokenKind.TEXT, raw, visible=html.unescape(raw)))

    @override
    def handle_charref(self, name: str) -> None:
        raw = f"&#{name};"
        self.tokens.append(HtmlToken(TokenKind.TEXT, raw, visible=html.unescape(raw)))


class _TextReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @override
    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_text(value: str) -> str:
    """Return rendered text while ignoring inline markup."""
    reader = _TextReader()
    reader.feed(value)
    reader.close()
    return "".join(reader.parts)


def split_balanced_html(
    value: str,
    max_chars: int,
    max_bytes: int | None = None,
) -> list[str]:
    """Split markup near text boundaries and balance active tags per fragment."""
    if max_chars < 1:
        raise HtmlChunkError(max_chars=max_chars)
    if max_bytes is not None and max_bytes < 1:
        raise HtmlChunkByteLimitError(max_bytes=max_bytes)
    tokenizer = _Tokenizer()
    tokenizer.feed(value)
    tokenizer.close()
    return _build_chunks(tokenizer.tokens, max_chars, max_bytes)


def _build_chunks(
    tokens: list[HtmlToken],
    max_chars: int,
    max_bytes: int | None,
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    active: list[HtmlToken] = []
    current_length = 0
    total_bytes = 0

    def flush() -> None:
        nonlocal current, current_length, total_bytes
        if current_length == 0:
            return
        closers = [f"</{token.tag}>" for token in reversed(active)]
        parts = [*current, *closers]
        if max_bytes is not None:
            fragment_bytes = 0
            for part in parts:
                fragment_bytes += utf8_byte_length_bounded(
                    part,
                    max_bytes - total_bytes - fragment_bytes,
                )
                if total_bytes + fragment_bytes > max_bytes:
                    raise HtmlChunkByteLimitError(max_bytes=max_bytes)
            total_bytes += fragment_bytes
        chunks.append("".join(parts))
        current = [token.raw for token in active]
        current_length = 0

    for index, token in enumerate(tokens):
        match token.kind:
            case TokenKind.START:
                subtree_length = _subtree_visible_length(tokens, index)
                if (
                    current_length > 0
                    and subtree_length <= max_chars
                    and current_length + subtree_length > max_chars
                ):
                    flush()
                current.append(token.raw)
                active.append(token)
            case TokenKind.EMPTY:
                current.append(token.raw)
            case TokenKind.END:
                current.append(token.raw)
                _remove_active(active, token.tag)
            case TokenKind.TEXT:
                raw = token.raw
                visible = token.visible
                while raw:
                    capacity = max_chars - current_length
                    if capacity == 0:
                        flush()
                        capacity = max_chars
                    raw_part, visible_part, raw = _take_text(raw, visible, capacity)
                    current.append(raw_part)
                    current_length += len(visible_part)
                    if raw and current_length == max_chars:
                        flush()
                    visible = raw
            case unreachable:
                assert_never(unreachable)
    flush()
    return chunks


def _take_text(raw: str, visible: str, limit: int) -> tuple[str, str, str]:
    if raw != visible:
        return raw, visible, ""
    if len(raw) <= limit:
        return raw, raw, ""
    window = raw[:limit]
    boundaries = list(re.finditer(r"(?<=[.!?\u3002\uff01\uff1f])\s+|\s+", window))
    cut = boundaries[-1].end() if boundaries else limit
    if cut < max(1, limit // 3):
        cut = limit
    return raw[:cut], raw[:cut], raw[cut:]


def _remove_active(active: list[HtmlToken], tag: str) -> None:
    for index in range(len(active) - 1, -1, -1):
        if active[index].tag == tag:
            del active[index]
            return


def _subtree_visible_length(tokens: list[HtmlToken], start: int) -> int:
    depth = 0
    length = 0
    for token in tokens[start:]:
        match token.kind:
            case TokenKind.START:
                depth += 1
            case TokenKind.END:
                depth -= 1
                if depth == 0:
                    return length
            case TokenKind.TEXT:
                length += len(token.visible)
            case TokenKind.EMPTY:
                pass
            case unreachable:
                assert_never(unreachable)
    return length
