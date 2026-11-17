"""Synthetic court file for end-to-end checks.

Everything here is invented: names, numbers, addresses and e-mail addresses
(example.test). ``build_spis`` writes one ZIP with many formats and returns
every personal value that was put into it, so a detector can look for them
in the released output.
"""
import datetime
import io
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anonymizer.leaks import Value  # noqa: E402
from test_processing import ProcessingTests  # noqa: E402

COURT = "Okresní soud v Kolíně"
CASE = "12 C 345/2026"
HEADER = f"{COURT}, sp. zn. {CASE}"
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf")
NOTICE = ("Poučení: Proti tomuto rozhodnutí je možno podat odvolání do patnácti dnů od jeho doručení ke "
          "Krajskému soudu v Praze prostřednictvím Okresního soudu v Kolíně. Odvolání musí směřovat proti "
          "všem výrokům, musí být odůvodněno a musí z něj být zřejmé, kdo je podává, které věci se týká a "
          "co sleduje. Nesplní-li povinný, co mu ukládá toto rozhodnutí, může se oprávněný domáhat výkonu rozhodnutí.")
JUDGE = "JUDr. Alena Rýdlová, soudkyně"
CLERK = "Božena Linhartová"
CERTIFIED = f"Shodu s prvopisem potvrzuje {CLERK}, vedoucí kanceláře."


@dataclass(frozen=True)
class Person:
    first: str
    last: str
    born: datetime.date
    street: str = ""
    city: str = ""
    phone: str = ""
    email: str = ""
    account: str = ""
    document: str = ""
    ico: str = ""
    female: bool = False
    common: bool = False

    @property
    def name(self):
        return f"{self.first} {self.last}"

    @property
    def address(self):
        return f"{self.street}, {self.city}"

    @property
    def rc(self):
        """Birth number with the 10-digit checksum (divisible by 11); invented, not a real person's."""
        serial = 100 + self.born.day * 7 + self.born.month
        while True:
            base = int(f"{self.born.year % 100:02d}{self.born.month + 50 * self.female:02d}{self.born.day:02d}{serial:03d}")
            check = -base * 10 % 11
            if check < 10:
                number = f"{base * 10 + check:010d}"
                return number[:6] + "/" + number[6:]
            serial += 1

    def date(self, style="short"):
        d, m, y = self.born.day, self.born.month, self.born.year
        months = ("ledna února března dubna května června července srpna září října listopadu prosince").split()
        return {"short": f"{d}. {m}. {y}", "padded": f"{d:02d}.{m:02d}.{y}", "iso": f"{y}-{m:02d}-{d:02d}",
                "long": f"{d}. {months[m - 1]} {y}"}[style]

    def values(self):
        found = [Value("JMENO", self.name), Value("PRIJMENI", self.last, self.common),
                 Value("KRESTNI", self.first, self.first in COMMON_FIRST), Value("NAROZENI", self.date("iso")),
                 Value("RC", self.rc)]
        for kind, text in (("ADRESA", self.address if self.street else ""), ("TELEFON", self.phone),
                           ("EMAIL", self.email), ("UCET", self.account), ("DOKLAD", self.document), ("ICO", self.ico)):
            if text:
                found.append(Value(kind, text))
        return found


COMMON_FIRST = {"Jan"}  # also an ordinary word elsewhere (sv. Jan); found only with a capital letter
D = datetime.date
PEOPLE = {
    "wolf": Person("Tadeáš", "Wolf", D(1985, 3, 14), "Dlouhá 15", "280 02 Kolín", "721 345 678",
                   "tadeas.wolf@example.test", "123456789/0800", "204516973"),
    "novak": Person("Jan", "Novák", D(1978, 11, 2), "Nádražní 8/3", "110 00 Praha 1", "733 218 540",
                    "jan.novak@example.test", "19-2000123456/0100", "118840325"),
    "novakova": Person("Marie", "Nováková", D(1981, 7, 30), "Zahradní 22", "282 01 Český Brod", "734 902 116",
                       "marie.novakova@example.test", "7766554433/2010", "306217748", female=True),
    "maly": Person("Bohumil", "Malý", D(1969, 5, 5), "Lipová 101", "284 01 Kutná Hora", "777 600 413",
                   "b.maly@example.test", "2800451239/2010", "117093562", common=True),
    "hlavacek": Person("Vojmír", "Hlaváček", D(1990, 9, 19), "Tyršova 4a", "280 02 Kolín", "725 187 364",
                       "vojmir.hlavacek@example.test", "1019283746/0300", "402963185", "25123891"),
    "ondrackova": Person("Radoslava", "Ondráčková", D(1975, 2, 2), "Havlíčkova 31", "281 63 Kostelec nad Černými lesy",
                         "733 456 789", "radoslava.ondrackova@example.test", "55667788/0600", "209385714", female=True),
    "pospisil": Person("Petr", "Pospíšil", D(1983, 12, 24), "U Nádraží 6", "290 01 Poděbrady", "702 551 903",
                       "petr.pospisil@example.test", "670100220/6210", "331472085"),
    "hajek": Person("Karel", "Hájek", D(1972, 4, 17), "Palackého 77/12", "280 00 Kolín", "774 220 861",
                    "karel.hajek@example.test", "35-1122334455/0800", "512640937", "60193531"),
    "kucerova": Person("Ilona", "Kučerová", D(1988, 8, 9), "Smetanova 9", "281 51 Velký Osek", "736 405 128",
                       "ilona.kucerova@example.test", "4400112233/0100", "227905461", female=True),
    "benes": Person("Zdeněk", "Beneš", D(1966, 1, 28), "Komenského 54", "280 02 Kolín", "723 819 047",
                    "zdenek.benes@example.test", "9988776655/0300", "148236507"),
}
# Court staff appear in every decision; only their names are personal data here.
STAFF = [Value("JMENO", "Alena Rýdlová"), Value("PRIJMENI", "Rýdlová"),
         Value("JMENO", "Božena Linhartová"), Value("PRIJMENI", "Linhartová")]


def font():
    for path in FONTS:
        if Path(path).is_file():
            return path
    raise RuntimeError("Chybí písmo s českou diakritikou (DejaVu Sans, Liberation Sans nebo Arial).")


# -- format builders -----------------------------------------------------------

def text_pdf(pages, header=HEADER):
    """Text PDF with a repeated header and a footer 'strana N z M' on every page."""
    face = font()
    with pymupdf.open() as document:
        for number, body in enumerate(pages, 1):
            page = document.new_page(width=450, height=400)
            page.insert_text((30, 26), header, fontsize=9, fontname="spis", fontfile=face)
            left = page.insert_textbox(pymupdf.Rect(30, 40, 420, 365), body, fontsize=10, fontname="spis", fontfile=face)
            assert left >= 0, "Strana je přeplněná."
            page.insert_text((30, 388), f"strana {number} z {len(pages)}", fontsize=9, fontname="spis", fontfile=face)
        document.set_metadata({"author": CLERK, "title": f"Spis {CASE}"})
        return document.tobytes()


def image_pixmaps(pages):
    """Render lines of text as greyscale 300 DPI images (no text layer)."""
    face = font()
    result = []
    for lines in pages:
        with pymupdf.open() as document:
            page = document.new_page(width=460, height=60 + 17 * len(lines))
            for index, line in enumerate(lines):
                page.insert_text((24, 26 + 17 * index), line, fontsize=12, fontname="spis", fontfile=face)
            result.append(page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY))
    return result


def scan_pdf(pages):
    with pymupdf.open() as document:
        for pixmap in image_pixmaps(pages):
            page = document.new_page(width=pixmap.width * 72 / 300, height=pixmap.height * 72 / 300)
            page.insert_image(page.rect, pixmap=pixmap)
        return document.tobytes()


def tiff(pages):
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "scan.tif"
        ProcessingTests.write_tiff(target, image_pixmaps(pages))
        return target.read_bytes()


def png(lines):
    return image_pixmaps([lines])[0].tobytes("png")


def docx(build):
    document = Document()
    build(document)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def with_frame(document, body):
    section = document.sections[0]
    section.header.paragraphs[0].text = HEADER
    section.footer.paragraphs[0].text = "strana 1"
    document.core_properties.author = CLERK
    document.core_properties.last_modified_by = CLERK
    for paragraph in body:
        document.add_paragraph(paragraph)


def doc97(paragraphs):
    class Shim:
        compound_file = staticmethod(ProcessingTests.compound_file)

    return ProcessingTests.word_file(Shim(), "\r".join(paragraphs) + "\r")


def rtf(paragraphs):
    def encode(text):
        return "".join(char if char.isascii() and char not in "\\{}" else
                       "\\'" + char.encode("cp1250").hex() if char.encode("cp1250", "ignore") else "?"
                       for char in text)

    body = "\\par\n".join(encode(paragraph) for paragraph in paragraphs)
    info = "{\\info{\\author " + encode(CLERK) + "}}"
    return ("{\\rtf1\\ansi\\ansicpg1250\\deff0{\\fonttbl{\\f0 Arial;}}" + info + "\\f0\\fs22\n" + body + "\\par}").encode("ascii")


def odt(paragraphs):
    names = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
             'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
             'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/"')
    content = (f'<office:document-content {names}><office:body><office:text>'
               + "".join(f"<text:p>{escape(paragraph)}</text:p>" for paragraph in paragraphs)
               + "</office:text></office:body></office:document-content>")
    meta = (f'<office:document-meta {names}><office:meta><dc:creator>{CLERK}</dc:creator>'
            f'<meta:generator>Test</meta:generator></office:meta></office:document-meta>')
    manifest = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                '</manifest:manifest>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text", zipfile.ZIP_STORED)
        archive.writestr("content.xml", content)
        archive.writestr("meta.xml", meta)
        archive.writestr("styles.xml", f'<office:document-styles {names}/>')
        archive.writestr("META-INF/manifest.xml", manifest)
    return stream.getvalue()


def xlsx(rows):
    strings = []
    sheet = []
    for number, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row):
            if value not in strings:
                strings.append(value)
            cells.append(f'<c r="{"ABCDEFG"[column]}{number}" t="s"><v>{strings.index(value)}</v></c>')
        sheet.append(f'<row r="{number}">{"".join(cells)}</row>')
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                               '<Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                               '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                               '</Types>',
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet name="Osoby" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/>'
                                      f'<Relationship Id="rId2" Type="{rel}/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{main}"><sheetData>{"".join(sheet)}</sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<sst xmlns="{main}">' + "".join(f"<si><t>{escape(value)}</t></si>" for value in strings) + "</sst>",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content)
    return stream.getvalue()


def mail(subject, sender, to, body, attachment=None, name="priloha.docx", html=None):
    message = EmailMessage()
    message["From"] = Address(sender.name, *sender.email.split("@"))
    message["To"] = Address(to.name, *to.email.split("@"))
    message["Subject"] = subject
    message["Date"] = "Tue, 16 Jun 2026 09:30:00 +0200"
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    if attachment:
        message.add_attachment(attachment, maintype="application", subtype="octet-stream", filename=name)
    return message.as_bytes()


# -- the case file -------------------------------------------------------------

def build_spis(target_zip, scan_pages=2):
    """Write the synthetic case file to ``target_zip`` and return all personal values put into it."""
    p = PEOPLE
    wolf, novak, novakova, maly, hlavacek, ondrackova, pospisil, hajek, kucerova, benes = (
        p[key] for key in ("wolf", "novak", "novakova", "maly", "hlavacek", "ondrackova", "pospisil", "hajek",
                           "kucerova", "benes"))
    files = {}

    def person_line(person, role, with_id=True):
        line = (f"{role} {person.name}, nar. {person.date()}, bytem {person.address}, tel. {person.phone}, "
                f"e-mail {person.email}")
        return line + (f", OP č.: {person.document}, č. ú. {person.account}." if with_id else ".")

    # 1. DOCX with a table, header, footer; the witness Beneš appears by surname only.
    def claim(document):
        with_frame(document, [
            "ŽALOBA o zaplacení částky 184 500 Kč s příslušenstvím",
            person_line(hlavacek, "Žalobce: pan", False) + f" IČO: {hlavacek.ico}, č. ú. {hlavacek.account}.",
            f"Žalovaný: {wolf.name}, rodné číslo {wolf.rc}, bytem {wolf.address}, OP č.: {wolf.document}.",
            f"Žalobce je zastoupen advokátem Mgr. {pospisil.name}, tel. +420 {pospisil.phone}, {pospisil.email}.",
            "Při předání vozidla byl přítomen svědek Beneš, který může potvrdit stav tachometru. "
            "Rozdíl mezi dohodnutou a skutečnou částkou nebyl malý rozdíl, ale celých 40 000 Kč.",
            "Účastníci řízení:"])
        table = document.add_table(rows=1, cols=4)
        for cell, title in zip(table.rows[0].cells, ("Jméno a příjmení", "Datum narození", "Telefon", "Poznámka")):
            cell.text = title
        for person, note in ((hlavacek, "žalobce"), (wolf, "žalovaný"), (novak, "svědek"), (novakova, "svědkyně")):
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text, cells[3].text = person.name, person.date("padded"), person.phone, note

    files["01_zahajeni/01_zalobni_navrh.docx"] = docx(claim)

    # 2. DOC (Word 97), including an upper-case name.
    files["01_zahajeni/02_prihlaska_pohledavky.doc"] = doc97([
        "PŘIHLÁŠKA POHLEDÁVKY",
        f"ŽALOBCE: {hajek.name.upper()}, nar. {hajek.date('padded')}, IČO: {hajek.ico}",
        f"Sídlo: {hajek.address}, telefon {hajek.phone}, e-mail {hajek.email}",
        f"Pohledávka vůči dlužníkovi {wolf.name} vznikla ze smlouvy o dílo. Hájek žádá úhradu na účet {hajek.account}.",
        f"Zástupce: Ing. {novak.first[0]}. {novak.last}, {novak.last} {novak.first[0]}. potvrdil převzetí."])

    # 3. DOCX interview record: Wolf by surname in several cases.
    def interview(document):
        with_frame(document, [
            "PROTOKOL O VÝSLECHU",
            f"Obžalovaný {wolf.name} mlčel. Wolf pak řekl, že s tím nemá nic společného. "
            f"Soud se Wolfa zeptal na noc z 12. na 13. března 2026 a Wolfovi byla přečtena předchozí výpověď.",
            f"Obhájce Wolfa upozornil, že s Wolfem nebyl výslech předem projednán. "
            f"Osobní údaje: nar. {wolf.date()}, rodné číslo {wolf.rc}, bytem {wolf.address}, "
            f"telefon {wolf.phone}, {wolf.email}.",
            f"Svědek {maly.name} potvrdil, že viděl Malého před domem, kde Malý poté odešel a svědek Malý nebyl zraněn.",
            f"Paní {novakova.name} vypověděla, že Novákovou ani jejího manžela J. Novák neznala. "
            f"Novák J. sdělil, že mluvil s Novákovou jen jednou.", NOTICE, JUDGE, CERTIFIED])

    files["02_protokoly/03_vyslech_Wolf.docx"] = docx(interview)

    # 4. Judgment as a text PDF: repeated header and footer, the notice, the signature.
    pages = [
        (f"ROZSUDEK\nJMÉNEM REPUBLIKY\n\nOkresní soud v Kolíně rozhodl samosoudkyní JUDr. Alenou Rýdlovou ve věci žalobce "
         f"{hlavacek.name}, nar. {hlavacek.date()}, bytem {hlavacek.address}, proti žalovanému {wolf.name}, "
         f"nar. {wolf.date('padded')}, bytem {wolf.address}, o zaplacení 184 500 Kč, takto:"),
        (f"Žalovaný je povinen zaplatit žalobci do tří dnů částku 184 500 Kč na účet {hlavacek.account}. "
         f"Odůvodnění: Žalobce tvrdil, že se Wolf zavázal předat vozidlo. Wolf to popřel. Svědek {hlavacek.name} "
         f"podle Wolfa nemluvil pravdu, soud však uvěřil svědku Benešovi a slyšel i Pospíšila."),
        NOTICE,
        (f"V Kolíně dne 17. 6. 2026\n{JUDGE}\n\n{CERTIFIED}\nKontakt na kancelář: {pospisil.phone}, "
         f"{wolf.email}. Rodné číslo žalovaného {wolf.rc}.")]
    files["02_protokoly/04_rozsudek_12C345-2026.pdf"] = text_pdf(pages)

    # 5. ODT: third decision with the same notice, married couple, the common-word surname.
    files["02_protokoly/05_platebni_rozkaz.odt"] = odt([
        HEADER, "PLATEBNÍ ROZKAZ",
        f"Žalobkyně {novakova.name}, nar. {novakova.date('long')}, bytem {novakova.address}, e-mail "
        f"{novakova.email}, tel. {novakova.phone}, rodné číslo {novakova.rc}.",
        f"Žalovaný {maly.name}, nar. {maly.date()}, bytem {maly.address}, OP č.: {maly.document}, "
        f"č. ú. {maly.account}, telefon {maly.phone}, {maly.email}.",
        "Podle svědka Malého byl rozdíl v ceně jen malý rozdíl a svědek Malý to potvrdil i podruhé.",
        NOTICE, JUDGE, CERTIFIED])

    # 6. RTF minutes: the full name of Beneš appears only here (earlier only the surname).
    files["02_protokoly/06_protokol_o_jednani.rtf"] = rtf([
        "PROTOKOL O JEDNÁNÍ", HEADER,
        f"Svědek {benes.name}, nar. {benes.date()}, bytem {benes.address}, tel. {benes.phone}, {benes.email}, "
        f"rodné číslo {benes.rc}, OP č.: {benes.document}.",
        f"Dále vypovídala paní {kucerova.name}, nar. {kucerova.date('padded')}, bytem {kucerova.address}, "
        f"telefon +420 {kucerova.phone}, e-mail {kucerova.email}.",
        f"Pospíšil J. a J. Novák předali soudu listiny. S Novákovou se soud nespojil. Účet {benes.account}.",
        f"{JUDGE}; {CERTIFIED}"])

    # 7. Scanned PDF (image only); one name is hyphenated at the end of a line.
    scan = [["Podání ze dne 10. 6. 2026",
             "Žalobkyně paní Radoslava Ondráč-",
             f"ková, nar. {ondrackova.date()}, bytem {ondrackova.street},",
             f"{ondrackova.city}, tel. {ondrackova.phone},",
             f"e-mail {ondrackova.email}, rodné číslo {ondrackova.rc}."],
            ["Doplnění podání ze dne 12. 6. 2026",
             f"Svědkyně {kucerova.name} bydlí na adrese {kucerova.address}.",
             f"Číslo účtu {ondrackova.account}, OP č.: {ondrackova.document}."]]
    files["02_protokoly/07_sken_podani_Ondrackova.pdf"] = scan_pdf(scan[:scan_pages])

    # 8. TIFF scan.
    files["02_protokoly/08_sken_potvrzeni.tif"] = tiff([
        ["Potvrzení o převzetí listin ze dne 15. 6. 2026",
         f"Převzal pan {hlavacek.name}, bytem {hlavacek.street}, telefon {hlavacek.phone}.",
         f"IČO: {hlavacek.ico}."],
        [f"Druhá strana: {hajek.name}, č. ú. {hajek.account}."]][:min(scan_pages, 2)])

    # 9. XLSX list of persons.
    rows = [("Jméno a příjmení", "Rodné číslo", "Datum narození", "Adresa", "Telefon a e-mail", "Doklady")]
    for person in PEOPLE.values():
        if person is not benes:
            ids = f"OP č.: {person.document}, č. ú. {person.account}" + (f", IČO: {person.ico}" if person.ico else "")
            rows.append((person.name, person.rc, person.date("short"), person.address,
                         f"{person.phone}; {person.email}", ids))
    files["03_seznamy/09_seznam_osob.xlsx"] = xlsx(rows)

    # 10./11. E-mail with an attachment and a quoted earlier message; the quoted message is also in the file alone.
    original = (f"Dobrý den,\nposílám podklady k věci {CASE}. Můj telefon je {kucerova.phone}.\n"
                f"S pozdravem\n{kucerova.name}\n")
    quoted = "\n".join("> " + line for line in original.splitlines())
    reply = (f"Děkuji, {kucerova.first}. Podklady předám panu {hlavacek.name}.\n\n"
             f"-----Původní zpráva-----\nOd: {kucerova.name} <{kucerova.email}>\nPředmět: Podklady\n\n{original}\n{quoted}\n")
    attachment = docx(lambda document: with_frame(document, [
        f"Plná moc: {hlavacek.name} zmocňuje {pospisil.name}, nar. {pospisil.date()}, bytem {pospisil.address}."]))
    files["04_posta/10_zprava_Pospisil.eml"] = mail("Re: Podklady k věci", pospisil, kucerova, reply, attachment,
                                                    "plna_moc_Pospisil.docx",
                                                    html=f"<p>Děkuji, <b>{kucerova.first}</b>.</p>")
    files["04_posta/11_puvodni_zprava.eml"] = mail("Podklady", kucerova, pospisil, original)

    # 12. PNG with text.
    files["05_foto/12_foto_vizitka_Kucerova.png"] = png([f"{kucerova.name}", f"tel. {kucerova.phone}",
                                                          f"e-mail {kucerova.email}"])

    # 13. Nested ZIP with two documents.
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("01_potvrzeni_Benes.docx", docx(lambda document: with_frame(document, [
            f"Potvrzuji, že {benes.name} převzal listiny. Beneš má telefon {benes.phone}."])))
        archive.writestr("02_poznamka.txt", f"Poznámka soudu: {benes.name}, {benes.email}, účet {benes.account}.\n")
    files["06_prilohy/13_prilohy_Benes.zip"] = nested.getvalue()

    # 14. The same attachment under two names.
    receipt = text_pdf([f"Potvrzení o zaplacení\nPlátce {hajek.name}, IČO: {hajek.ico}, účet {hajek.account}, "
                        f"tel. {hajek.phone}."])
    files["06_prilohy/14_potvrzeni_o_zaplaceni.pdf"] = receipt
    files["06_prilohy/14_potvrzeni_o_zaplaceni_kopie.pdf"] = receipt

    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return STAFF + [value for person in PEOPLE.values() for value in person.values()]
