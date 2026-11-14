from pathlib import Path
import ctypes
import hashlib
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

from . import forms
from .czech import fold


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
    """Characters with boxes. Pieces of one visual row are joined into one line, so a value
    drawn as a separate text object still follows its cue ("IČO" + number)."""
    text = []
    boxes = []
    line_id = 0
    previous = None
    for block in raw["blocks"]:
        for line in block.get("lines", ()):
            chars = [(letter, pdf.Rect(char["bbox"])) for span in line["spans"]
                     for char in span["chars"] for letter in char["c"]]
            if not chars:
                continue
            rect = pdf.Rect(line["bbox"])
            level = abs(line.get("dir", (1, 0))[1]) < 0.01 and line.get("dir", (1, 0))[0] > 0
            height = max(min(rect.height, previous.height if previous else rect.height), 1)
            if (previous is not None and level and min(rect.y1, previous.y1) - max(rect.y0, previous.y0) > 0.6 * height
                    and rect.x0 >= previous.x1 - 0.5 * height):
                text.pop()
                boxes.pop()
                line_id -= 1
                gap = rect.x0 - previous.x1
                joint = "   " if gap > 1.5 * height else " " if gap > 0.15 * height else ""
                if text and text[-1] == " " and joint == " ":
                    joint = ""
                text.extend(joint)
                boxes.extend([None] * len(joint))
            for letter, box in chars:
                text.append(letter)
                boxes.append((line_id, box))
            text.append("\n")
            boxes.append(None)
            line_id += 1
            previous = rect if level else None
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


def redact_geometry(pixmap, rules, text, geometry, transform, reference=None):
    """Black out every finding. reference is the untouched page image used to find where
    the ink of a value really ends (earlier black bars must not be mistaken for ink)."""
    redacted = []
    for item in rules.find(text):
        lines = {}
        segment, last = 0, None
        for entry in geometry[item.start:item.end]:
            if entry is not None:
                line_id, bbox = entry
                # a wide gap (another column) starts a new rectangle instead of covering what lies between
                if last is not None and last[0] == line_id and bbox.x0 - last[1].x1 > 1.2 * max(bbox.height, 1):
                    segment += 1
                last = entry
                lines[line_id, segment] = lines.get((line_id, segment), pdf.Rect()) | bbox
        if not lines:
            raise ValueError("Nalezený údaj nelze umístit na stránku.")
        redacted.extend(blackout(pixmap, snap_to_ink(reference or pixmap, area, transform), PIXELS)
                        for area in lines.values())
    return redacted


def snap_to_ink(pixmap, rectangle, matrix):
    """Grow a pixel rectangle along the text until blank paper is reached on both ends.

    Character boxes, above all those from OCR, are approximate; without this the last
    letter of a value can stay visible beside the black bar."""
    area = (rectangle * matrix).irect & pixmap.irect
    if area.is_empty:
        return pdf.Rect((rectangle * matrix).irect)
    samples, stride, count = pixmap.samples, pixmap.stride, pixmap.n
    across = area.width >= area.height                  # text runs along x (else along y, rotated pages)
    thickness = area.height if across else area.width
    gap, limit = max(3, int(0.12 * thickness)), thickness
    low, high = (area.y0, area.y1) if across else (area.x0, area.x1)

    def inked(position):
        for other in range(low, high):
            x, y = (position, other) if across else (other, position)
            offset = (y - pixmap.y) * stride + (x - pixmap.x) * count
            if min(samples[offset:offset + min(count, 3)]) < 170:
                return True
        return False

    start, end = (area.x0, area.x1) if across else (area.y0, area.y1)
    bound = (pixmap.x + pixmap.width) if across else (pixmap.y + pixmap.height)
    origin = pixmap.x if across else pixmap.y
    blank, position = 0, end
    while position < min(bound, end + limit) and blank < gap:
        if inked(position):
            end, blank = position + 1, 0
        else:
            blank += 1
        position += 1
    blank, position, first = 0, start - 1, start
    while position >= max(origin, first - limit) and blank < gap:
        if inked(position):
            start, blank = position, 0
        else:
            blank += 1
        position -= 1
    return pdf.Rect(start, area.y0, end, area.y1) if across else pdf.Rect(area.x0, start, area.x1, end)


def has_ocr(tessdata):
    return tessdata is not None and (Path(tessdata) / "ces.traineddata").is_file()


def recognize_image(pixmap, tessdata):
    if not has_ocr(tessdata):
        raise ValueError("Obraz vyžaduje česká data OCR (ces.traineddata).")
    with pdf.open("pdf", pixmap.pdfocr_tobytes(language="ces", tessdata=ocr_path(tessdata))) as recognized:
        page = recognized[0]
        text, boxes = text_geometry(page.get_text("rawdict", flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES))
        matrix = pdf.Matrix(pixmap.width / page.rect.width, pixmap.height / page.rect.height)
        return text, boxes, matrix


SCALE = pdf.Matrix(300 / 72, 300 / 72)
PIXELS = pdf.Matrix(1, 1)


def render(page):
    if (page.rect * SCALE).irect.get_area() > MAX_PIXELS:
        raise ValueError("Stránka přesahuje limit velikosti.")
    return page.get_pixmap(matrix=SCALE, annots=False, alpha=False)


def read_page(page, pixmap, tessdata, force_ocr):
    """Text sources of one page: its text layer and, where the page has graphics, OCR."""
    raw = page.get_text("rawdict", flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES)
    text, boxes = text_geometry(raw)
    sources = [(text, boxes, page.rotation_matrix * SCALE)]
    if force_ocr or page.get_image_info() or page.get_drawings() or not text.strip() or "�" in text:
        recognized = recognize_image(pixmap, tessdata)
        if not recognized[0].strip() and not text.strip():
            raise ValueError("OCR nenašlo čitelný text. Stránku zkontrolujte ručně.")
        sources.append(recognized)
    return sources


def mostly_black(pixmap, rect):
    area = pdf.Rect(rect).irect & pixmap.irect
    if area.is_empty:
        return True
    colors = pixmap.color_count(clip=area, colors=True)
    return colors.get(b"\x00\x00\x00", 0) >= 0.6 * sum(colors.values())


def form_areas(labels, width, extend):
    """Areas blacked out on a form page. Kept separate so tests can break it on purpose."""
    return forms.band_areas(labels, width, extend)


def recheck(pixmap, rules, tessdata, redacted, form):
    """Read the finished page image again, independently of what the detectors reported."""
    for healed in range(3):
        text, boxes, matrix = recognize_image(pixmap, tessdata)
        if form:
            words = forms.page_words(text, boxes, matrix)
            left = [word for word in forms.values(forms.find_labels(words), words)
                    if len(word.plain) > 1 and not mostly_black(pixmap, word.rect)]
            if left:
                raise forms.FormError("form_field_unredacted",
                                      "Formulář nebyl vydán: kontrola výstupu našla nezačerněné pole.")
        if not rules.find(text):
            return healed
        if healed == 2:
            break
        redacted.extend(redact_geometry(pixmap, rules, text, boxes, matrix))
    raise forms.FormError("residual_data", "PDF nebylo vydáno: ve výstupním obrazu zůstal čitelný údaj.")


def redact_pdf(source, destination, rules, tessdata=None, force_ocr=False, report=None):
    report = {} if report is None else report
    expected, digests, analysed = [], [], []
    with pdf.open(source) as original, pdf.open() as output:
        if original.needs_pass or original.is_repaired or not len(original):
            raise ValueError("PDF je šifrované, poškozené nebo prázdné.")
        # Pass 1: read every page and learn names and values before anything is redacted.
        for page in original:
            sources = read_page(page, render(page), tessdata, force_ocr)
            for content, _, _ in sources:
                rules.learn(content)
            words = [forms.page_words(*item) for item in sources]
            labels = [forms.find_labels(item) for item in words]
            found = set().union(*(forms.fields(item) for item in labels))
            analysed.append((sources, words, labels, fold(" ".join(item[0] for item in sources)), found))
        extend = forms.check_document([(folded, found) for *_, folded, found in analysed])
        is_form = any(found for *_, found in analysed)
        if is_form and not has_ocr(tessdata):
            raise forms.FormError("form_needs_ocr", "Formulář nebyl vydán: kontrola výstupu vyžaduje data OCR.")
        for (_, words, labels, _, _), wider in zip(analysed, extend):
            for page_words, page_labels in zip(words, labels):
                for value in forms.values(page_labels, page_words, wider):
                    rules.remember_literal(value.text)
        # Pass 2: redact the rendered image and verify the result.
        healed = 0
        for page, (sources, _, labels, _, found), wider in zip(original, analysed, extend):
            pixmap = render(page)
            clean = pdf.Pixmap(pixmap)
            blocks = forms.graphic_blocks(pixmap) if is_form else []
            redacted = []
            for page_labels in labels if found else ():
                redacted.extend(blackout(pixmap, area, PIXELS) for area in form_areas(page_labels, pixmap.width, wider))
            redacted.extend(blackout(pixmap, area, PIXELS) for area in blocks)
            for content, geometry, transform in sources:
                redacted.extend(redact_geometry(pixmap, rules, content, geometry, transform, clean))
            if has_ocr(tessdata):
                healed += recheck(pixmap, rules, tessdata, redacted, bool(found))
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, pixmap=pixmap, keep_proportion=False)
            expected.append(redacted)
            digests.append(hashlib.sha256(pixmap.samples).digest())
        output.save(destination, garbage=4, deflate=True)
    validate_pdf(destination, expected, digests)
    report.update(pages=len(expected), form=is_form, form_extended=any(extend),
                  output_reread="ocr" if has_ocr(tessdata) else "unavailable", healed_rounds=healed,
                  areas=sum(map(len, expected)))


def validate_pdf(destination, expected, digests=None):
    with pdf.open(destination) as result:
        if len(result) != len(expected) or result.embfile_count() or result.get_xml_metadata():
            raise ValueError("PDF neprošlo strukturální kontrolou.")
        for number, (page, areas) in enumerate(zip(result, expected, strict=True)):
            images = page.get_images()
            if (page.get_text().strip() or page.get_links() or page.first_annot or page.first_widget
                    or len(images) != 1 or images[0][1]):
                raise ValueError("PDF obsahuje neočekávanou vrstvu.")
            image = pdf.Pixmap(result, images[0][0])
            if digests is not None and hashlib.sha256(image.samples).digest() != digests[number]:
                raise ValueError("Uložený obraz stránky se liší od zkontrolovaného.")
            for area in areas:
                if any(image.color_count(clip=area, colors=True).keys() - {b"\x00\x00\x00"}):
                    raise ValueError("Výstupní obraz neobsahuje úplnou redakci.")
