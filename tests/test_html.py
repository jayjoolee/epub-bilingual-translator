from __future__ import annotations

from xml.etree import ElementTree as ET

from epub_bilingual_translator import html as html_tools


def test_long_formatted_paragraph_is_split_into_balanced_chunks() -> None:
    # Given
    source = (
        "Sentence one has <em>important words and a long inline section</em>. "
        'Sentence two has <a href="https://example.com">a link</a>. '
        "Sentence three finishes the paragraph cleanly."
    )

    # When
    chunks = html_tools.split_balanced_html(source, max_chars=55)

    # Then
    assert len(chunks) > 1
    assert html_tools.visible_text("".join(chunks)) == html_tools.visible_text(source)
    for chunk in chunks:
        assert len(html_tools.visible_text(chunk)) <= 55
        _ = ET.fromstring(f"<root>{chunk}</root>")
