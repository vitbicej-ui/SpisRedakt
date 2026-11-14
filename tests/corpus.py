"""Synthetic reference documents with known ground truth. Every value here is invented."""
import os
import random
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.shared import Pt
import pymupdf as pdf

FONT_FILE = next((str(path) for path in (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
) if path.is_file()), None)
FONT = pdf.Font(fontfile=FONT_FILE) if FONT_FILE else None


@dataclass
class Truth:
    """What must disappear and what must stay, with page positions for PDF variants."""
    secret: list = field(default_factory=list)     # (page, rect, text)
    keep: list = field(default_factory=list)       # (page, rect, text)

    @property
    def secret_texts(self):
        return sorted({text for _, _, text in self.secret})

    @property
    def keep_texts(self):
        return sorted({text for _, _, text in self.keep})


def put(page, truth, x, y, text, secret=None, size=11):
    """Draw text at the baseline point and record its rectangle. Returns the right edge."""
    page.insert_text((x, y), text, fontsize=size, fontname="F0", fontfile=FONT_FILE)
    width = FONT.text_length(text, size)
    rect = pdf.Rect(x, y - 0.75 * size, x + width, y + 0.22 * size)
    if secret is not None:
        (truth.secret if secret else truth.keep).append((page.number, rect, text))
    return x + width


def as_scan(document, dpi=200, damage=None):
    """Image-only copy of a document, optionally with OCR-hostile speckles over given rects."""
    scan = pdf.open()
    for page in document:
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        if damage:
            rng = random.Random(7)
            scale = dpi / 72
            for rect in damage.get(page.number, ()):
                area = (rect * pdf.Matrix(scale, scale)).irect
                for _ in range(int(area.width * area.height * 0.10)):
                    x, y = rng.randrange(area.x0, area.x1), rng.randrange(area.y0, area.y1)
                    pixmap.set_pixel(x, y, (255, 255, 255))
        sheet = scan.new_page(width=page.rect.width, height=page.rect.height)
        sheet.insert_image(sheet.rect, pixmap=pixmap)
    return scan


# ---------------------------------------------------------------- Slovak criminal-record extract
SK_FIELDS = [
    ("Meno", "Ctibor"), ("Priezvisko", "Vzorovský"),
    ("Rodné priezvisko", "Skúšobný"), ("Dátum narodenia", "29.02.1984"),
    ("Miesto narodenia", "Testovce"), ("Štát narodenia", "Slovenská republika"),
    ("Štátne občianstvo", "Slovenská republika"), ("Číslo dokladu", "ZZ123456"),
    ("Rodné číslo", "840229/9999"), ("Pohlavie", "muž"),
    ("Číslo žiadosti", "RT-2026-0042817"), ("Kód výpisu", "K7Q9-XW42-TT01"),
]
SK_NAME = "Ctibor Vzorovský"


def slovak_register(layout="beside", title=True, order="rows", fields=SK_FIELDS, qr=True):
    """Two-column personal form. layout: value beside or below its label.
    order: sequence in which text objects are written (affects extraction order only)."""
    document = pdf.open()
    page = document.new_page()
    truth = Truth()
    jobs = []
    if title:
        jobs.append((60, 70, "GENERÁLNA PROKURATÚRA SLOVENSKEJ REPUBLIKY", False, 12))
        jobs.append((60, 95, "VÝPIS Z REGISTRA TRESTOV", False, 15))
    top = 140
    step = 26 if layout == "beside" else 44
    labels, values = [], []
    for index, (label, value) in enumerate(fields):
        x = 60 if index % 2 == 0 else 320
        y = top + (index // 2) * step
        labels.append((x, y, label + ":", False, 11))
        if layout == "beside":
            values.append((x + FONT.text_length(label + ":", 11) + 8, y, value, True, 11))
        else:
            values.append((x, y + 16, value, True, 11))
    bottom = top + ((len(fields) + 1) // 2) * step + 40
    body = [
        (60, bottom, "Podľa záznamov registra trestov osoba", False, 11),
        (60 + FONT.text_length("Podľa záznamov registra trestov osoba ", 11), bottom, SK_NAME, True, 11),
        (60, bottom + 18, "nemá záznam o odsúdení.", False, 11),
        (60, bottom + 60, "Výpis vyhotovený dňa 13.11.2026 v Bratislave.", False, 11),
    ]
    if order == "rows":
        jobs += [item for pair in zip(labels, values) for item in pair] + body
    elif order == "values_first":
        jobs += body + values + labels
    else:                                   # right column first, then left
        jobs += [item for group in (labels, values) for item in group if item[0] >= 300]
        jobs += [item for group in (values, labels) for item in group if item[0] < 300] + body
    for x, y, text, secret, size in jobs:
        put(page, truth, x, y, text, secret, size)
    if qr:
        rng = random.Random(3)
        origin, cell = pdf.Point(430, bottom + 90), 4
        for row in range(25):
            for column in range(25):
                if rng.random() < 0.5 or row in (0, 24) or column in (0, 24):
                    page.draw_rect(pdf.Rect(origin.x + column * cell, origin.y + row * cell,
                                            origin.x + (column + 1) * cell, origin.y + (row + 1) * cell),
                                   color=None, fill=(0, 0, 0))
        truth.secret.append((0, pdf.Rect(origin.x + 4, origin.y + 4, origin.x + 96, origin.y + 96), "<QR>"))
    return document, truth


# ---------------------------------------------------------------- Czech court order
COMPANY = "Severomoravská stavební a obchodní společnost s.r.o."
# Lines of segments: (text, secret). Secret segments must vanish entirely.
CZ_LINES = [
    [("USNESENÍ", False)],
    [("Okresní soud v Testově rozhodl samosoudkyní Mgr. ", None), ("Ludmilou Zkušební", True), (" ve věci", None)],
    [("žalobce: ", None), (COMPANY, True), (", IČO ", None), ("274 16 835", True), (",", None)],
    [("se sídlem ", None), ("tř. Kpt. Jaroše 1027/14a, 602 00 Brno", True), (",", None)],
    [("proti žalovanému: ", None), ("Bohumil Kratochvíl", True), (", nar. ", None), ("29. 2. 1984", True),
     (", bytem ", None), ("nám. Svobody 12,", True)],
    [("702 00 Ostrava", True), (", o zaplacení ", None), ("125 000 Kč", False), (" s příslušenstvím,", None)],
    [("č. j. ", None), ("12 C 345/2023-67", True)],
    [("takto:", None)],
    [("I. Žalovaný ", None), ("Bohumil Kratochvíl", True), (" je povinen zaplatit žalobci částku ", None),
     ("125 000 Kč", False)],
    [("do ", None), ("15 dnů", False), (" od právní moci tohoto usnesení.", None)],
    [("II. Žalobci ", None), (COMPANY, True)],
    [("se nepřiznává náhrada nákladů řízení ve výši ", None), ("4 800 Kč", False), (".", None)],
    [("Odůvodnění:", False)],
    [("Žalobou doručenou soudu dne ", None), ("3. 4. 2023", False), (" se žalobce domáhal zaplacení dluhu.", None)],
    [("S ", None), ("Bohumilem Kratochvílem", True), (" uzavřel smlouvu dne ", None), ("15. 1. 2022", False), (".", None)],
    [("Výzva byla ", None), ("Bohumilu Kratochvílovi", True), (" doručena dne ", None), ("20. 2. 2023", False), (".", None)],
    [("Svědkyně ", None), ("Ctislava Nedomová", True), (" potvrdila, že pan ", None), ("Kratochvíl", True),
     (" dluh uznal.", None)],
    [("Jednatel žalobce ", None), ("Radomil Zkoušený", True), (" předložil smlouvu a uvedl, že", None)],
    [("s ", None), ("Kratochvílem", True), (" jednal osobně.", None)],
    [("Kontakt: ", None), ("kratochvil@example.test", True), (", tel. ", None), ("+420 777 222 111", True),
     (", účet č. ", None), ("123456789/0800", True), (".", None)],
    [("V Testově dne ", None), ("13. 11. 2026", False)],
    [("Mgr. ", None), ("Ludmila Zkušební", True), (", samosoudkyně", None)],
]
# Words that betray the people and the company even when cut out of the full value.
CZ_FRAGMENTS = ["Kratochv", "Bohumil", "Severomoravsk", "Nedomov", "Ctislav", "Zkoušen", "Radomil", "Zkušební",
                "Ludmil", "Jaroše", "Svobody", "Ostrava", "274 16 835", "27416835", "345/2023", "1984",
                "kratochvil@", "777 222 111", "123456789"]
CZ_KEEP = ["USNESENÍ", "125 000 Kč", "4 800 Kč", "15 dnů", "3. 4. 2023", "15. 1. 2022", "20. 2. 2023",
           "13. 11. 2026", "Okresní soud v Testově", "Odůvodnění:"]


def czech_order_pdf():
    document = pdf.open()
    page = document.new_page()
    truth = Truth()
    y = 70
    for line in CZ_LINES:
        x = 56
        for text, secret in line:
            x = put(page, truth, x, y, text, secret, 10.5)
        y += 22
    return document, truth


def czech_order_docx(path):
    """Same order as a Word file; secret values are split across differently formatted runs."""
    document = Document()
    document.core_properties.author = "Bohumil Kratochvíl"
    document.core_properties.title = "Usnesení Kratochvíl"
    for number, line in enumerate(CZ_LINES):
        paragraph = document.add_heading(level=1) if number == 0 else document.add_paragraph()
        for text, secret in line:
            if secret and len(text) > 6:
                middle = len(text) // 2 + 1
                first = paragraph.add_run(text[:middle])
                first.bold = True
                second = paragraph.add_run(text[middle:])
                second.italic = True
                second.font.size = Pt(12)
            else:
                run = paragraph.add_run(text)
                if secret is False:
                    run.underline = True
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Žalovaný"
    table.cell(0, 1).text = "Bohumil Kratochvíl"
    table.cell(1, 0).text = "Částka"
    table.cell(1, 1).text = "125 000 Kč"
    document.sections[0].header.paragraphs[0].text = "Věc: Kratochvíl – zaplacení 125 000 Kč"
    document.sections[0].footer.paragraphs[0].text = "Doručuje se: Bohumil Kratochvíl, nám. Svobody 12, 702 00 Ostrava"
    document.save(path)
