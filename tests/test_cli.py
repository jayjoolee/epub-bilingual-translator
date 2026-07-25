from __future__ import annotations

from typing import TYPE_CHECKING, Self

from typer.testing import CliRunner

from epub_bilingual_translator import cli, models
from tests.helpers import write_epub

if TYPE_CHECKING:
    import pathlib
    import types

    import pytest


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
