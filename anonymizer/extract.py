"""Extract the raw text of every item of a case file (step 1 of the Markdown output).

The result is held in memory only; nothing derived from a document is written
anywhere but a temporary folder that is removed when the walk ends. Neither
``Document`` nor ``Skipped`` keeps a file name or path. A failure in one item
never propagates: the item becomes a ``Skipped`` with a stable code.
"""
import csv
import io
import json
import mimetypes
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile

from lxml import etree
from lxml import html as lxml_html
import pymupdf as pdf

from . import formats
from .documents import MAX_PIXELS, blank, recognize_image, slovak_register
from .formats import SUPPORTED, FormatError, decode, identify, size_limit
from .office import DRAW, SHEET, WORD, column_index, xml_root
from .reasons import reason_code

R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
W = f"{{{WORD}}}"
S = f"{{{SHEET}}}"
A = f"{{{DRAW}}}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
V = "{urn:schemas-microsoft-com:vml}"
PART_LIMIT = 100_000_000
IMAGE_LIMIT = 25_000_000
OCR_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".bmp"}
MAX_ZIP_DEPTH = 3
MAX_NESTING = 8
FORMAT_NAMES = {".jpg": "JPEG", ".tif": "TIFF"}


# -- data model -------------------------------------------------------------------

@dataclass
class Block:
    kind: str                       # "p", "table", "image", "note", "mailheader"
    # Document text never appears in repr (logs, tracebacks).
    text: str = field(default="", repr=False)
    rows: "list[list[str]] | None" = field(default=None, repr=False)
    ocr: bool = False


@dataclass
class Page:
    number: "int | None"
    blocks: "list[Block]"


@dataclass
class Document:
    index: int
    format: str
    pages: "list[Page]"
    headers: "list[str]" = field(repr=False)
    footers: "list[str]" = field(repr=False)
    parent: "int | None" = None
    date: "str | None" = None


@dataclass
class Skipped:
    index: int
    format: str
    code: str


class _Skip(Exception):
    """An item that is deliberately not extracted; carries only a code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Abort(BaseException):
    """Wraps an exception raised by the progress callback so it is never swallowed."""

    def __init__(self, error):
        self.error = error
        super().__init__()


@dataclass
class _Result:
    pages: list
    headers: list
    footers: list
    date: "str | None" = None
    children: tuple = ()            # (supplier of bytes, suffix hint) of e-mail attachments


class _Context:
    def __init__(self, tessdata, progress, folder):
        self.tessdata, self.progress, self.folder = tessdata, progress, Path(folder)
        self.has_ocr = bool(tessdata) and (Path(tessdata) / "ces.traineddata").is_file()
        self.counter = self.items = self.pages = self.files = 0
        self.budget = [formats.ARCHIVE_UNPACKED, formats.ARCHIVE_MEMBERS]

    def next_index(self):
        self.counter += 1
        return self.counter

    def new_path(self, hint):
        self.files += 1
        return self.folder / f"{self.files:06d}{hint if re.fullmatch(r'[.][a-z0-9]{1,6}', hint or '') else ''}"

    def tick(self):
        if self.progress:
            try:
                self.progress(self.items, self.pages)
            except BaseException as error:
                raise _Abort(error) from None

    def page(self):
        self.pages += 1
        self.tick()


# -- text cleaning --------------------------------------------------------------------

def _line(text):
    return re.sub(r"[^\S\n]+", " ", text).strip()


def _noise(line):
    chars = [c for c in line if not c.isspace()]
    good = sum(c.isalnum() for c in chars)
    return not chars or good / len(chars) < 0.5 or (len(chars) < 3 and not good)


def clean_ocr(text, *, text_layer=False):
    """Tidy OCR text: join hyphenated words and lines, drop noise lines, normalise spaces.

    With ``text_layer`` (a PDF text layer) lines are kept and noise is not removed.
    """
    paragraphs = []
    for chunk in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [_line(item) for item in chunk.split("\n")]
        lines = [item for item in lines if item and (text_layer or not _noise(item))]
        if not lines:
            continue
        out = lines[0]
        for line in lines[1:]:
            if re.search(r"[^\W\d_][-­]$", out) and line[:1].islower():
                out = out[:-1] + line
            else:
                out += ("\n" if text_layer else " ") + line
        paragraphs.append(out)
    return "\n\n".join(paragraphs)


def _plain(text):
    return _line(text.replace("­", ""))


def _czech_date(value):
    return f"{value.day}. {value.month}. {value.year}"


def _flat(blocks):
    """Flatten blocks (a table cell, a header) to text; nested tables become lines."""
    parts = []
    for block in blocks:
        if block.kind == "table":
            parts.extend(" | ".join(cell for cell in row if cell) for row in block.rows or ())
        elif block.text:
            parts.append(block.text)
    return "\n".join(part for part in parts if part)


def _unique(values):
    seen, result = set(), []
    for value in values:
        if value and any(c.isalnum() for c in value) and value not in seen:
            seen.add(value)
            result.append(value)
    return result


# -- images and OCR ----------------------------------------------------------------------

def _pixmap(data):
    """Return an RGB pixmap (no alpha) of image bytes, or None when it cannot be decoded."""
    try:
        pix = pdf.Pixmap(data)
    except Exception:
        try:
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtGui import QImage

            image = QImage.fromData(data)
            if image.isNull() or image.width() * image.height() > MAX_PIXELS:
                return None
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            pix = pdf.Pixmap(bytes(buffer.data()))
        except Exception:
            return None
    return _rgb(pix)


def _rgb(pix):
    """RGB pixmap without alpha (PyMuPDF OCR does not read gray, alpha or other colorspaces), or None."""
    if pix.colorspace is None:
        return None
    if pix.alpha and pix.width * pix.height <= MAX_PIXELS:
        # transparent pixels would turn black; lay the image on a white page instead
        with pdf.open() as sheet:
            page = sheet.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, pixmap=pix)
            pix = page.get_pixmap(dpi=72, colorspace=pdf.csRGB, alpha=False)
    base = pdf.Pixmap(pix, 0) if pix.alpha else pix
    return base if base.colorspace == pdf.csRGB else pdf.Pixmap(pdf.csRGB, base)


def ocr_blocks(pixmap, tessdata):
    """OCR a pixmap and return the text paragraphs (lines grouped by vertical gaps)."""
    pixmap = _rgb(pixmap)
    if pixmap is None:
        return []
    text, boxes, _ = recognize_image(pixmap, tessdata)
    rects = {}
    for entry in boxes:
        if entry is not None:
            line_id, box = entry
            rects[line_id] = rects.get(line_id, pdf.Rect()) | box
    blocks, current, previous = [], [], None
    for number, line in enumerate(text.split("\n")):
        box = rects.get(number)
        if not line.strip() or box is None:
            continue
        if previous is not None and box.y0 - previous.y1 > 0.5 * max(previous.height, 1):
            blocks.append(current)
            current = []
        current.append(line)
        previous = box
    blocks.append(current)
    return [clean for clean in (clean_ocr("\n".join(lines)) for lines in blocks) if clean]


def _image_block(ctx, data, suffix):
    """Image block of an embedded picture; OCR text is attached when OCR data is available."""
    if suffix in OCR_SUFFIXES and ctx.has_ocr and len(data) <= IMAGE_LIMIT:
        try:
            pix = _pixmap(data)
            if pix is not None and min(pix.width, pix.height) >= 20 and pix.width * pix.height <= MAX_PIXELS:
                text = "\n\n".join(ocr_blocks(pix, ctx.tessdata))
                if text:
                    return Block("image", text=text, ocr=True)
        except Exception:
            pass
    return Block("image")


def _render(page, clip=None):
    rect = clip or page.rect
    dpi = min(300, int(72 * (MAX_PIXELS / max(rect.width * rect.height, 1)) ** 0.5))
    if dpi < 50:
        raise _Skip("pdf_page_too_large")
    scale = pdf.Matrix(dpi / 72, dpi / 72)
    return page.get_pixmap(matrix=scale, clip=clip, annots=False, alpha=False)


def _need_ocr(ctx):
    if not ctx.has_ocr:
        raise _Skip("ocr_data_missing")


# -- OOXML helpers --------------------------------------------------------------------------

def _read(archive, name, limit=PART_LIMIT):
    if archive.getinfo(name).file_size > limit:
        raise FormatError("size_limit", "Položka archivu překračuje limit velikosti.")
    return archive.read(name)


def _resolve(base, target):
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))


def _rels(archive, name, base):
    """Map relationship id -> (type, part name) of internal relationships."""
    if name not in archive.namelist():
        return {}
    result = {}
    for node in xml_root(_read(archive, name)):
        if node.get("TargetMode") != "External" and node.get("Id") and node.get("Target"):
            result[node.get("Id")] = (node.get("Type", ""), _resolve(base, node.get("Target")))
    return result


def _choice(node):
    chosen = node.find(MC + "Choice")
    return chosen if chosen is not None else node.find(MC + "Fallback")


# -- DOCX ----------------------------------------------------------------------------------------

DOCX_SKIP = {W + name for name in ("del", "moveFrom", "instrText", "delText", "delInstrText", "pPr", "rPr",
                                   "commentReference", "commentRangeStart", "commentRangeEnd", "sectPr",
                                   "sym", "footnoteReference", "endnoteReference")}


def _hidden_run(run):
    properties = run.find(W + "rPr")
    if properties is None:
        return False
    for name in ("vanish", "specVanish"):
        node = properties.find(W + name)
        if node is not None and node.get(W + "val", "1").lower() not in {"0", "false", "off"}:
            return True
    return False


class _Docx:
    def __init__(self, ctx, archive, rels):
        self.ctx, self.archive, self.rels = ctx, archive, rels
        self.code = []              # field-code state: True while inside instruction text

    def image(self, rid):
        target = self.rels.get(rid)
        if not target:
            return Block("image")
        name = target[1]
        suffix = posixpath.splitext(name)[1].lower()
        try:
            data = _read(self.archive, name, IMAGE_LIMIT) if suffix in OCR_SUFFIXES else b""
        except (KeyError, FormatError):
            return Block("image")
        return _image_block(self.ctx, data, suffix)

    def paragraph(self, node):
        text, extra, pictures = [], [], []

        def visit(item):
            tag = item.tag
            if not isinstance(tag, str) or tag in DOCX_SKIP or (tag == W + "r" and _hidden_run(item)):
                return
            if tag == W + "fldChar":
                kind = item.get(W + "fldCharType")
                if kind == "begin":
                    self.code.append(True)
                elif kind == "separate" and self.code:
                    self.code[-1] = False
                elif kind == "end" and self.code:
                    self.code.pop()
                return
            leaf = {W + "t", W + "tab", W + "ptab", W + "br", W + "cr", W + "noBreakHyphen", W + "txbxContent",
                    A + "blip", V + "imagedata"}
            if tag in leaf and any(self.code):
                return                      # inside the instruction text of a field
            if tag == W + "t":
                text.append(item.text or "")
            elif tag in (W + "tab", W + "ptab"):
                text.append(" ")
            elif tag in (W + "br", W + "cr"):
                text.append(" " if item.get(W + "type") in {"page", "column"} else "\n")
            elif tag == W + "noBreakHyphen":
                text.append("-")
            elif tag == W + "txbxContent":
                extra.extend(self.blocks(item))
            elif tag == MC + "AlternateContent":
                chosen = _choice(item)
                for child in chosen if chosen is not None else ():
                    visit(child)
            elif tag == A + "blip":
                pictures.append(item.get(R + "embed"))
            elif tag == V + "imagedata":
                pictures.append(item.get(R + "id"))
            else:
                for child in item:
                    visit(child)

        for child in node:
            visit(child)
        result = []
        joined = re.sub(r"[ \t ]+", " ", "".join(text)).strip()
        if joined:
            result.append(Block("p", text=joined))
        result.extend(extra)
        result.extend(self.image(rid) for rid in pictures if rid)
        return result

    def table(self, node):
        rows = []
        for row in node.iterchildren(W + "tr"):
            properties = row.find(W + "trPr")
            if properties is not None and properties.find(W + "del") is not None:
                continue
            cells = [_flat(self.blocks(cell)) for cell in row.iterchildren(W + "tc")]
            if any(cells):
                rows.append(cells)
        return Block("table", rows=rows)

    def blocks(self, container):
        result = []
        for child in container:
            tag = child.tag
            if tag == W + "p":
                result.extend(self.paragraph(child))
            elif tag == W + "tbl":
                result.append(self.table(child))
            elif tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    result.extend(self.blocks(content))
            elif tag in (W + "customXml", W + "ins", W + "moveTo", W + "smartTag"):
                result.extend(self.blocks(child))
            elif tag == MC + "AlternateContent":
                chosen = _choice(child)
                if chosen is not None:
                    result.extend(self.blocks(chosen))
        return result


def _docx(ctx, path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if names.count("word/document.xml") != 1:
            raise ValueError("Neplatný dokument Office.")
        root = xml_root(_read(archive, "word/document.xml"))
        body = root.find(W + "body")
        rels = _rels(archive, "word/_rels/document.xml.rels", "word")
        blocks = _Docx(ctx, archive, rels).blocks(body) if body is not None else []
        for name in ("word/footnotes.xml", "word/endnotes.xml"):
            if name not in names:
                continue
            walker = _Docx(ctx, archive, {})
            for note in xml_root(_read(archive, name)):
                if note.get(W + "type") in {"separator", "continuationSeparator", "continuationNotice"}:
                    continue
                text = _flat(b for b in walker.blocks(note) if b.kind != "image")
                if text:
                    blocks.append(Block("note", text=text))
        headers, footers = [], []
        for name in sorted(names):
            match = re.fullmatch(r"word/(header|footer)\d*\.xml", name)
            if match:
                text = _flat(_Docx(ctx, archive, {}).blocks(xml_root(_read(archive, name))))
                (headers if match[1] == "header" else footers).append(text)
    return _Result([Page(None, blocks)], _unique(headers), _unique(footers))


# -- XLSX ---------------------------------------------------------------------------------------

BUILTIN_DATES = {14, 15, 16, 17, 22, 27, 28, 29, 30, 31, 36, 50, 51, 52, 53, 54, 55, 56, 57, 58}


def _date_format(code):
    code = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", code).casefold()
    return bool(re.search(r"[dy]|mmm", code))


def _time_in_format(code):
    return bool(re.search(r"[hs]", re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code).casefold()))


def _sheet_header_text(value):
    value = re.sub(r'&"[^"]*"|&K[0-9A-Fa-f]{6}|&\d+|&[PNDTFAZGBIUSXYLCR]', " ", value or "")
    return _line(value.replace("&&", "&"))


def _xlsx_styles(archive):
    """Return, per cell format index, whether it is a date and whether it shows a time."""
    if "xl/styles.xml" not in archive.namelist():
        return []
    root = xml_root(_read(archive, "xl/styles.xml"))
    codes = {int(node.get("numFmtId")): node.get("formatCode", "") for node in root.iter(S + "numFmt")
             if (node.get("numFmtId") or "").isdigit()}
    formats_ = root.find(S + "cellXfs")
    result = []
    for xf in formats_ if formats_ is not None else ():
        number = int(xf.get("numFmtId", "0") or 0) if (xf.get("numFmtId") or "0").isdigit() else 0
        code = codes.get(number)
        if code is None:
            result.append((number in BUILTIN_DATES, number == 22))
        else:
            result.append((_date_format(code), _time_in_format(code)))
    return result


def _xlsx_cell(cell, shared, styles, epoch):
    kind = cell.get("t")
    value = cell.find(S + "v")
    raw = value.text if value is not None and value.text else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (IndexError, ValueError):
            return ""
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(S + "t")
                       if not any(etree.QName(p).localname == "rPh" for p in node.iterancestors()))
    if kind == "b":
        return "PRAVDA" if raw == "1" else "NEPRAVDA" if raw else ""
    if kind == "e":
        return ""
    if kind in {None, "n"} and raw:
        index = cell.get("s")
        flags = styles[int(index)] if index and index.isdigit() and int(index) < len(styles) else (False, False)
        if flags[0]:
            try:
                serial = float(raw)
                moment = datetime.combine(epoch, datetime.min.time()) + timedelta(days=serial)
            except (ValueError, OverflowError):
                return raw
            text = _czech_date(moment)
            return f"{text} {moment:%H:%M}" if flags[1] and moment.time() != datetime.min.time() else text
    return raw


def _xlsx(ctx, path):
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("Neplatný dokument Office.")
        workbook = xml_root(_read(archive, "xl/workbook.xml"))
        properties = workbook.find(S + "workbookPr")
        epoch = date(1904, 1, 1) if properties is not None and properties.get("date1904") in {"1", "true"} \
            else date(1899, 12, 30)
        rels = _rels(archive, "xl/_rels/workbook.xml.rels", "xl")
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in xml_root(_read(archive, "xl/sharedStrings.xml")).iterchildren(S + "si"):
                shared.append("".join(node.text or "" for node in item.iter(S + "t")
                                      if not any(etree.QName(p).localname == "rPh" for p in node.iterancestors())))
        styles = _xlsx_styles(archive)
        blocks, headers, footers = [], [], []
        for sheet in workbook.iter(S + "sheet"):
            target = rels.get(sheet.get(R + "id"))
            if not target or target[1] not in names or "/worksheets/" not in "/" + target[1]:
                continue
            if sheet.get("name"):
                blocks.append(Block("p", text=sheet.get("name")))
            rows = []
            with archive.open(target[1]) as stream:
                parser = etree.iterparse(stream, events=("end",), resolve_entities=False, no_network=True,
                                         tag=[S + "row", S + "oddHeader", S + "oddFooter", S + "evenHeader",
                                              S + "evenFooter", S + "firstHeader", S + "firstFooter"])
                for _, node in parser:
                    name = etree.QName(node).localname
                    if name == "row":
                        values, column = {}, 0
                        for cell in node.iterchildren(S + "c"):
                            column = column_index(cell.get("r")) or column + 1
                            text = _xlsx_cell(cell, shared, styles, epoch).strip()
                            if text:
                                values[column] = text
                        if values:
                            rows.append([values.get(i, "") for i in range(1, max(values) + 1)])
                        node.clear()
                        while node.getprevious() is not None:
                            del node.getparent()[0]
                    else:
                        (headers if "Header" in name else footers).append(_sheet_header_text(node.text))
            if rows:
                blocks.append(Block("table", rows=rows))
    return _Result([Page(None, blocks)], _unique(headers), _unique(footers))


# -- PPTX --------------------------------------------------------------------------------------

PLACEHOLDERS_SKIPPED = {"sldImg", "sldNum", "dt"}


def _drawing_paragraph(node):
    parts = []
    for child in node:
        if child.tag in (A + "r", A + "fld"):
            parts.append("".join(t.text or "" for t in child.iterchildren(A + "t")))
        elif child.tag == A + "br":
            parts.append("\n")
    return re.sub(r"[ \t ]+", " ", "".join(parts)).strip()


def _slide_blocks(ctx, archive, node, rels, kind="p"):
    blocks = []
    for child in node:
        tag = child.tag
        if not isinstance(tag, str):
            continue
        if tag == A + "p":
            text = _drawing_paragraph(child)
            if text:
                blocks.append(Block(kind, text=text))
        elif tag == A + "tbl":
            rows = []
            for row in child.iterchildren(A + "tr"):
                cells = ["\n".join(t for t in (_drawing_paragraph(p) for p in cell.iter(A + "p")) if t)
                         for cell in row.iterchildren(A + "tc")]
                if any(cells):
                    rows.append(cells)
            blocks.append(Block("table", rows=rows))
        elif tag == MC + "AlternateContent":
            chosen = _choice(child)
            if chosen is not None:
                blocks.extend(_slide_blocks(ctx, archive, chosen, rels, kind))
        elif tag == A + "blip":
            target = rels.get(child.get(R + "embed"))
            suffix = posixpath.splitext(target[1])[1].lower() if target else ""
            try:
                data = _read(archive, target[1], IMAGE_LIMIT) if target and suffix in OCR_SUFFIXES else b""
            except (KeyError, FormatError):
                data = b""
            blocks.append(_image_block(ctx, data, suffix))
        elif tag == P + "sp" and any(ph.get("type") in PLACEHOLDERS_SKIPPED for ph in child.iter(P + "ph")):
            continue
        else:
            blocks.extend(_slide_blocks(ctx, archive, child, rels, kind))
    return blocks


def _pptx(ctx, path):
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        presentation = xml_root(_read(archive, "ppt/presentation.xml"))
        rels = _rels(archive, "ppt/_rels/presentation.xml.rels", "ppt")
        pages = []
        for number, slide in enumerate(presentation.iter(P + "sldId"), 1):
            target = rels.get(slide.get(R + "id"))
            if not target or target[1] not in names:
                continue
            slide_rels = _rels(archive, posixpath.join(posixpath.dirname(target[1]), "_rels",
                                                       posixpath.basename(target[1]) + ".rels"),
                               posixpath.dirname(target[1]))
            blocks = _slide_blocks(ctx, archive, xml_root(_read(archive, target[1])), slide_rels)
            for kind, note in slide_rels.values():
                if kind.endswith("/notesSlide") and note in names:
                    blocks.extend(_slide_blocks(ctx, archive, xml_root(_read(archive, note)), {}, "note"))
            pages.append(Page(number, blocks))
            ctx.page()
    return _Result(pages, [], [])


# -- OpenDocument -------------------------------------------------------------------------------

O_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
O_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
O_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
O_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
O_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
O_PRESENTATION = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
O_XLINK = "{http://www.w3.org/1999/xlink}"
ODF_HIDDEN = {O_OFFICE + "annotation", O_OFFICE + "annotation-end", O_TEXT + "tracked-changes",
              O_TEXT + "hidden-text", O_TEXT + "hidden-paragraph"}


class _Odf:
    def __init__(self, ctx, archive):
        self.ctx, self.archive = ctx, archive

    def image(self, href):
        suffix = posixpath.splitext(href or "")[1].lower()
        try:
            data = _read(self.archive, href, IMAGE_LIMIT) if suffix in OCR_SUFFIXES and href else b""
        except (KeyError, FormatError):
            data = b""
        return _image_block(self.ctx, data, suffix)

    def paragraph(self, node):
        text, extra, pictures = [], [], []

        def visit(item):
            tag = item.tag
            if isinstance(tag, str) and tag not in ODF_HIDDEN:
                if tag == O_TEXT + "s":
                    text.append(" ")
                elif tag == O_TEXT + "tab":
                    text.append(" ")
                elif tag == O_TEXT + "line-break":
                    text.append("\n")
                elif tag == O_TEXT + "note":
                    for body in item.iterchildren(O_TEXT + "note-body"):
                        extra.extend(Block("note", text=b.text) for b in self.blocks(body) if b.text)
                elif tag == O_DRAW + "text-box":
                    extra.extend(self.blocks(item))
                elif tag == O_DRAW + "image":
                    pictures.append(item.get(O_XLINK + "href"))
                else:
                    text.append(item.text or "")
                    for child in item:
                        visit(child)
            text.append(item.tail or "")

        text.append(node.text or "")
        for child in node:
            visit(child)
        result = []
        joined = re.sub(r"[ \t ]+", " ", "".join(text)).strip()
        if joined:
            result.append(Block("p", text=joined))
        result.extend(extra)
        result.extend(self.image(href) for href in pictures if href)
        return result

    def cell_text(self, cell):
        date_value = cell.get(O_OFFICE + "date-value")
        if date_value:
            try:
                return _czech_date(datetime.fromisoformat(date_value))
            except ValueError:
                pass
        text = _flat(self.blocks(cell))
        if not text and cell.get(O_OFFICE + "value"):
            return cell.get(O_OFFICE + "value")
        return text

    def rows(self, node):
        for child in node:
            if child.tag == O_TABLE + "table-row":
                yield child
            elif child.tag in (O_TABLE + "table-header-rows", O_TABLE + "table-row-group", O_TABLE + "table-rows"):
                yield from self.rows(child)

    def table(self, node):
        rows = []
        for row in self.rows(node):
            cells = []
            for cell in row:
                if cell.tag == O_TABLE + "covered-table-cell":
                    cells.append("")
                elif cell.tag == O_TABLE + "table-cell":
                    text = self.cell_text(cell)
                    cells.extend([text] * min(max(int(cell.get(O_TABLE + "number-columns-repeated", "1")), 1), 1000))
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.extend([cells] * min(max(int(row.get(O_TABLE + "number-rows-repeated", "1")), 1), 100))
        return Block("table", rows=rows)

    def blocks(self, container, kind="p"):
        result = []
        for child in container:
            tag = child.tag
            if not isinstance(tag, str) or tag in ODF_HIDDEN:
                continue
            if tag in (O_TEXT + "p", O_TEXT + "h"):
                result.extend(self.paragraph(child))
            elif tag == O_TABLE + "table":
                if container.tag == O_OFFICE + "spreadsheet" and child.get(O_TABLE + "name"):
                    result.append(Block("p", text=child.get(O_TABLE + "name")))
                result.append(self.table(child))
            elif tag == O_PRESENTATION + "notes":
                result.extend(Block("note", text=b.text) for b in self.blocks(child) if b.text)
            elif tag == O_DRAW + "image":
                result.append(self.image(child.get(O_XLINK + "href")))
            elif tag == O_TEXT + "section" and child.get(O_TEXT + "display") == "none":
                continue
            else:
                result.extend(self.blocks(child))
        return result


def _odf(ctx, path):
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        if "content.xml" not in names:
            raise ValueError("Neplatný dokument OpenDocument.")
        walker = _Odf(ctx, archive)
        body = xml_root(_read(archive, "content.xml")).find(O_OFFICE + "body")
        pages = []
        content = body[0] if body is not None and len(body) else None
        if content is not None and content.tag == O_OFFICE + "presentation":
            for number, slide in enumerate(content.iterchildren(O_DRAW + "page"), 1):
                pages.append(Page(number, walker.blocks(slide)))
                ctx.page()
        else:
            pages.append(Page(None, walker.blocks(content) if content is not None else []))
        headers, footers = [], []
        if "styles.xml" in names:
            styles = xml_root(_read(archive, "styles.xml"))
            for master in styles.iter(O_STYLE + "master-page"):
                for part in master:
                    name = etree.QName(part).localname if isinstance(part.tag, str) else ""
                    if name.startswith(("header", "footer")):
                        (headers if name.startswith("header") else footers).append(_flat(walker.blocks(part)))
    return _Result(pages, _unique(headers), _unique(footers))


# -- RTF ----------------------------------------------------------------------------------------

RTF_HEADERS = {"header", "headerl", "headerr", "headerf"}
RTF_FOOTERS = {"footer", "footerl", "footerr", "footerf"}


def _rtf_hidden(group, rtf):
    """True for a group switched to hidden text or tracked deletion by its own control words."""
    for child in group.children:
        if isinstance(child, rtf.Token) and child.kind == "word":
            if child.name in {"v", "deleted"} and child.param != 0:
                return True
    return False


def _rtf(ctx, path):
    from . import rtf

    document = rtf.parse(path.read_bytes())
    codepage = [c for c in document.children if isinstance(c, rtf.Token) and c.name == "ansicpg"]
    parts = {"header": [], "footer": [], "note": []}

    def strip(group):
        kept = []
        for child in group.children:
            if isinstance(child, rtf.Group):
                name, _ = child.destination()
                if name in {"annotation", "atnid", "atnauthor", "atntime", "atnref"} or _rtf_hidden(child, rtf):
                    continue
                if name == "object":
                    # Keep what the reader sees: the rendered result of the object.
                    for sub in child.children:
                        if isinstance(sub, rtf.Group) and sub.destination()[0] == "result":
                            strip(sub)
                            kept.append(sub)
                    continue
                strip(child)
                if name in RTF_HEADERS:
                    parts["header"].append(child)
                    continue
                if name in RTF_FOOTERS:
                    parts["footer"].append(child)
                    continue
                if name == "footnote":
                    parts["note"].append(child)
                    continue
            kept.append(child)
        group.children = kept

    strip(document)

    def collect(groups):
        wrapper = rtf.Group()
        wrapper.children = codepage + list(groups)
        return rtf.collect(wrapper)[0]

    def text_of(block):
        return re.sub(r"[ \t ]+", " ", "".join(char for char, _, _ in block)).strip()

    blocks, _, pictures, tables = rtf.collect(document)
    owner = {}
    for number, (rows, _) in enumerate(tables):
        for row in rows:
            for cell in row:
                for block in cell:
                    owner[id(block)] = number
    result, emitted = [], set()
    for block in blocks:
        number = owner.get(id(block))
        if number is None:
            text = text_of(block)
            if text:
                result.append(Block("p", text=text))
        elif number not in emitted:
            emitted.add(number)
            rows = [["\n".join(t for t in (text_of(b) for b in cell) if t) for cell in row]
                    for row in tables[number][0]]
            result.append(Block("table", rows=[row for row in rows if any(row)]))
    for group in pictures:
        words = {c.name for c in group.children if isinstance(c, rtf.Token) and c.kind == "word"}
        kind = ".png" if "pngblip" in words else ".jpg" if "jpegblip" in words else ""
        data = b""
        if kind and "bin" not in words:
            digits = b"".join(c.raw for c in group.children if isinstance(c, rtf.Token) and c.kind == "text")
            try:
                data = bytes.fromhex(digits.decode("ascii", "replace"))
            except ValueError:
                data = b""
        result.append(_image_block(ctx, data, kind))
    for block in collect(parts["note"]):
        if text_of(block):
            result.append(Block("note", text=text_of(block)))
    headers = ["\n".join(t for t in map(text_of, collect([g])) if t) for g in parts["header"]]
    footers = ["\n".join(t for t in map(text_of, collect([g])) if t) for g in parts["footer"]]
    return _Result([Page(None, result)], _unique(headers), _unique(footers))


# -- PDF and scans -------------------------------------------------------------------------------

def _words(text):
    return re.findall(r"\w+", text.casefold())


# Fonts such as Arial share one glyph for the space and the no-break space and for the hyphen and
# the soft hyphen; the PDF text layer then reports U+00A0 and U+00AD for what is drawn as " " and "-".
PDF_LAYER = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u00ad": "-"})


def _pdf_page(ctx, page):
    """Return (blocks, text seen for the Slovak-register check) of one page."""
    raw = [b for b in page.get_text("blocks", sort=True) if b[6] == 0]
    layer = [(b[1], b[0], clean_ocr(b[4].translate(PDF_LAYER), text_layer=True)) for b in raw]
    layer = [item for item in layer if item[2]]
    layer_text = "\n".join(item[2] for item in layer)
    if sum(c.isalpha() for c in layer_text) < 20 or "�" in layer_text:
        if not layer_text.strip() and blank(page):
            return [], ""
        _need_ocr(ctx)
        found = ocr_blocks(_render(page), ctx.tessdata)
        seen = layer_text + "\n" + "\n".join(found)
        if found:
            return [Block("p", text=text, ocr=True) for text in found], seen
        if layer:
            return [Block("p", text=item[2]) for item in layer], seen
        return [Block("image")], seen
    items = [(y, x, Block("p", text=text)) for y, x, text in layer]
    seen = layer_text
    known = set(_words(layer_text))
    area = page.rect.width * page.rect.height
    regions = []
    for info in page.get_image_info():
        box = pdf.Rect(info["bbox"]) & page.rect
        if not box.is_empty and box.width * box.height >= 0.01 * area and box not in regions:
            regions.append(box)
    for box in regions[:20]:
        if not ctx.has_ocr:
            items.append((box.y0, box.x0, Block("image")))
            continue
        found = ocr_blocks(_render(page, box), ctx.tessdata)
        seen += "\n" + "\n".join(found)
        fresh = [text for text in found
                 if (words := _words(text)) and sum(w in known for w in words) / len(words) < 0.8]
        items.extend((box.y0, box.x0, Block("p", text=text, ocr=True)) for text in fresh)
        if not found:
            items.append((box.y0, box.x0, Block("image")))
    items.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in items], seen


def _pdf(ctx, path):
    pages = []
    with pdf.open(path) as document:
        if document.needs_pass or not len(document):
            raise _Skip("pdf_encrypted_or_empty")
        for number, page in enumerate(document, 1):
            blocks, seen = _pdf_page(ctx, page)
            if slovak_register(seen):
                raise _Skip("withheld_slovak_register")
            pages.append(Page(number, blocks))
            ctx.page()
    return _Result(pages, [], [])


def _tiff(ctx, path):
    pages = []
    with pdf.open(path, filetype="tiff") as scan:
        if not scan.page_count or scan.page_count > 500:
            raise _Skip("scan_pages")
        for number, page in enumerate(scan, 1):
            if blank(page):
                pages.append(Page(number, []))
            else:
                _need_ocr(ctx)
                found = ocr_blocks(_render(page), ctx.tessdata)
                pages.append(Page(number, [Block("p", text=t, ocr=True) for t in found] or [Block("image")]))
            ctx.page()
    return _Result(pages, [], [])


def _image(ctx, path):
    _need_ocr(ctx)
    pix = _pixmap(path.read_bytes())
    if pix is None:
        raise _Skip("office_image_unreadable")
    if pix.width * pix.height > MAX_PIXELS:
        raise _Skip("office_image_too_large")
    text = "\n\n".join(ocr_blocks(pix, ctx.tessdata))
    block = Block("image", text=text, ocr=True) if text else Block("image")
    ctx.page()
    return _Result([Page(None, [block])], [], [])


# -- e-mail ----------------------------------------------------------------------------------------

HTML_DROP = {"script", "style", "head", "title", "iframe", "object", "embed", "link", "meta", "base", "form",
             "noscript", "applet"}
SIGNATURE_TYPES = {"application/pkcs7-signature", "application/x-pkcs7-signature", "application/pgp-signature"}


def _html_blocks(markup):
    from .mail import BLOCK_TAGS

    document = lxml_html.document_fromstring(markup or "<html></html>")
    paragraphs, current, quote = [], [], [0]

    def flush():
        text = _line("".join(current).replace(" ", " "))
        if text:
            paragraphs.append("> " * quote[0] + text)
        current.clear()

    def visit(node):
        tag = node.tag.lower() if isinstance(node.tag, str) else None
        if tag in HTML_DROP:
            return
        block = tag in BLOCK_TAGS
        if block:
            flush()
        if tag == "blockquote":
            quote[0] += 1
        if tag:
            current.append(node.text or "")
            for child in node:
                visit(child)
                current.append(child.tail or "")
        if tag == "blockquote":
            flush()
            quote[0] -= 1
        if block:
            flush()

    visit(document)
    flush()
    return [Block("p", text=text) for text in paragraphs]


def _text_blocks(text):
    chunks = re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return [Block("p", text=chunk) for chunk in
            ("\n".join(line.rstrip() for line in chunk.strip("\n").split("\n")) for chunk in chunks) if chunk.strip()]


def _part_text(part):
    try:
        return part.get_content()
    except (LookupError, ValueError, KeyError):
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def _mail_collect(part, texts, attachments):
    kind = part.get_content_type()
    if kind == "message/rfc822":
        inner = part.get_payload(0) if part.is_multipart() and part.get_payload() else None
        if inner is not None:
            attachments.append((lambda inner=inner: inner.as_bytes(policy=policy.SMTP), ".eml"))
        return
    if part.is_multipart():
        children = list(part.iter_parts())
        if kind == "multipart/alternative":
            plain = [c for c in children if c.get_content_type() == "text/plain"
                     and c.get_content_disposition() != "attachment"]
            chosen = plain[0] if plain else next((c for c in children if c.get_content_type() == "text/html"
                                                  or c.is_multipart()), None)
            for child in children:
                if child is chosen:
                    _mail_collect(child, texts, attachments)
                elif child.get_content_type() not in {"text/plain", "text/html"} and not child.is_multipart():
                    _mail_collect(child, texts, attachments)
            return
        for child in children:
            _mail_collect(child, texts, attachments)
        return
    if kind in SIGNATURE_TYPES:
        return
    try:
        filename = part.get_filename()
    except Exception:
        filename = None
    if kind in {"text/plain", "text/html"} and part.get_content_disposition() != "attachment" and not filename:
        texts.append(part)
        return
    suffix = Path(filename).suffix.lower() if filename else ""
    if not re.fullmatch(r"\.[a-z0-9]{1,6}", suffix):
        suffix = mimetypes.guess_extension(kind) or ""
    attachments.append((lambda part=part: part.get_payload(decode=True) or b"", suffix))


def _mail_date(message):
    value = message["Date"]
    if value is None:
        return None, ""
    try:
        moment = value.datetime if hasattr(value, "datetime") else parsedate_to_datetime(str(value))
    except Exception:
        moment = None
    if moment is None:
        return None, str(value)
    return moment.date().isoformat(), f"{_czech_date(moment)} {moment:%H:%M}"


def _eml(ctx, data):
    message = BytesParser(policy=policy.default).parsebytes(data)
    if not message.keys():
        raise ValueError("Soubor není platný e-mail.")
    lines = []
    for label, name in (("Od", "From"), ("Komu", "To"), ("Kopie", "Cc"), ("Předmět", "Subject")):
        try:
            value = str(message[name]) if message[name] is not None else ""
        except Exception:
            value = ""
        if value.strip():
            lines.append(f"{label}: {_line(value)}")
    iso, shown = _mail_date(message)
    if shown:
        lines.append(f"Datum: {shown}")
    blocks = [Block("mailheader", text="\n".join(lines))] if lines else []
    texts, attachments = [], []
    _mail_collect(message, texts, attachments)
    for part in texts:
        text = _part_text(part)
        blocks.extend(_html_blocks(text) if part.get_content_subtype() == "html" else _text_blocks(text))
    return _Result([Page(None, blocks)], [], [], iso, tuple(attachments))


def _msg(ctx, path):
    from .mail import msg_to_eml

    return _eml(ctx, msg_to_eml(path.read_bytes()).as_bytes(policy=policy.SMTP))


# -- plain text formats ------------------------------------------------------------------------------

def _text(ctx, path, suffix):
    data = path.read_bytes()
    if suffix == ".xml":
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True,
                                                      remove_comments=True, remove_pis=True))
        if root.getroottree().docinfo.doctype:
            raise FormatError("xml_dtd", "XML obsahuje nepodporované DTD.")
        blocks = []
        for node in root.iter():
            for value in (node.text, node.tail):
                if value and value.strip():
                    blocks.append(Block("p", text=_line(value)))
        return _Result([Page(None, blocks)], [], [])
    text = decode(data)
    if "\0" in text:
        raise FormatError("binary_as_text", "Soubor obsahuje binární data.")
    if suffix == ".json":
        return _Result([Page(None, [Block("p", text=json.dumps(json.loads(text), ensure_ascii=False, indent=2))])],
                       [], [])
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        if suffix == ".csv":
            try:
                delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;").delimiter
            except csv.Error:
                pass
        rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
        rows = [row for row in rows if any(row)]
        return _Result([Page(None, [Block("table", rows=rows)] if rows else [])], [], [])
    return _Result([Page(None, _text_blocks(text))], [], [])


# -- dispatch and walking ------------------------------------------------------------------------------

def _extract(ctx, path, suffix):
    if suffix == ".pdf":
        return _pdf(ctx, path)
    if suffix in formats.SCANS:
        return _tiff(ctx, path)
    if suffix in {".png", ".jpg", ".jpeg", ".bmp"}:
        return _image(ctx, path)
    if suffix == ".docx":
        return _docx(ctx, path)
    if suffix == ".xlsx":
        return _xlsx(ctx, path)
    if suffix == ".pptx":
        return _pptx(ctx, path)
    if suffix in formats.ODF:
        return _odf(ctx, path)
    if suffix == ".rtf":
        return _rtf(ctx, path)
    if suffix in formats.LEGACY:
        from .legacy import doc_to_docx, xls_to_xlsx

        converted = ctx.new_path(formats.LEGACY[suffix])
        try:
            converted.write_bytes((doc_to_docx if suffix == ".doc" else xls_to_xlsx)(path.read_bytes()))
            return (_docx if suffix == ".doc" else _xlsx)(ctx, converted)
        finally:
            converted.unlink(missing_ok=True)
    if suffix == ".eml":
        return _eml(ctx, path.read_bytes())
    if suffix == ".msg":
        return _msg(ctx, path)
    return _text(ctx, path, suffix)


def _format_name(suffix):
    return FORMAT_NAMES.get(suffix, suffix[1:].upper())


def _failure(error):
    if isinstance(error, (_Skip, FormatError)):
        return error.code
    code = reason_code(error)
    return "processing_error" if code == "other" else code


class _Limited:
    """Read at most ``limit`` bytes of a stream; more means the declared size was a lie."""

    def __init__(self, stream, limit):
        self.stream, self.left = stream, limit

    def read(self, size=-1):
        data = self.stream.read(size if size >= 0 else self.left + 1)
        self.left -= len(data)
        if self.left < 0:
            raise FormatError("size_limit", "Položka archivu překračuje limit velikosti.")
        return data


def _item(ctx, path, hint, parent, zip_depth, depth, unsupported):
    """Extract one file; yields its Document or Skipped (and those of its children)."""
    suffix = None
    try:
        suffix = identify(path, hint)
        if suffix is None or suffix not in SUPPORTED:
            raise _Skip(unsupported)
        if depth >= MAX_NESTING:
            raise _Skip("archive_depth")
        if path.stat().st_size > size_limit(suffix):
            raise _Skip("size_limit")
        if suffix == ".zip":
            yield from _zip(ctx, path, parent, zip_depth, depth)
            return
        result = _extract(ctx, path, suffix)
    except _Abort:
        raise
    except Exception as error:
        ctx.items += 1
        yield Skipped(ctx.next_index(), _format_name(suffix) if suffix in SUPPORTED else "UNKNOWN", _failure(error))
        ctx.tick()
        return
    index = ctx.next_index()
    ctx.items += 1
    yield Document(index, _format_name(suffix), result.pages, result.headers, result.footers, parent, result.date)
    ctx.tick()
    for supplier, child_hint in result.children:
        target = ctx.new_path(child_hint)
        try:
            data = supplier()
            if not data:
                continue
            target.write_bytes(data)
            del data
            yield from _item(ctx, target, child_hint, index, zip_depth, depth + 1, "attachment_unsupported")
        finally:
            target.unlink(missing_ok=True)


def _zip(ctx, path, parent, zip_depth, depth):
    def skipped(name, code):
        ctx.items += 1
        return Skipped(ctx.next_index(), name, code)

    if zip_depth >= MAX_ZIP_DEPTH:
        yield skipped("ZIP", "archive_depth")
        return
    try:
        archive = ZipFile(path)
    except Exception as error:
        yield skipped("ZIP", _failure(error))
        return
    with archive:
        for entry in archive.infolist():
            ctx.budget[0] -= entry.file_size
            ctx.budget[1] -= 1
            if min(ctx.budget) < 0:
                yield skipped("ZIP", "archive_limit")
                return
            if entry.is_dir():
                continue
            if entry.flag_bits & 1:
                yield skipped("UNKNOWN", "archive_encrypted")
                continue
            if entry.file_size > formats.ARCHIVE_BYTES:
                yield skipped("UNKNOWN", "size_limit")
                continue
            hint = Path(entry.filename).suffix.lower()
            target = ctx.new_path(hint)
            try:
                try:
                    with archive.open(entry) as source, target.open("wb") as destination:
                        shutil.copyfileobj(_Limited(source, formats.ARCHIVE_BYTES), destination)
                except Exception as error:
                    yield skipped("UNKNOWN", _failure(error))
                    continue
                yield from _item(ctx, target, hint, parent, zip_depth + 1, depth + 1, "archive_member_unsupported")
            finally:
                target.unlink(missing_ok=True)


def _run(path, hint, tessdata, progress):
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="SpisRedakt-extract-") as folder:
        ctx = _Context(tessdata, progress, folder)
        try:
            yield from _item(ctx, path, hint if hint is not None else path.suffix.lower(), None, 0, 0,
                             "format_unsupported")
        except _Abort as abort:
            raise abort.error from None


def walk(path, tessdata=None, progress=None) -> Iterator["Document | Skipped"]:
    """Yield a Document or Skipped for every item of a file or ZIP archive, depth first.

    ``progress(items, pages)`` is called after every item and page.
    """
    return _run(path, None, tessdata, progress)


def extract_file(path, suffix, tessdata=None, progress=None) -> "list[Document | Skipped]":
    """Extract one file whose extension is ``suffix`` (e-mail attachments and archives included)."""
    return list(_run(path, suffix, tessdata, progress))
