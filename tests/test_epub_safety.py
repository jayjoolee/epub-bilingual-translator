from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING, Self
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from epub_bilingual_translator import cli, epub, models
from tests.helpers import XHTML, read_paragraphs, write_epub, write_epub_with_chapter
from tests.test_epub import RecordingTranslator, options

if TYPE_CHECKING:
    import pathlib
    import types


class DestinationRaceTranslator:
    engine_id: str = "destination-race"

    def __init__(self, destination: pathlib.Path) -> None:
        self.destination: pathlib.Path = destination

    def translate_html(self, value: str, pair: models.LanguagePair) -> str:
        del pair
        _ = self.destination.write_bytes(b"sentinel")
        return value


@pytest.mark.parametrize(
    "opt_out",
    ['translate="no"', 'class="body notranslate extra"'],
)
def test_nested_opt_out_excludes_whole_block_from_provider(
    tmp_path: pathlib.Path,
    opt_out: str,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(
        source,
        f"Allowed.</p><p>Before <span {opt_out}>Private.</span> After.",
    )
    translator = RecordingTranslator(max_chars=2_800)

    # When
    result = epub.translate_epub(options(source, output), translator)

    # Then
    assert result.translated_blocks == 1
    assert len(translator.requests) == 1
    paragraphs = read_paragraphs(output)
    assert len(paragraphs) == 3
    assert paragraphs[2].get("data-epub-translation") is None


def test_translation_does_not_duplicate_original_inter_sibling_tail(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "Alpha.</p>TAIL<span>Sibling.</span><p>")

    # When
    _ = epub.translate_epub(
        options(source, output),
        RecordingTranslator(max_chars=2_800),
    )

    # Then
    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("OEBPS/chapter.xhtml"))
    body = root.find(f"{{{XHTML}}}body")
    assert body is not None
    assert ET.tostring(body, encoding="unicode").count("TAIL") == 1
    paragraphs = list(body)
    assert paragraphs[0].tail is None
    assert paragraphs[1].tail == "TAIL"


def test_hostile_entity_declaration_returns_concise_cli_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "hostile.epub"
    output = tmp_path / "translated.epub"
    chapter = f"""<?xml version="1.0"?>
<!DOCTYPE html [<!ENTITY attack "ENTITY_SENTINEL">]>
<html xmlns="{XHTML}"><body><p>&attack;</p></body></html>""".encode()
    write_epub_with_chapter(source, chapter)
    monkeypatch.setattr(cli, "create_default_translator", cli_test_translator)

    # When
    result = CliRunner().invoke(cli.app, [str(source), "--output", str(output)])

    # Then
    assert result.exit_code == 1
    assert "invalid EPUB: unsafe XML content" in result.output
    assert "Traceback" not in result.output
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_destination_created_during_translation_is_not_overwritten(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "Race.")
    translator = DestinationRaceTranslator(output)

    # When
    with pytest.raises(models.OutputPathError):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert output.read_bytes() == b"sentinel"
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def cli_test_translator() -> CliTestManagedTranslator:
    return CliTestManagedTranslator()


class CliTestManagedTranslator(RecordingTranslator):
    def __init__(self) -> None:
        super().__init__(max_chars=2_800)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        del error_type, error, traceback
