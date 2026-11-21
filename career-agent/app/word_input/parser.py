"""Read DOCX paragraphs and tables in document order, including nested tables."""
from io import BytesIO
from zipfile import BadZipFile, ZipFile
from docx import Document
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 120_000


class WordInputError(ValueError):
    pass


def parse_docx(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise WordInputError("DOCX must be nonempty and at most 10 MB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                raise WordInputError("Not a valid DOCX file")
        document = Document(BytesIO(data))
    except (BadZipFile, Exception) as exc:
        if isinstance(exc, WordInputError):
            raise
        raise WordInputError("Could not open DOCX file") from exc

    blocks: list[dict] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            value = paragraph.text.strip()
            if value:
                blocks.append({"kind": "paragraph", "text": value, "style": paragraph.style.name if paragraph.style else None})
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for i, row in enumerate(table.rows):
                cells = [cell.text.replace("\n", " / ").strip() for cell in row.cells]
                if any(cells):
                    blocks.append({"kind": "table_row", "row_index": i, "cells": cells, "text": " | ".join(cells)})
    text = "\n".join(b["text"] for b in blocks)
    if not text.strip():
        raise WordInputError("DOCX has no extractable paragraphs or table text")
    if len(text) > MAX_CHARS:
        raise WordInputError("DOCX extracted text exceeds 120,000 characters")
    return {"blocks": blocks, "text": text, "characters": len(text)}
