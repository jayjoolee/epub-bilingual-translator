from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    import pathlib

XHTML = "http://www.w3.org/1999/xhtml"
CONTAINER = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
PACKAGE = b"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier>fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>"""


@dataclass(frozen=True, slots=True)
class ZipMember:
    name: str
    data: bytes
    compression: int = zipfile.ZIP_DEFLATED


def write_epub(
    path: pathlib.Path,
    paragraph: str,
    *,
    encrypted: bool = False,
    encryption_xml: bytes | None = None,
    extra_members: tuple[ZipMember, ...] = (),
) -> None:
    members = valid_epub_members(paragraph)
    if encrypted:
        members.append(
            ZipMember(
                "META-INF/encryption.xml",
                b'<?xml version="1.0"?><encryption/>',
            ),
        )
    if encryption_xml is not None:
        members.append(ZipMember("META-INF/encryption.xml", encryption_xml))
    members.extend(extra_members)
    write_zip(path, members)


def valid_epub_members(paragraph: str) -> list[ZipMember]:
    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML}">
  <head><title>Fixture</title></head>
  <body><p class="body">{paragraph}</p></body>
</html>""".encode()
    return [
        ZipMember("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
        ZipMember("META-INF/container.xml", CONTAINER),
        ZipMember("OEBPS/content.opf", PACKAGE),
        ZipMember("OEBPS/chapter.xhtml", chapter),
    ]


def write_epub_with_chapter(path: pathlib.Path, chapter: bytes) -> None:
    members = valid_epub_members("placeholder")
    members[-1] = ZipMember("OEBPS/chapter.xhtml", chapter)
    write_zip(path, members)


def write_zip(path: pathlib.Path, members: list[ZipMember]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for member in members:
            archive.writestr(
                member.name,
                member.data,
                compress_type=member.compression,
            )


def read_paragraphs(path: pathlib.Path) -> list[ET.Element]:
    with zipfile.ZipFile(path) as archive:
        chapter = archive.read("OEBPS/chapter.xhtml")
    root = ET.fromstring(chapter)
    body = root.find(f"{{{XHTML}}}body")
    assert body is not None
    return list(body)
