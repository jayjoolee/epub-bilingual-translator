from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    import pathlib

XHTML = "http://www.w3.org/1999/xhtml"


def write_epub(
    path: pathlib.Path,
    paragraph: str,
    *,
    encrypted: bool = False,
    encryption_xml: bytes | None = None,
    font_data: bytes | None = None,
) -> None:
    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML}">
  <head><title>Fixture</title></head>
  <body><p class="body">{paragraph}</p></body>
</html>""".encode()
    container = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    package = b"""<?xml version="1.0" encoding="utf-8"?>
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
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype",
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container)
        if encrypted:
            archive.writestr(
                "META-INF/encryption.xml",
                b'<?xml version="1.0"?><encryption/>',
            )
        if encryption_xml is not None:
            archive.writestr("META-INF/encryption.xml", encryption_xml)
        if font_data is not None:
            archive.writestr("OEBPS/fonts/fixture.otf", font_data)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)


def read_paragraphs(path: pathlib.Path) -> list[ET.Element]:
    with zipfile.ZipFile(path) as archive:
        chapter = archive.read("OEBPS/chapter.xhtml")
    root = ET.fromstring(chapter)
    body = root.find(f"{{{XHTML}}}body")
    assert body is not None
    return list(body)
