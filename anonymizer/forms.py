"""Personal-data forms (Slovak criminal-record extract and alike), handled by position.

The value of a field is whatever sits beside or below its label, so the whole row band
of every recognised label is blacked out except the label itself. Reading order of the
extracted text plays no role; only coordinates do.
"""
import re
from dataclasses import dataclass
from statistics import median

import pymupdf as pdf

from .czech import FORM_LABELS, fold

FIELDS = {
    "meno": {"meno"}, "jmeno": {"meno"}, "priezvisko": {"priezvisko"}, "prijmeni": {"priezvisko"},
    "meno a priezvisko": {"meno", "priezvisko"}, "jmeno a prijmeni": {"meno", "priezvisko"},
    "rodne priezvisko": {"rodne priezvisko"}, "rodne prijmeni": {"rodne priezvisko"},
    "datum narodenia": {"datum narodenia"}, "datum narozeni": {"datum narodenia"},
    "miesto narodenia": {"miesto narodenia"}, "misto narozeni": {"miesto narodenia"},
    "stat narodenia": {"stat narodenia"}, "stat narozeni": {"stat narodenia"},
    "statne obcianstvo": {"obcianstvo"}, "statna prislusnost": {"obcianstvo"},
    "statni obcanstvi": {"obcianstvo"}, "statni prislusnost": {"obcianstvo"},
    "cislo dokladu": {"doklad"}, "cislo dokladu totoznosti": {"doklad"},
    "druh a cislo dokladu totoznosti": {"doklad"}, "cislo obcianskeho preukazu": {"doklad"},
    "cislo pasu": {"doklad"}, "cislo op": {"doklad"}, "cislo obcanskeho prukazu": {"doklad"},
    "rodne cislo": {"rodne cislo"}, "pohlavie": {"pohlavie"}, "pohlavi": {"pohlavie"},
    "cislo ziadosti": {"ziadost"}, "cislo zadosti": {"ziadost"},
    "kod vypisu": {"kod"}, "kod rt": {"kod"}, "kod odpisu": {"kod"},
}
REQUIRED = {"meno", "priezvisko", "rodne priezvisko", "datum narodenia", "miesto narodenia", "stat narodenia",
            "obcianstvo", "doklad", "rodne cislo", "pohlavie", "ziadost", "kod"}
CORE = {"meno", "priezvisko", "datum narodenia"}
MINIMUM = 8
REGISTER_TITLE = re.compile(r"regist(?:er|r[aeiu]?)\s+trestov|rejstrik\w*\s+trestu")


class FormError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass
class Word:
    text: str
    rect: pdf.Rect          # pixel coordinates of the rendered page

    @property
    def plain(self):
        return re.sub(r"^\W+|\W+$", "", fold(self.text))


@dataclass
class Label:
    phrase: str
    rect: pdf.Rect
    words: list


def page_words(text, boxes, matrix):
    """Words with pixel rectangles from the character geometry of one text source."""
    words, chars, state = [], [], None

    def flush():
        if chars:
            rect = pdf.Rect()
            for _, box in chars:
                rect |= box
            words.append(Word("".join(char for char, _ in chars), rect))
            chars.clear()

    for char, entry in zip(text, boxes):
        if entry is None or char.isspace():
            flush()
            continue
        line, box = entry
        box = pdf.Rect(box) * matrix
        box.normalize()
        if chars and (line != state or box.x0 - chars[-1][1].x1 > 0.7 * max(box.height, 1)):
            flush()
        state = line
        chars.append((char, box))
    flush()
    return [word for word in words if not word.rect.is_empty]


def rows_of(words):
    rows = []
    for word in sorted(words, key=lambda item: (item.rect.y0 + item.rect.y1) / 2):
        centre = (word.rect.y0 + word.rect.y1) / 2
        if rows and abs(centre - rows[-1][0]) <= 0.5 * max(word.rect.height, rows[-1][1]):
            rows[-1][2].append(word)
        else:
            rows.append([centre, word.rect.height, [word]])
    return [sorted(row[2], key=lambda item: item.rect.x0) for row in rows]


def distance(left, right, limit):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, 1):
        current = [row]
        for column, b in enumerate(right, 1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (a != b)))
        previous = current
    return previous[-1]


def similar(word, token):
    limit = 0 if len(token) <= 3 else 1 if len(token) <= 6 else 2
    return distance(word, token, limit) <= limit


def find_labels(words):
    """Field labels that stand as labels (colon or isolated cell), not words of a sentence."""
    labels = []
    for row in rows_of(words):
        index = 0
        while index < len(row):
            height = max(row[index].rect.height, 1)
            for phrase in FORM_LABELS:
                part = row[index:index + len(phrase)]
                if len(part) != len(phrase) or not all(similar(word.plain, token) for word, token in zip(part, phrase)):
                    continue
                if any(right.rect.x0 - left.rect.x1 > 1.5 * height for left, right in zip(part, part[1:])):
                    continue
                following = row[index + len(phrase)] if index + len(phrase) < len(row) else None
                colon = part[-1].text.rstrip().endswith(":") or (following is not None and following.text.strip() == ":")
                apart_before = index == 0 or part[0].rect.x0 - row[index - 1].rect.x1 > height
                apart_after = following is None or following.rect.x0 - part[-1].rect.x1 > height
                if not (colon or (apart_before and apart_after)):
                    continue
                rect = pdf.Rect()
                for word in part:
                    rect |= word.rect
                labels.append(Label(" ".join(phrase), rect, part))
                index += len(phrase) - 1
                break
            index += 1
    return labels


def fields(labels):
    found = set()
    for label in labels:
        found |= FIELDS.get(label.phrase, set())
    return found


def bands(labels, extend=False):
    """Horizontal bands (top, bottom, labels) holding the values of the labelled fields."""
    if not labels:
        return []
    rows = []
    for label in sorted(labels, key=lambda item: item.rect.y0):
        if rows and label.rect.y0 < rows[-1][0].rect.y0 + 0.6 * rows[-1][0].rect.height:
            rows[-1].append(label)
        else:
            rows.append([label])
    height = median(label.rect.height for label in labels)
    tops = [min(label.rect.y0 for label in row) - 0.25 * height for row in rows]
    gaps = [later - earlier for earlier, later in zip(tops, tops[1:]) if later - earlier < 8 * height]
    pitch = max(median(gaps) if gaps else 0, 2.6 * height)
    result = []
    for number, row in enumerate(rows):
        top = tops[number]
        last = number + 1 == len(rows) or tops[number + 1] - top > 1.8 * pitch
        bottom = top + pitch * (2 if extend and last else 1) if last else tops[number + 1]
        first = number == 0 or top - tops[number - 1] > 1.8 * pitch
        if extend and first:
            top -= pitch
        result.append((top, bottom, row))
    return result


def band_areas(labels, width, extend=False):
    """Rectangles to black out: every band in full page width, minus the labels."""
    areas = []
    for top, bottom, row in bands(labels, extend):
        row = sorted(row, key=lambda item: item.rect.x0)
        edge = 0
        for label in row:
            keep = label.rect
            if keep.x0 > edge:
                areas.append(pdf.Rect(edge, top, keep.x0, bottom))
            left, right = max(keep.x0, edge), max(keep.x1, edge)
            if keep.y0 - top >= 3:
                areas.append(pdf.Rect(left, top, right, keep.y0))
            if bottom - keep.y1 >= 3:
                areas.append(pdf.Rect(left, keep.y1, right, bottom))
            edge = right
        if width > edge:
            areas.append(pdf.Rect(edge, top, width, bottom))
    return [area for area in areas if area.width >= 1 and area.height >= 1]


def values(labels, words, extend=False):
    """Words lying in the bands that are not labels: the personal values themselves."""
    kept = {id(word) for label in labels for word in label.words}
    found = []
    for top, bottom, _ in bands(labels, extend):
        for word in words:
            centre = (word.rect.y0 + word.rect.y1) / 2
            if top <= centre <= bottom and id(word) not in kept and word.plain:
                found.append(word)
    return found


def check_document(pages):
    """pages: per page (folded text, set of fields). Decide whether the form may be released.

    Returns per page True when bands must be extended (some expected field was not found).
    """
    form_pages = [found for _, found in pages if found]
    titled = [bool(REGISTER_TITLE.search(text)) for text, _ in pages]
    if not form_pages:
        if any(titled):
            raise FormError("form_fields_missing",
                            "Výpis z registra trestov nebyl vydán: na stránce nebyla nalezena osobní pole.")
        return [False] * len(pages)
    for found in form_pages:
        if not CORE <= found or not {"rodne cislo", "doklad"} & found or len(found & REQUIRED) < MINIMUM:
            raise FormError("form_incomplete",
                            "Slovenský výpis nebo formulář nebyl vydán: nepodařilo se spolehlivě najít povinná osobní pole.")
    return [bool(found) and not REQUIRED <= found for _, found in pages]


def graphic_blocks(pixmap):
    """Dense non-text blocks (QR codes, barcodes, photographs, stamps) in pixel rectangles."""
    gray = pdf.Pixmap(pdf.csGRAY, pixmap) if pixmap.n != 1 else pdf.Pixmap(pixmap)
    gray.shrink(4)
    width, height, samples, stride = gray.width, gray.height, gray.samples, gray.stride
    dark = [[samples[y * stride + x] < 150 for x in range(width)] for y in range(height)]
    seen = [[False] * width for _ in range(height)]
    blocks = []
    for y in range(height):
        for x in range(width):
            if not dark[y][x] or seen[y][x]:
                continue
            stack, cells = [(x, y)], 0
            seen[y][x] = True
            x0 = x1 = x
            y0 = y1 = y
            while stack:
                cx, cy = stack.pop()
                cells += 1
                x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and dark[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            columns, lines = x1 - x0 + 1, y1 - y0 + 1
            if lines >= 5 and columns >= 5 and cells >= 0.5 * columns * lines:
                scale_x, scale_y = pixmap.width / width, pixmap.height / height
                blocks.append(pdf.Rect(x0 * scale_x - 8, y0 * scale_y - 8, (x1 + 1) * scale_x + 8, (y1 + 1) * scale_y + 8))
    return blocks
