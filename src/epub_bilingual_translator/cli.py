"""Public one-line command for bilingual EPUB creation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.text import Text

from epub_bilingual_translator import __version__
from epub_bilingual_translator.epub import translate_epub
from epub_bilingual_translator.google import GoogleConfig, GoogleWebTranslator
from epub_bilingual_translator.models import (
    EpubTranslationError,
    LanguagePair,
    ManagedTranslator,
    Options,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
_error_console = Console(stderr=True)
_LANGUAGE_TAG: Final = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


def default_output_path(input_path: Path, target: str) -> Path:
    """Return the non-overwriting default name beside the source book."""
    return input_path.with_name(f"{input_path.stem}.{target}.bilingual.epub")


def create_default_translator() -> ManagedTranslator:
    """Create the cached unofficial provider used by the installed command."""
    cache_dir = (
        Path.home() / ".cache" / "epub-bilingual-translator" / "google-web-gtx-v1"
    )
    return GoogleWebTranslator(GoogleConfig(cache_dir=cache_dir))


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"epub-translate {__version__}")
        raise typer.Exit


def _target_language(value: str) -> str:
    return _parse_language(value, allow_auto=False)


def _source_language(value: str) -> str:
    return _parse_language(value, allow_auto=True)


def _parse_language(value: str, *, allow_auto: bool) -> str:
    normalized = value.strip()
    if allow_auto and normalized.casefold() == "auto":
        return "auto"
    if normalized.casefold() == "auto" or _LANGUAGE_TAG.fullmatch(normalized) is None:
        detail = "use a language tag such as en, ko, or zh-CN"
        raise typer.BadParameter(detail)
    return normalized


@app.command()
def translate_command(
    input_path: Annotated[Path, typer.Argument(help="Source DRM-free .epub file")],
    target: Annotated[
        str,
        typer.Option(
            "--to",
            metavar="LANG",
            callback=_target_language,
            help="Target language code",
        ),
    ] = "ko",
    source: Annotated[
        str,
        typer.Option(
            "--source",
            metavar="LANG",
            callback=_source_language,
            help="Source language code",
        ),
    ] = "auto",
    output_path: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="New output .epub path"),
    ] = None,
    max_chars: Annotated[
        int,
        typer.Option(
            "--max-chars",
            min=200,
            max=5_000,
            help="Maximum visible characters per provider request",
        ),
    ] = 2_800,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = False,
) -> None:
    """Create a translation-first bilingual EPUB with the original retained."""
    del version
    destination = output_path or default_output_path(input_path, target)
    options = Options(
        input_path=input_path,
        output_path=destination,
        pair=LanguagePair(source=source, target=target),
        max_chars=max_chars,
    )
    try:
        with (
            _error_console.status("Translating EPUB"),
            create_default_translator() as translator,
        ):
            result = translate_epub(options, translator)
    except EpubTranslationError as error:
        _error_console.print(
            Text.assemble(("Translation failed:", "red"), " ", str(error)),
        )
        raise typer.Exit(code=1) from None
    summary = " ".join(
        (
            f"Created {result.output_path}",
            f"({result.translated_blocks} blocks,",
            f"{result.translated_chunks} fragments)",
        ),
    )
    typer.echo(summary)


def main() -> None:
    """Run the installed epub-translate command."""
    app()
