"""Repair provider markup within translated XHTML byte limits."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from defusedxml import ElementTree as DefusedET

from epub_bilingual_translator.html import split_balanced_html, visible_text
from epub_bilingual_translator.models import ArchiveError
from epub_bilingual_translator.xhtml import utf8_byte_length_bounded

if TYPE_CHECKING:
    from xml.etree import ElementTree as ET

_BARE_AMPERSAND: Final = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#[xX][0-9A-Fa-f]+;)",
)
_BARE_AMPERSAND_EXPANSION_BYTES: Final = len("amp;")


def repair_inner_markup(element: ET.Element, markup: str, max_bytes: int) -> None:
    """Replace one element's children with repaired provider markup within its limit."""
    if not _repaired_markup_fits(markup, max_bytes):
        raise ArchiveError(detail="translated XHTML exceeds limit")
    for child in list(element):
        element.remove(child)
    repaired = _BARE_AMPERSAND.sub("&amp;", markup)
    repaired = "".join(
        split_balanced_html(
            repaired,
            max(1, len(visible_text(repaired))),
            max_bytes,
        ),
    )
    wrapper = DefusedET.fromstring(
        f'<wrapper xmlns="http://www.w3.org/1999/xhtml">{repaired}</wrapper>',
    )
    element.text = wrapper.text
    for child in list(wrapper):
        wrapper.remove(child)
        element.append(child)


def _repaired_markup_fits(markup: str, max_bytes: int) -> bool:
    size = utf8_byte_length_bounded(markup, max_bytes)
    if size > max_bytes:
        return False
    for _ in _BARE_AMPERSAND.finditer(markup):
        size += _BARE_AMPERSAND_EXPANSION_BYTES
        if size > max_bytes:
            return False
    return True
