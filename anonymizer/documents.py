from pathlib import Path
import ctypes
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PARTS = re.compile(r"word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml")
OMIT = {W + name for name in ("del", "moveFrom", "drawing", "pict", "object", "txbxContent")}
MAX_PIXELS = 30_000_000


def ocr_path(folder):
    path = str(Path(folder).resolve())
    if os.name == "nt" and not path.isascii():
        short = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(path, short, len(short))
        if 0 < length < len(short) and short.value.isascii():
            return short.value
        relative = os.path.relpath(path) if Path(path).drive == Path.cwd().drive else path
        if relative.isascii():
            return relative
        raise ValueError("Pro OCR vyberte složku, jejíž úplná cesta neobsahuje diakritiku.")
    return path


def visible_text(node):
    if node.tag in OMIT or node.tag == W + "p":
        return ""
    if node.tag == W + "t":
        return node.text or ""
    if node.tag in {W + "br", W + "cr", W + "tab"}:
        return " "
    return "".join(visible_text(child) for child in node)


def paragraphs(node):
    if node.tag in OMIT:
        return
    if node.tag == W + "p":
        yield "".join(visible_text(child) for child in node)
        return
    for child in node:
        yield from paragraphs(child)


def read_docx(source):
    with ZipFile(source) as archive:
        names = archive.namelist()
        if names.count("word/document.xml") != 1:
            raise ValueError("Neplatný DOCX.")
        for name in sorted(names, key=lambda item: (item != "word/document.xml", item)):
            if PARTS.fullmatch(name):
                if archive.getinfo(name).file_size > 25_000_000:
                    raise ValueError("Příliš velká část DOCX.")
                yield from paragraphs(ET.fromstring(archive.read(name)))


def redact_docx(source, destination, rules):
    output = Document()
    count = 0
    for paragraph in read_docx(source):
        replaced, matches = rules.replace(paragraph)
        output.add_paragraph(replaced)
        count += matches
    if not any(p.text.strip() for p in output.paragraphs):
        raise ValueError("DOCX nemá podporovaný text.")
    output.save(destination)
    if any(rules.find(text) for text in read_docx(destination)):
        raise ValueError("Ve výstupu zůstal detekovatelný údaj.")


def text_geometry(raw):
    text = []
    boxes = []
    line_id = 0
    for block in raw["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                for char in span["chars"]:
                    for letter in char["c"]:
                        text.append(letter)
                        boxes.append((line_id, pdf.Rect(char["bbox"])))
            text.append("\n")
            boxes.append(None)
            line_id += 1
    return "".join(text), boxes


def blackout(pixmap, rectangle, matrix):
    pixel_rect = (rectangle * matrix).irect
    pixel_rect.x0 -= 2
    pixel_rect.y0 -= 2
    pixel_rect.x1 += 2
    pixel_rect.y1 += 2
    pixel_rect &= pixmap.irect
    if pixel_rect.is_empty or not pixmap.set_rect(pixel_rect, (0, 0, 0)):
        raise ValueError("Oblast redakce je mimo obraz stránky.")
    return pixel_rect


def redact_geometry(pixmap, rules, text, geometry, transform):
    redacted = []
    for item in rules.find(text):
        lines = {}
        for entry in geometry[item.start:item.end]:
            if entry is not None:
                line_id, bbox = entry
                lines[line_id] = lines.get(line_id, pdf.Rect()) | bbox
        if not lines:
            raise ValueError("Nalezený údaj nelze umístit na stránku.")
        redacted.extend(blackout(pixmap, area, transform) for area in lines.values())
    return redacted


def recognize_image(pixmap, tessdata):
    if tessdata is None or not (Path(tessdata) / "ces.traineddata").is_file():
        raise ValueError("Obraz vyžaduje česká data OCR (ces.traineddata).")
    with pdf.open("pdf", pixmap.pdfocr_tobytes(language="ces", tessdata=ocr_path(tessdata))) as recognized:
        page = recognized[0]
        text, boxes = text_geometry(page.get_text("rawdict", flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES))
        matrix = pdf.Matrix(pixmap.width / page.rect.width, pixmap.height / page.rect.height)
        return text, boxes, matrix


def blank(page):
    """True for an empty page, such as the back of a scanned sheet."""
    preview = page.get_pixmap(dpi=40, colorspace=pdf.csGRAY, annots=False, alpha=False)
    dark = sum(1 for value in preview.samples if value < 160)
    return dark <= 0.002 * preview.width * preview.height


def slovak_register(text):
    """True for text that looks like a Slovak criminal-record extract or form (never released)."""
    folded = "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c))
    return bool(re.search(r"regist(?:er|r[aeiu]?)\s+trestov", folded)
                or ("priezvisko" in folded and re.search(r"narodenia|ziadatel", folded)))


def redact_pdf(source, destination, rules, tessdata=None, force_ocr=False):
    expected = []
    with pdf.open(source) as original, pdf.open() as output:
        # A repaired cross-reference table is common in scanner output; every page
        # is re-rendered anyway, so only password protection and emptiness stop it.
        if original.needs_pass or not len(original):
            raise ValueError("PDF je šifrované, poškozené nebo prázdné.")
        for page in original:
            # Large drawings and posters are rendered below 300 DPI to fit the pixel limit.
            dpi = min(300, int(72 * (MAX_PIXELS / max(page.rect.width * page.rect.height, 1)) ** 0.5))
            if dpi < 50:
                raise ValueError("Stránka přesahuje limit velikosti.")
            scale = pdf.Matrix(dpi / 72, dpi / 72)
            pixmap = page.get_pixmap(matrix=scale, annots=False, alpha=False)
            raw = page.get_text("rawdict", flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES)
            text, boxes = text_geometry(raw)
            matrix = page.rotation_matrix * scale
            sources = [(text, boxes, matrix)]
            blacked = False
            if force_ocr or page.get_image_info() or page.get_drawings() or not text.strip() or "\ufffd" in text:
                recognized = recognize_image(pixmap, tessdata)
                if recognized[0].strip():
                    sources.append(recognized)
                elif not text.strip() and not blank(page):
                    # A photo, signature or unreadable scan: OCR cannot rule out
                    # personal data, so the whole page is blacked out.
                    pixmap.clear_with(0)
                    blacked = True
            if slovak_register(" ".join(content for content, _, _ in sources)):
                raise ValueError("Slovenský výpis nebo formulář nebyl vydán: rozložení osobních polí zatím nelze spolehlivě ověřit.")
            redacted = [pixmap.irect] if blacked else []
            for content, geometry, transform in sources:
                redacted.extend(redact_geometry(pixmap, rules, content, geometry, transform))
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, pixmap=pixmap, keep_proportion=False)
            expected.append(redacted)
        output.save(destination, garbage=4, deflate=True)
    validate_pdf(destination, expected)


def validate_pdf(destination, expected):
    with pdf.open(destination) as result:
        if len(result) != len(expected) or result.embfile_count() or result.get_xml_metadata():
            raise ValueError("PDF neprošlo strukturální kontrolou.")
        for page, areas in zip(result, expected, strict=True):
            images = page.get_images()
            if (page.get_text().strip() or page.get_links() or page.first_annot or page.first_widget
                    or len(images) != 1 or images[0][1]):
                raise ValueError("PDF obsahuje neočekávanou vrstvu.")
            if areas:
                image = pdf.Pixmap(result, images[0][0])
                for area in areas:
                    if any(image.color_count(clip=area, colors=True).keys() - {b"\x00\x00\x00"}):
                        raise ValueError("Výstupní obraz neobsahuje úplnou redakci.")
