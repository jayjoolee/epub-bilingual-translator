from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

import pytest

from epub_bilingual_translator import epub, models
from epub_bilingual_translator import html as html_tools
from tests.helpers import XHTML, ZipMember, read_paragraphs, write_epub

if TYPE_CHECKING:
    import pathlib

FONT_OBFUSCATION_ALGORITHM = "http://www.idpf.org/2008/embedding"
FONT_DATA = b"obfuscated-font-bytes-\x00\x01\x02"


class RecordingTranslator:
    engine_id: str = "recording-test"

    def __init__(self, max_chars: int) -> None:
        self.max_chars: int = max_chars
        self.requests: list[str] = []

    def translate_html(
        self,
        value: str,
        pair: models.LanguagePair,
    ) -> str:
        del pair
        assert len(html_tools.visible_text(value)) <= self.max_chars
        self.requests.append(value)
        return value.replace("Sentence", "문장").replace("important", "중요")


class BareAmpersandTranslator:
    engine_id: str = "bare-ampersand-test"

    def translate_html(
        self,
        value: str,
        pair: models.LanguagePair,
    ) -> str:
        del pair
        return value.replace("A and P", "A&P")


class MissingEndTagTranslator:
    engine_id: str = "missing-end-tag-test"

    def translate_html(
        self,
        value: str,
        pair: models.LanguagePair,
    ) -> str:
        del pair
        return value.replace("</span>", "")


def options(
    source: pathlib.Path,
    output: pathlib.Path,
    *,
    max_chars: int = 2_800,
) -> models.Options:
    return models.Options(
        input_path=source,
        output_path=output,
        pair=models.LanguagePair(source="en", target="ko"),
        max_chars=max_chars,
    )


def test_translation_first_output_preserves_long_markup_links_and_order(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    paragraph = " ".join(
        "".join(
            (
                f"Sentence {index} contains <em>important</em> information and ",
                f'<a href="https://example.com/{index}">a reference</a>.',
            ),
        )
        for index in range(60)
    )
    write_epub(source, paragraph)
    translator = RecordingTranslator(max_chars=220)

    # When
    result = epub.translate_epub(options(source, output, max_chars=220), translator)

    # Then
    assert result.translated_blocks == 1
    assert result.translated_chunks > 1
    paragraphs = read_paragraphs(output)
    assert len(paragraphs) == 2
    assert "문장" in "".join(paragraphs[0].itertext())
    assert "Sentence" in "".join(paragraphs[1].itertext())
    assert len(list(paragraphs[0].iter(f"{{{XHTML}}}a"))) == 60
    assert len(list(paragraphs[1].iter(f"{{{XHTML}}}a"))) == 60
    with zipfile.ZipFile(output) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.testzip() is None


def test_bare_ampersand_from_provider_is_repaired(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "She worked at A and P for many years.")

    # When
    _ = epub.translate_epub(options(source, output), BareAmpersandTranslator())

    # Then
    translated = read_paragraphs(output)[0]
    assert "".join(translated.itertext()) == "She worked at A&P for many years."


def test_missing_provider_closing_tag_is_repaired(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "<span>Her relatives lived nearby.</span>")

    # When
    _ = epub.translate_epub(options(source, output), MissingEndTagTranslator())

    # Then
    translated = read_paragraphs(output)[0]
    assert "".join(translated.itertext()) == "Her relatives lived nearby."
    assert len(list(translated.iter(f"{{{XHTML}}}span"))) == 1


def test_encryption_metadata_is_refused_before_translation(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "protected.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "Secret.", encrypted=True)
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.DrmProtectedError):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()


def test_standard_font_obfuscation_is_preserved_without_decryption(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "font-obfuscated.epub"
    output = tmp_path / "translated.epub"
    encryption_xml = _encryption_xml(FONT_OBFUSCATION_ALGORITHM)
    write_epub(
        source,
        "Sentence with an embedded font.",
        encryption_xml=encryption_xml,
        extra_members=(
            ZipMember("OEBPS/fonts/fixture.otf", FONT_DATA),
            ZipMember("OEBPS/images/cover.jpg", b"image-bytes-\xff\xd8\xff"),
        ),
    )
    translator = RecordingTranslator(max_chars=2_800)

    # When
    result = epub.translate_epub(options(source, output), translator)

    # Then
    assert result.translated_blocks == 1
    with (
        zipfile.ZipFile(source) as source_archive,
        zipfile.ZipFile(output) as output_archive,
    ):
        assert output_archive.read("META-INF/encryption.xml") == source_archive.read(
            "META-INF/encryption.xml",
        )
        assert output_archive.read("OEBPS/fonts/fixture.otf") == source_archive.read(
            "OEBPS/fonts/fixture.otf",
        )
        assert output_archive.read("OEBPS/images/cover.jpg") == source_archive.read(
            "OEBPS/images/cover.jpg",
        )


def test_ambiguous_casefold_encryption_metadata_is_refused_before_translation(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "ambiguous-encryption.epub"
    output = tmp_path / "translated.epub"
    write_epub(
        source,
        "Secret.",
        encryption_xml=_encryption_xml(FONT_OBFUSCATION_ALGORITHM),
        extra_members=(
            ZipMember("OEBPS/fonts/fixture.otf", FONT_DATA),
            ZipMember(
                "meta-inf/ENCRYPTION.XML",
                _encryption_xml("http://www.w3.org/2001/04/xmlenc#aes256-cbc"),
            ),
        ),
    )
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.DrmProtectedError):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()


@pytest.mark.parametrize(
    "algorithm",
    [None, "http://www.w3.org/2001/04/xmlenc#aes256-cbc"],
)
def test_non_obfuscation_encryption_method_is_refused(
    tmp_path: pathlib.Path,
    algorithm: str | None,
) -> None:
    # Given
    source = tmp_path / "protected.epub"
    output = tmp_path / "translated.epub"
    write_epub(
        source,
        "Secret.",
        encryption_xml=_encryption_xml(algorithm),
        extra_members=(ZipMember("OEBPS/fonts/fixture.otf", FONT_DATA),),
    )
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.DrmProtectedError):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()


def test_source_path_cannot_be_used_as_output(tmp_path: pathlib.Path) -> None:
    # Given
    source = tmp_path / "source.epub"
    write_epub(source, "Keep me.")

    # When
    with pytest.raises(models.OutputPathError):
        _ = epub.translate_epub(options(source, source), BareAmpersandTranslator())

    # Then
    with zipfile.ZipFile(source) as archive:
        assert archive.testzip() is None


def _encryption_xml(algorithm: str | None) -> bytes:
    method = (
        f'<enc:EncryptionMethod Algorithm="{algorithm}"/>'
        if algorithm is not None
        else "<enc:EncryptionMethod/>"
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    {method}
    <enc:CipherData>
      <enc:CipherReference URI="OEBPS/fonts/fixture.otf"/>
    </enc:CipherData>
  </enc:EncryptedData>
</encryption>""".encode()
