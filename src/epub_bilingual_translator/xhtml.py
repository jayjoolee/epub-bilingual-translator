"""Bound translated XHTML fragments and serialized documents."""

from __future__ import annotations

from typing import TYPE_CHECKING, BinaryIO, Final
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from defusedxml import ElementTree as DefusedET

from epub_bilingual_translator.models import ArchiveError

if TYPE_CHECKING:
    from collections.abc import Iterator

_SERIALIZATION_CHUNK_CHARACTERS: Final = 64 * 1024


class _XhtmlLimitExceededError(Exception):
    pass


def parse_xhtml_bounded(
    source: BinaryIO,
    *,
    max_nesting: int,
    max_elements: int,
) -> ET.Element:
    """Parse one XHTML document while enforcing structural limits."""
    root: ET.Element | None = None
    depth = 0
    elements = 0
    for event, element in _iterparse_xhtml(source):
        if event == "start":
            if root is None:
                root = element
            depth += 1
            elements += 1
            if depth > max_nesting:
                raise ArchiveError(detail="XHTML nesting exceeds limit")
            if elements > max_elements:
                raise ArchiveError(detail="XHTML element count exceeds limit")
        else:
            depth -= 1
    if root is None:
        raise ArchiveError(detail="malformed XML content")
    return root


def _iterparse_xhtml(source: BinaryIO) -> Iterator[tuple[str, ET.Element]]:
    events: Iterator[tuple[str, ET.Element]] = DefusedET.iterparse(
        source,
        events=("start", "end"),
    )
    return events


def utf8_byte_length_bounded(value: str, limit: int) -> int:
    """Count UTF-8 bytes without making an encoded copy beyond the remaining limit."""
    size = 0
    try:
        for start in range(0, len(value), _SERIALIZATION_CHUNK_CHARACTERS):
            size += len(
                value[start : start + _SERIALIZATION_CHUNK_CHARACTERS].encode("utf-8"),
            )
            if size > limit:
                return size
    except UnicodeEncodeError:
        raise ArchiveError(detail="translated XHTML contains invalid Unicode") from None
    return size


class _BoundedXhtmlSink:
    def __init__(self, limit: int) -> None:
        self._limit: int = limit
        self._data: bytearray = bytearray()

    def write(self, data: str) -> int:
        for start in range(0, len(data), _SERIALIZATION_CHUNK_CHARACTERS):
            encoded = data[start : start + _SERIALIZATION_CHUNK_CHARACTERS].encode()
            if len(self._data) + len(encoded) > self._limit:
                raise _XhtmlLimitExceededError
            self._data.extend(encoded)
        return len(data)

    def to_bytes(self) -> bytes:
        return bytes(self._data)


def serialize_document_bounded(root: ET.Element, limit: int) -> bytes:
    """Serialize XHTML incrementally before materializing the bounded result."""
    sink = _BoundedXhtmlSink(limit)
    try:
        ET.ElementTree(root).write(
            sink,
            encoding="unicode",
            xml_declaration=True,
            short_empty_elements=True,
        )
    except _XhtmlLimitExceededError:
        raise ArchiveError(detail="translated XHTML exceeds limit") from None
    except UnicodeEncodeError:
        raise ArchiveError(detail="translated XHTML contains invalid Unicode") from None
    return sink.to_bytes()


def inner_markup(element: ET.Element) -> str:
    """Return one element's child markup while preserving escaped leading text."""
    parts = [escape(element.text or "")]
    parts.extend(
        ET.tostring(child, encoding="unicode", short_empty_elements=True)
        for child in element
    )
    return "".join(parts)
