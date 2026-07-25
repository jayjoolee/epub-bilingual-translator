from __future__ import annotations

import dataclasses
import zipfile
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from typer.testing import CliRunner

from epub_bilingual_translator import cli, epub, models
from tests.helpers import (
    ZipMember,
    patch_zip_compression_method,
    valid_epub_members,
    write_zip,
)
from tests.test_epub import RecordingTranslator, options
from tests.test_epub_safety import cli_test_translator

if TYPE_CHECKING:
    import pathlib


@pytest.mark.parametrize("defect", ["missing", "value", "compressed", "order"])
def test_invalid_mimetype_is_rejected_before_translation(
    tmp_path: pathlib.Path,
    defect: Literal["missing", "value", "compressed", "order"],
) -> None:
    # Given
    source = tmp_path / "invalid.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    match defect:
        case "missing":
            members = members[1:]
        case "value":
            members[0] = dataclasses.replace(members[0], data=b"text/plain")
        case "compressed":
            members[0] = dataclasses.replace(
                members[0],
                compression=zipfile.ZIP_DEFLATED,
            )
        case "order":
            members[0], members[1] = members[1], members[0]
        case unreachable:
            assert_never(unreachable)
    write_zip(source, members)
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.ArchiveError):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()


@pytest.mark.parametrize(
    "unsafe_name",
    ["../evil.txt", "/absolute.txt", "OEBPS\\evil.txt", "OEBPS/./evil.txt"],
)
def test_unsafe_zip_member_name_is_rejected(
    tmp_path: pathlib.Path,
    unsafe_name: str,
) -> None:
    # Given
    source = tmp_path / "unsafe.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    members.append(ZipMember(unsafe_name, b"unsafe"))
    write_zip(source, members)

    # When
    with pytest.raises(models.ArchiveError):
        _ = epub.translate_epub(
            options(source, output),
            RecordingTranslator(max_chars=2_800),
        )

    # Then
    assert not output.exists()


def test_duplicate_member_is_rejected(tmp_path: pathlib.Path) -> None:
    # Given
    source = tmp_path / "duplicate.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    members.append(members[-1])
    with pytest.warns(UserWarning, match="Duplicate name"):
        write_zip(source, members)

    # When
    with pytest.raises(models.ArchiveError):
        _ = epub.translate_epub(
            options(source, output),
            RecordingTranslator(max_chars=2_800),
        )

    # Then
    assert not output.exists()


def test_missing_container_is_rejected(tmp_path: pathlib.Path) -> None:
    # Given
    source = tmp_path / "missing-container.epub"
    output = tmp_path / "translated.epub"
    members = [
        member
        for member in valid_epub_members("Sentence.")
        if member.name != "META-INF/container.xml"
    ]
    write_zip(source, members)

    # When
    with pytest.raises(models.ArchiveError):
        _ = epub.translate_epub(
            options(source, output),
            RecordingTranslator(max_chars=2_800),
        )

    # Then
    assert not output.exists()


def test_suspicious_compression_ratio_is_rejected_with_small_fixture(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "ratio.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    members.append(ZipMember("OEBPS/high-ratio.bin", b"0" * 262_144))
    write_zip(source, members)

    # When
    with pytest.raises(models.ArchiveError, match="compression ratio"):
        _ = epub.translate_epub(
            options(source, output),
            RecordingTranslator(max_chars=2_800),
        )

    # Then
    assert not output.exists()


def test_unsupported_compression_is_rejected_before_provider_use(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    source = tmp_path / "unsupported.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    members.append(ZipMember("OEBPS/unsupported.bin", b"asset"))
    write_zip(source, members)
    patch_zip_compression_method(source, "OEBPS/unsupported.bin", 99)
    translator = RecordingTranslator(max_chars=2_800)

    # When
    with pytest.raises(models.ArchiveError, match="unsupported ZIP compression"):
        _ = epub.translate_epub(options(source, output), translator)

    # Then
    assert translator.requests == []
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []


def test_cli_reports_unsupported_compression_without_private_details(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "private-source.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Sentence.")
    members.append(ZipMember("OEBPS/unsupported.bin", b"asset"))
    write_zip(source, members)
    patch_zip_compression_method(source, "OEBPS/unsupported.bin", 99)
    monkeypatch.setattr(cli, "create_default_translator", cli_test_translator)

    # When
    result = CliRunner().invoke(
        cli.app,
        [str(source), "--output", str(output)],
    )

    # Then
    assert result.exit_code == 1
    assert "unsupported ZIP compression" in result.output
    assert "Traceback" not in result.output
    assert str(tmp_path) not in result.output
    assert not output.exists()
    assert list(tmp_path.glob(".*.epub.tmp")) == []
