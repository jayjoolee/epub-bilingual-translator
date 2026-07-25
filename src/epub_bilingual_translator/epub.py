"""Translate XHTML members while preserving the EPUB archive boundary."""

from __future__ import annotations

import copy
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from epub_bilingual_translator.archive import (
    MAX_XHTML_BYTES,
    MAX_XML_METADATA_BYTES,
    XHTML_SUFFIXES,
    copy_member_streaming,
    create_temporary_output,
    publish_no_clobber,
    read_member_bounded,
    validate_archive,
    verify_output,
    write_transformed_member,
)
from epub_bilingual_translator.html import (
    HtmlChunkByteLimitError,
    split_balanced_html,
    visible_text,
)
from epub_bilingual_translator.markup import repair_inner_markup
from epub_bilingual_translator.models import (
    ArchiveError,
    DrmProtectedError,
    InputPathError,
    LanguagePair,
    Options,
    OutputPathError,
    TranslationResult,
    Translator,
)
from epub_bilingual_translator.xhtml import (
    inner_markup,
    serialize_document_bounded,
    utf8_byte_length_bounded,
)

_BLOCK_TAGS: Final = frozenset(
    {"p", "li", "blockquote", "figcaption", "dd", "dt", "td", "th"}
    | {f"h{level}" for level in range(1, 7)},
)
_SKIP_TAGS: Final = frozenset({"script", "style", "pre", "code", "math", "svg"})
_ENCRYPTION_MEMBER: Final = "meta-inf/encryption.xml"
_CONTAINER_ENCRYPTION: Final = (
    "{urn:oasis:names:tc:opendocument:xmlns:container}encryption"
)
_ENCRYPTED_DATA: Final = "{http://www.w3.org/2001/04/xmlenc#}EncryptedData"
_ENCRYPTION_METHOD: Final = "{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod"
_CIPHER_DATA: Final = "{http://www.w3.org/2001/04/xmlenc#}CipherData"
_CIPHER_REFERENCE: Final = "{http://www.w3.org/2001/04/xmlenc#}CipherReference"
_FONT_OBFUSCATION: Final = "http://www.idpf.org/2008/embedding"
_FONT_SUFFIXES: Final = frozenset({".otf", ".ttf", ".woff", ".woff2"})


@dataclass(frozen=True, slots=True)
class _TransformContext:
    translator: Translator
    pair: LanguagePair
    max_chars: int


@dataclass(frozen=True, slots=True)
class _DocumentResult:
    data: bytes
    blocks: int
    chunks: int


def translate_epub(
    options: Options,
    translator: Translator,
) -> TranslationResult:
    """Create a complete bilingual EPUB atomically or leave no output."""
    _validate_paths(options)
    context = _TransformContext(translator, options.pair, options.max_chars)
    temporary: Path | None = None
    blocks = 0
    chunks = 0
    try:
        with zipfile.ZipFile(options.input_path) as source:
            plan = validate_archive(source)
            _validate_encryption(source)
            temporary = create_temporary_output(options.output_path)
            with zipfile.ZipFile(temporary, "w") as destination:
                for info in plan.entries:
                    if info.filename.casefold().endswith(XHTML_SUFFIXES):
                        data = read_member_bounded(source, info, MAX_XHTML_BYTES)
                        document = _translate_document(data, context)
                        write_transformed_member(destination, info, document.data)
                        blocks += document.blocks
                        chunks += document.chunks
                    else:
                        copy_member_streaming(source, destination, info)
        verify_output(temporary)
        publish_no_clobber(temporary, options.output_path)
    except DefusedXmlException as error:
        raise ArchiveError(detail="unsafe XML content") from error
    except ET.ParseError as error:
        raise ArchiveError(detail="malformed XML content") from error
    except HtmlChunkByteLimitError:
        raise ArchiveError(detail="XHTML fragments exceed limit") from None
    except zipfile.BadZipFile as error:
        raise ArchiveError(detail="corrupt ZIP archive") from error
    except OSError as error:
        raise ArchiveError(detail=f"archive I/O failed: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return TranslationResult(options.output_path, blocks, chunks)


def _translate_document(data: bytes, context: _TransformContext) -> _DocumentResult:
    root = DefusedET.fromstring(data)
    blocks = 0
    chunks = 0
    translated_bytes = 0
    for parent in root.iter():
        for original in list(parent):
            if _local_name(original) not in _BLOCK_TAGS or _should_skip(original):
                continue
            markup = inner_markup(original)
            if not visible_text(markup).strip():
                continue
            pieces = split_balanced_html(markup, context.max_chars, MAX_XHTML_BYTES)
            translated_pieces: list[str] = []
            for piece in pieces:
                translated_piece = context.translator.translate_html(
                    piece, context.pair
                )
                translated_bytes += utf8_byte_length_bounded(
                    translated_piece,
                    MAX_XHTML_BYTES - translated_bytes,
                )
                if translated_bytes > MAX_XHTML_BYTES:
                    raise ArchiveError(detail="translated XHTML exceeds limit")
                translated_pieces.append(translated_piece)
            translated = "".join(translated_pieces)
            translated_element = copy.deepcopy(original)
            translated_element.tail = None
            repair_inner_markup(translated_element, translated, MAX_XHTML_BYTES)
            translated_element.set("lang", context.pair.target)
            translated_element.set("data-epub-translation", "translation")
            original.set("data-epub-translation", "original")
            parent.insert(list(parent).index(original), translated_element)
            blocks += 1
            chunks += len(pieces)
    return _DocumentResult(
        serialize_document_bounded(root, MAX_XHTML_BYTES),
        blocks,
        chunks,
    )


def _should_skip(element: ET.Element) -> bool:
    for descendant in element.iter():
        classes = set((descendant.get("class") or "").split())
        if (
            _local_name(descendant) in _SKIP_TAGS
            or "notranslate" in classes
            or descendant.get("translate") == "no"
        ):
            return True
    return any(
        _local_name(descendant) in _BLOCK_TAGS
        for descendant in element.iter()
        if descendant is not element
    )


def _local_name(element: ET.Element) -> str:
    return element.tag.rpartition("}")[2].lower()


def _validate_encryption(source: zipfile.ZipFile) -> None:
    members = source.namelist()
    encryption_member = next(
        (member for member in members if member.casefold() == _ENCRYPTION_MEMBER),
        None,
    )
    if encryption_member is None:
        return
    try:
        info = source.getinfo(encryption_member)
        root = DefusedET.fromstring(
            read_member_bounded(source, info, MAX_XML_METADATA_BYTES),
        )
    except (ET.ParseError, DefusedXmlException) as error:
        raise DrmProtectedError(member=encryption_member) from error
    encrypted_data = list(root)
    if root.tag != _CONTAINER_ENCRYPTION or not encrypted_data:
        raise DrmProtectedError(member=encryption_member)
    for entry in encrypted_data:
        if entry.tag != _ENCRYPTED_DATA:
            raise DrmProtectedError(member=encryption_member)
        methods = entry.findall(_ENCRYPTION_METHOD)
        cipher_data = entry.findall(_CIPHER_DATA)
        if len(methods) != 1 or len(cipher_data) != 1:
            raise DrmProtectedError(member=encryption_member)
        references = cipher_data[0].findall(_CIPHER_REFERENCE)
        algorithm = methods[0].get("Algorithm")
        if (
            algorithm != _FONT_OBFUSCATION
            or len(references) != 1
            or not _is_font_reference(references[0].get("URI"), members)
        ):
            raise DrmProtectedError(member=encryption_member)


def _is_font_reference(uri: str | None, members: list[str]) -> bool:
    if uri is None:
        return False
    parsed = urlsplit(uri)
    path = unquote(parsed.path)
    normalized = PurePosixPath(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or normalized.is_absolute()
        or ".." in normalized.parts
    ):
        return False
    return normalized.suffix.casefold() in _FONT_SUFFIXES and path in members


def _validate_paths(options: Options) -> None:
    if not options.input_path.is_file():
        raise InputPathError(options.input_path, "input EPUB not found")
    if options.input_path.suffix.casefold() != ".epub":
        raise InputPathError(options.input_path, "input must end in .epub")
    if options.input_path.resolve() == options.output_path.resolve():
        raise OutputPathError(options.output_path, "source overwrite refused")
    if options.output_path.exists():
        raise OutputPathError(options.output_path, "output already exists")
    try:
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputPathError(options.output_path, str(error)) from error
