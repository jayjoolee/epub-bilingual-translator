from __future__ import annotations

from typing import TYPE_CHECKING, Self

import pytest
from typer.testing import CliRunner

from epub_bilingual_translator import cli, models
from tests.helpers import ZipMember, valid_epub_members, write_epub, write_zip

if TYPE_CHECKING:
    import pathlib
    import types


class FakeManagedTranslator:
    engine_id: str = "cli-fake"

    def translate_html(self, value: str, pair: models.LanguagePair) -> str:
        del pair
        return value.replace("Hello", "안녕")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        del error_type, error, traceback


def fake_translator() -> FakeManagedTranslator:
    return FakeManagedTranslator()


def test_help_exposes_public_one_line_options() -> None:
    # Given
    runner = CliRunner()

    # When
    result = runner.invoke(cli.app, ["--help"])

    # Then
    assert result.exit_code == 0
    assert "--to" in result.stdout
    assert "--source" in result.stdout
    assert "--output" in result.stdout
    assert "--max-chars" in result.stdout


def test_version_works_without_input_path() -> None:
    # Given
    runner = CliRunner()

    # When
    result = runner.invoke(cli.app, ["--version"])

    # Then
    assert result.exit_code == 0
    assert result.stdout.strip() == "epub-translate 0.1.0"


def test_default_output_uses_target_and_injected_provider(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "book.epub"
    write_epub(source, "Hello world.")
    monkeypatch.setattr(cli, "create_default_translator", fake_translator)
    runner = CliRunner()

    # When
    result = runner.invoke(cli.app, [str(source), "--to", "ko"])

    # Then
    output = tmp_path / "book.ko.bilingual.epub"
    assert result.exit_code == 0
    assert output.is_file()
    assert str(output) in result.stdout


def test_unsafe_member_name_with_rich_markup_has_concise_typed_cli_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    source = tmp_path / "unsafe.epub"
    output = tmp_path / "translated.epub"
    members = valid_epub_members("Hello world.")
    members.append(ZipMember("../[/red]", b"unsafe"))
    write_zip(source, members)
    monkeypatch.setattr(cli, "create_default_translator", fake_translator)

    # When
    result = CliRunner().invoke(cli.app, [str(source), "--output", str(output)])

    # Then
    assert result.exit_code == 1
    assert "invalid EPUB: unsafe ZIP member name: ../[/red]" in result.output
    assert "MarkupError" not in result.output
    assert "Traceback" not in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--to", ""],
        ["--source", ""],
        ["--to", "english_US"],
        ["--source", "en_US"],
        ["--to", "auto"],
    ],
)
def test_invalid_language_is_rejected_before_provider_creation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    # Given
    source = tmp_path / "book.epub"
    write_epub(source, "Hello world.")
    provider_calls: list[bool] = []

    def forbidden_provider() -> FakeManagedTranslator:
        provider_calls.append(True)
        return FakeManagedTranslator()

    monkeypatch.setattr(cli, "create_default_translator", forbidden_provider)

    # When
    result = CliRunner().invoke(cli.app, [str(source), *arguments])

    # Then
    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert provider_calls == []


@pytest.mark.parametrize(
    ("source_language", "target_language"),
    [("auto", "ko"), ("en", "zh-CN")],
)
def test_valid_language_tags_reach_provider(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    source_language: str,
    target_language: str,
) -> None:
    # Given
    source = tmp_path / "book.epub"
    write_epub(source, "Hello world.")
    monkeypatch.setattr(cli, "create_default_translator", fake_translator)

    # When
    result = CliRunner().invoke(
        cli.app,
        [
            str(source),
            "--source",
            source_language,
            "--to",
            target_language,
        ],
    )

    # Then
    assert result.exit_code == 0
