from __future__ import annotations

import os
import zipfile
from typing import TYPE_CHECKING, Never, Self
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from epub_bilingual_translator import cli, epub, models, staging
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


class StagingPathSwapTranslator:
    engine_id: str = "staging-path-swap"

    def __init__(
        self,
        output: pathlib.Path,
        victim_directory: pathlib.Path,
    ) -> None:
        self.output: pathlib.Path = output
        self.victim_directory: pathlib.Path = victim_directory
        self.renamed_stage: pathlib.Path = output.parent / "renamed-stage"
        self.visible_stage: pathlib.Path | None = None

    def translate_html(self, value: str, pair: models.LanguagePair) -> str:
        del pair
        stages = list(
            self.output.parent.glob(f".{self.output.stem}-*.epub.tmp"),
        )
        assert len(stages) == 1
        self.visible_stage = stages[0]
        _ = self.visible_stage.rename(self.renamed_stage)
        self.visible_stage.symlink_to(self.victim_directory, target_is_directory=True)
        return value


class AmplifyingTranslator:
    engine_id: str = "amplifying-test"

    def __init__(self, fragment: str) -> None:
        self.fragment: str = fragment
        self.calls: int = 0

    def translate_html(
        self,
        value: str,
        pair: models.LanguagePair,
    ) -> str:
        del value, pair
        self.calls += 1
        return self.fragment


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


@pytest.mark.skipif(
    os.name != "posix",
    reason="requires POSIX dirfd semantics",
)
def test_staging_path_swap_cannot_redirect_zip_write_or_publication(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    victim_directory = tmp_path / "victim"
    victim_directory.mkdir()
    write_epub(source, "Race.")
    translator = StagingPathSwapTranslator(output, victim_directory)

    # When
    result = epub.translate_epub(options(source, output), translator)

    # Then
    assert result.output_path == output
    assert zipfile.is_zipfile(output)
    assert list(victim_directory.iterdir()) == []
    assert list(translator.renamed_stage.iterdir()) == []
    assert translator.visible_stage is not None
    assert translator.visible_stage.is_symlink()


def test_staging_closes_unowned_descriptor_when_fdopen_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    captured_descriptors: list[int] = []
    write_epub(source, "Descriptor handoff.")

    def fail_fdopen(descriptor: int, mode: str) -> Never:
        del mode
        captured_descriptors.append(descriptor)
        detail = "injected fdopen failure"
        raise OSError(detail)

    monkeypatch.setattr(os, "fdopen", fail_fdopen)

    # When
    with pytest.raises(models.ArchiveError, match="archive I/O failed"):
        _ = epub.translate_epub(
            options(source, output),
            RecordingTranslator(max_chars=2_800),
        )

    # Then
    assert len(captured_descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        _ = os.fstat(captured_descriptors[0])
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_staging_cleanup_error_does_not_mask_active_translation_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    output = tmp_path / "translated.epub"
    staged = staging.StagedOutput(output)

    def fail_close(instance: staging.StagedOutput) -> None:
        del instance
        raise models.ArchiveError(detail="injected cleanup failure")

    # When
    try:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(staging.StagedOutput, "close", fail_close)
            with (
                pytest.raises(
                    models.ProviderUnavailableError,
                    match="primary failure",
                ),
                staged,
            ):
                raise models.ProviderUnavailableError(detail="primary failure")
    finally:
        staged.close()

    # Then
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_cumulative_translated_output_is_refused_before_document_growth(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    limit = 4_000
    blocks = "".join("<p>small source block.</p>" for _ in range(10))
    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML}"><body>{blocks}</body></html>""".encode()
    write_epub_with_chapter(source, chapter)
    translator = AmplifyingTranslator("x" * 900)
    monkeypatch.setattr(epub, "MAX_XHTML_BYTES", limit)

    # When
    with pytest.raises(models.ArchiveError, match="translated XHTML exceeds limit"):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.calls == 5
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_final_xhtml_serialization_is_bounded_after_translations_fit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    limit = 4_000
    blocks = "".join("<p>small source block.</p>" for _ in range(10))
    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML}"><body>{blocks}</body></html>""".encode()
    write_epub_with_chapter(source, chapter)
    translator = AmplifyingTranslator("x" * 350)
    monkeypatch.setattr(epub, "MAX_XHTML_BYTES", limit)

    # When
    with pytest.raises(models.ArchiveError, match="translated XHTML exceeds limit"):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.calls == 10
    assert translator.calls * len(translator.fragment.encode()) < limit
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_malformed_provider_unicode_is_refused_without_output(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "small source block.")
    translator = AmplifyingTranslator("\ud800")

    # When
    with pytest.raises(
        models.ArchiveError,
        match="translated XHTML contains invalid Unicode",
    ):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.calls == 1
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_repeated_large_opening_tag_is_refused_before_provider_use(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    tag_padding = "x" * 1_000
    paragraph = f'<span data-padding="{tag_padding}">{"word " * 200}</span>'
    write_epub(source, paragraph)
    translator = RecordingTranslator(max_chars=200)
    monkeypatch.setattr(epub, "MAX_XHTML_BYTES", 4_000)

    # When
    with pytest.raises(models.ArchiveError, match="XHTML fragments exceed limit"):
        _ = epub.translate_epub(options(source, output, max_chars=200), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_bare_ampersand_repair_expansion_is_refused_before_allocation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "source.epub"
    output = tmp_path / "translated.epub"
    write_epub(source, "small source block.")
    translator = AmplifyingTranslator("&" * 1_000)
    monkeypatch.setattr(epub, "MAX_XHTML_BYTES", 4_000)

    # When
    with pytest.raises(models.ArchiveError, match="translated XHTML exceeds limit"):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.calls == 1
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_deep_xhtml_nesting_is_refused_before_provider_use(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "deep.xhtml.epub"
    output = tmp_path / "translated.epub"
    nesting = 990
    chapter = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<html xmlns="{XHTML}"><body><p>'
        f"{'<span>' * nesting}deep{'</span>' * nesting}"
        "</p></body></html>"
    ).encode()
    write_epub_with_chapter(source, chapter)
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.ArchiveError, match="XHTML nesting exceeds limit"):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()
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
