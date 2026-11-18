"""Regression corpus: fonts, OCR, line breaks and mixed pages that decided real leaks on Windows.

Everything is synthetic (tests/synthetic_spis.py). The Windows simulation (tests/winsim.py) makes the PDF text
layer report U+00A0 and U+00AD as the Arial font does; Liberation Sans has the shapes of Arial. Leaks are
searched in the released output by the independent detector (anonymizer/leaks.py), including OCR of the output
images, not only in the OCR text the redaction itself saw.

Messages show the synthetic text through ascii(), so a failure can be diagnosed from a CI log.
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anonymizer.batch import process_folders  # noqa: E402
from anonymizer.leaks import layers, report, scan_texts, scan_tree  # noqa: E402
from anonymizer.reasons import reason_code  # noqa: E402
import synthetic_spis as spis  # noqa: E402
import winsim  # noqa: E402

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
needs_ocr = unittest.skipUnless(TESSDATA, "Chybí testovací jazyková data.")

P = spis.PEOPLE
ALL_VALUES = spis.STAFF + [value for person in P.values() for value in person.values()]
SOFT = object()  # a soft hyphen as the element w:softHyphen
NOBREAK = object()  # a no-break hyphen as the element w:noBreakHyphen


# -- builders ---------------------------------------------------------------------

def lines_pixmap(lines, face="liberation", size=12, color=(0, 0, 0), background=None, rotate=0, dpi=300, width=460):
    """Lines of text as a greyscale scan image (no text layer); ``rotate`` turns the writing by degrees."""
    step = size * 1.45
    height = int(40 + step * len(lines))
    with pymupdf.open() as document:
        page = document.new_page(width=width, height=height)
        if background:
            page.draw_rect(page.rect, color=None, fill=background)
        morph = (pymupdf.Point(width / 2, height / 2), pymupdf.Matrix(rotate)) if rotate else None
        for index, line in enumerate(lines):
            page.insert_text((24, 24 + step * (index + 1) * 0.9), line, fontsize=size, fontname="x",
                             fontfile=spis.font(face), color=color, morph=morph)
        return page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)


def scan_of(pixmaps):
    with pymupdf.open() as document:
        for pixmap in pixmaps:
            page = document.new_page(width=pixmap.width * 72 / pixmap.xres, height=pixmap.height * 72 / pixmap.yres)
            page.insert_image(page.rect, pixmap=pixmap)
        return document.tobytes()


def styled_pdf(pages):
    """Text PDF; every page is ``(body, face, size, color, background)``."""
    with pymupdf.open() as document:
        for number, (body, face, size, color, background) in enumerate(pages, 1):
            page = document.new_page(width=450, height=400)
            if background:
                page.draw_rect(page.rect, color=None, fill=background)
            path = spis.font(face)
            page.insert_text((30, 26), spis.HEADER, fontsize=9, fontname="x", fontfile=path, color=color)
            left = page.insert_textbox(pymupdf.Rect(30, 40, 420, 365), body, fontsize=size, fontname="x",
                                       fontfile=path, color=color)
            assert left >= 0, "Strana je přeplněná."
            page.insert_text((30, 388), f"strana {number} z {len(pages)}", fontsize=9, fontname="x", fontfile=path,
                             color=color)
        return document.tobytes()


def person_text(person, role="Svědek"):
    return (f"{role} {person.name}, nar. {person.date()}, bytem {person.address}, tel. {person.phone}, "
            f"e-mail {person.email}, rodné číslo {person.rc}, č. ú. {person.account}.")


def person_lines(person, role="Svědek"):
    return [f"{role} {person.name}, nar. {person.date()},", f"bytem {person.address},",
            f"tel. {person.phone}, e-mail {person.email},", f"rodné číslo {person.rc}, č. ú. {person.account}."]


def wrapped_lines(hyphen):
    """Values broken over lines the way a typesetter does; one value per break."""
    o, h, k, a, n, b = (P[key] for key in ("ondrackova", "hlavacek", "kucerova", "hajek", "novak", "benes"))
    local = lambda person: person.email.split("@")[0]  # noqa: E731
    domain = lambda person: person.email.split("@")[1]  # noqa: E731
    return [
        f"Žalobkyně paní {o.first} {o.last[:6]}{hyphen}",
        f"{o.last[6:]}, bytem {o.street},",
        f"{o.city}, tel. {o.phone[:7]}",
        f"{o.phone[8:]}, e-mail {local(h)}@",
        f"{domain(h)}, rodné číslo {k.rc[:7]}",
        f"{k.rc[7:]}, č. ú. {a.account[:3]}",
        f"{a.account[3:]}, e-mail {local(n)}",
        f"@{domain(n)}, svědek {b.first} {b.last[:3]}{hyphen}",
        f"{b.last[3:]}, nar. {b.date()}.",
    ]


def add_parts(paragraph, parts):
    for part in parts:
        if part in (SOFT, NOBREAK):
            run = paragraph.add_run()
            run._r.append(OxmlElement("w:softHyphen" if part is SOFT else "w:noBreakHyphen"))
        else:
            paragraph.add_run(part)  # "\n" becomes w:br


def wrapped_docx():
    """DOCX with soft hyphens (character and element), no-break spaces and manual line breaks."""
    o, h, k, a, n, b, p, m = (P[key] for key in ("ondrackova", "hlavacek", "kucerova", "hajek", "novak", "benes",
                                                 "pospisil", "maly"))
    local = lambda person: person.email.split("@")[0]  # noqa: E731
    domain = lambda person: person.email.split("@")[1]  # noqa: E731
    rows = [
        ["Žalobkyně paní Radoslava Ondráč", SOFT, "ková, nar. 2. 2. 1975."],
        ["Žalobkyně paní Radoslava Ondráč\xad", "\n", "ková."],
        ["Žalobkyně paní Radoslava Ondráč-", "\n", "ková."],
        ["Žalobkyně paní Radoslava Ondráč", SOFT, "\n", "ková."],
        [f"Svědek {n.first}\xa0{n.last}, telefon {n.phone.replace(' ', chr(160))}."],
        [f"Svědek {b.name}, telefon {b.phone[:7]}", "\n", f"{b.phone[8:]}."],
        [f"Rodné číslo {k.rc[:7]}", "\n", f"{k.rc[7:]}."],
        [f"Rodné číslo {h.rc[:6]}\xad{h.rc[7:]} a {m.rc[:6]}\xa0{m.rc[7:]}."],
        [f"E-mail {local(h)}@", "\n", f"{domain(h)}."],
        [f"E-mail {local(p)}", "\n", f"@{domain(p)}."],
        [f"Bytem {a.street},", "\n", f"{a.city}."],
        [f"Bytem {k.street.replace(' ', chr(160))}, {k.city.replace(' ', chr(160))}."],
        [f"Účet {a.account[:3]}", "\n", f"{a.account[3:]}, jiný účet {p.account[:10]}", "\n", f"{p.account[10:]}."],
        ["Účet 35", NOBREAK, f"{a.account[3:]}."],
    ]
    document = Document()
    for parts in rows:
        add_parts(document.add_paragraph(), parts)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


# -- the whole path ---------------------------------------------------------------

class PathTests(unittest.TestCase):
    """Base class: files go through ``process_folders`` (copies and Markdown) and the output is searched."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def execute(self, files, simulate=False, zipped=False, values=ALL_VALUES, expect_released=True):
        """Process ``files`` ({name: bytes}); return (results, Markdown text, output folder)."""
        source, target = self.root / "vstup", self.root / "vystup"
        source.mkdir()
        target.mkdir()
        if zipped:
            with zipfile.ZipFile(source / "spis.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        else:
            for name, data in files.items():
                (source / name).write_bytes(data)
        guard = winsim.windows_text_layer() if simulate else contextlib.nullcontext()
        with guard:
            results = list(process_folders(source, target, TESSDATA, markdown=True))
            counted = scan_tree(target, values, TESSDATA)
            details = self.details(target, values) if sum(counted.values()) else ""
        markdown = next((path for _, path, _ in results if path is not None and path.suffix == ".md"), None)
        refused = [(Path(str(src)).name if src else "markdown", reason_code(reason))
                   for src, path, reason in results if path is None]
        self.assertIsNotNone(markdown, f"Markdown nebyl vydán: {refused}")
        if expect_released:
            self.assertEqual(refused, [], "Některý vstup nebyl vydán.")
        self.assertEqual(sum(counted.values()), 0, f"{report(counted)}\n{details}")
        return results, markdown.read_text(encoding="utf-8"), target

    def details(self, target, values):
        """Which output files leak and what the detector saw in them (synthetic text only)."""
        lines = []
        for path in sorted(target.rglob("*")):
            if path.is_file():
                texts = list(layers(path, TESSDATA))
                counted = scan_texts(texts, values)
                if sum(counted.values()):
                    lines.append(f"{path.name}: {dict(counted)} {ascii(' | '.join(texts))[:2500]}")
        return "\n".join(lines)

    def assert_gone(self, markdown, *texts):
        squeezed = "".join(markdown.split())
        for text in texts:
            self.assertNotIn("".join(text.split()), squeezed, f"V Markdownu zůstalo {ascii(text)}.")


class WindowsSimulationTests(unittest.TestCase):
    def test_text_layer_uses_no_break_space_and_soft_hyphen(self):
        data = spis.text_pdf(["Jan Novák, rodné číslo 850314-2010.\nDruhý řádek."], face="liberation")
        with pymupdf.open(stream=data) as document:
            page = document[0]
            # On Windows (Arial) the real layer may already report U+00A0/U+00AD; compare drawn text.
            before = page.get_text().replace("\xa0", " ").replace("\xad", "-")
            native_markup = page.get_text("html")
            with winsim.windows_text_layer():
                text = page.get_text()
                blocks = page.get_text("blocks")
                words = page.get_text("words")
                raw = page.get_text("rawdict")
                listed = page.get_text("text", sort=True)
                markup = page.get_text("html")
            self.assertEqual(page.get_text().replace("\xa0", " ").replace("\xad", "-"), before,
                             "Simulace se po bloku nevrátila.")
        self.assertIn("Jan\xa0Novák,\xa0rodné\xa0číslo\xa0850314\xad2010.", text)
        self.assertEqual(text.replace("\xa0", " ").replace("\xad", "-"), before)
        self.assertTrue(all(" " not in block[4] and "-" not in block[4] for block in blocks if block[6] == 0))
        self.assertIn("850314\xad2010.", [word[4] for word in words])
        letters = [char["c"] for block in raw["blocks"] for line in block.get("lines", ()) for span in line["spans"]
                   for char in span["chars"]]
        self.assertIn("\xa0", letters)
        self.assertIn("\xad", letters)
        self.assertNotIn(" ", letters)
        self.assertNotIn("-", letters)
        fonts = {span["font"] for block in raw["blocks"] for line in block.get("lines", ()) for span in line["spans"]}
        self.assertTrue(all("\xa0" not in font and "\xad" not in font for font in fonts))
        self.assertIn("\xa0", listed)
        self.assertEqual(markup, native_markup)  # markup keeps its own syntax

    def test_convert_handles_every_container(self):
        self.assertEqual(winsim.convert("a b-c"), "a\xa0b\xadc")
        self.assertEqual(winsim.convert(["a b", ("c d", 1.5)]), ["a\xa0b", ("c\xa0d", 1.5)])
        self.assertEqual(winsim.convert({"c": "-", "font": "A-B", "chars": [{"c": " "}], "text": "x y"}),
                         {"c": "\xad", "font": "A-B", "chars": [{"c": "\xa0"}], "text": "x\xa0y"})

    def test_font_choice(self):
        regular = spis.font("liberation")
        self.assertTrue(Path(regular).is_file())
        with spis.use_font("serif"):
            serif = spis.font()
            self.assertNotEqual(serif, regular)
            self.assertEqual(spis.font("liberation"), regular)
        self.assertNotEqual(spis.font(), serif)
        for face in spis.FACES:
            try:
                path = spis.font(face)
            except RuntimeError:
                continue  # not every face is installed on every system (e.g. Windows CI)
            self.assertTrue(Path(path).is_file(), f"Písmo {face} chybí.")


@needs_ocr
class WindowsPathTests(PathTests):
    def test_whole_case_file_with_arial_like_font_and_windows_text_layer(self):
        archive = self.root / "spis.zip"
        with spis.use_font("liberation"):
            values = spis.build_spis(archive, scan_pages=1)
        results, markdown, _ = self.execute({"spis.zip": archive.read_bytes()}, simulate=True, values=values)
        self.assertTrue(any(path is not None and path.suffix == ".zip" for _, path, _ in results),
                        "Kontrolní ZIP nebyl vydán.")
        for person in ("wolf", "hlavacek", "novak"):
            self.assert_gone(markdown, P[person].phone, P[person].email, P[person].rc)


@needs_ocr
class HardFontTests(PathTests):
    """Serif, italic, bold, small, large and grey writing, and a rotated scan."""

    def scans(self):
        body = person_lines(P["kucerova"])
        grey = dict(color=(0.45, 0.45, 0.45), background=(0.93, 0.93, 0.88))
        variants = {
            "serif": dict(face="serif"),
            "serif_kurziva": dict(face="serif-italic"),
            "serif_tucne": dict(face="serif-bold"),
            "male_7": dict(face="liberation", size=7),
            "male_serif_8": dict(face="serif", size=8),
            "velke_18": dict(face="liberation", size=18, width=900),
            "sede_na_svetlem": dict(face="liberation", **grey),
        }
        return {f"{name}.pdf": scan_of([lines_pixmap(body, **options)]) for name, options in variants.items()}

    def test_scans_in_difficult_fonts(self):
        self.execute(self.scans(), simulate=True)

    def test_text_pdf_in_difficult_fonts(self):
        persons = ("hlavacek", "wolf", "novak", "maly", "pospisil", "benes")
        grey = (0.45, 0.45, 0.45), (0.93, 0.93, 0.88)
        specs = [("serif", 10, (0, 0, 0), None), ("serif-italic", 10, (0, 0, 0), None),
                 ("serif-bold", 10, (0, 0, 0), None), ("liberation", 7, (0, 0, 0), None),
                 ("liberation", 18, (0, 0, 0), None), ("liberation", 10, *grey)]
        pages = [(person_text(P[key]), *spec) for key, spec in zip(persons, specs)]
        results, markdown, _ = self.execute({"pismo.pdf": styled_pdf(pages)}, simulate=True)
        self.assert_gone(markdown, *(P[key].name for key in persons), *(P[key].email for key in persons))

    def test_rotated_scans(self):
        """A slightly rotated scan is read, or the page is blacked out as a whole; nothing may be left."""
        body = person_lines(P["ondrackova"])
        files = {f"otoceny_{name}.pdf": scan_of([lines_pixmap(body, face=face, rotate=angle)])
                 for name, face, angle in (("3", "liberation", 3), ("zaporny_4", "serif", -4), ("7", "liberation", 7))}
        self.execute(files, simulate=True, expect_released=False)


@needs_ocr
class LineBreakTests(PathTests):
    def test_text_pdf_wrapped_values(self):
        pages = ["\n".join(wrapped_lines("-")), "\n".join(wrapped_lines("\xad"))]
        results, markdown, _ = self.execute({"zalomeni.pdf": spis.text_pdf(pages, face="liberation")}, simulate=True)
        self.assert_gone(markdown, P["ondrackova"].phone, P["hlavacek"].email, P["kucerova"].rc, P["hajek"].account,
                         P["novak"].email, P["ondrackova"].name)

    def test_scan_wrapped_values(self):
        data = scan_of([lines_pixmap(wrapped_lines("-"), face="liberation", size=11)])
        self.execute({"zalomeni_sken.pdf": data}, simulate=True)

    def test_docx_wrapped_values(self):
        results, markdown, _ = self.execute({"zalomeni.docx": wrapped_docx()})
        self.assert_gone(markdown, P["ondrackova"].phone, P["hlavacek"].email, P["kucerova"].rc, P["hajek"].account,
                         P["novak"].phone, P["pospisil"].email, P["benes"].phone)


@needs_ocr
class MixedAndCourtTemplateTests(PathTests):
    def test_pdf_alternating_text_and_scanned_pages(self):
        wolf, novak, hajek, benes = P["wolf"], P["novak"], P["hajek"], P["benes"]
        face = spis.font("liberation")
        with pymupdf.open() as document:
            for person, scanned in ((wolf, False), (novak, True), (hajek, False), (benes, True)):
                if scanned:
                    pixmap = lines_pixmap([f"Doplnění podání, strana {len(document) + 1}", *person_lines(person)])
                    page = document.new_page(width=pixmap.width * 72 / 300, height=pixmap.height * 72 / 300)
                    page.insert_image(page.rect, pixmap=pixmap)
                else:
                    page = document.new_page(width=450, height=400)
                    page.insert_textbox(pymupdf.Rect(30, 40, 420, 365), person_text(person), fontsize=10,
                                        fontname="x", fontfile=face)
            data = document.tobytes()
        results, markdown, _ = self.execute({"stridani.pdf": data}, simulate=True)
        self.assert_gone(markdown, *(P[key].name for key in ("wolf", "novak", "hajek", "benes")))

    def test_text_page_with_a_scanned_stamp_and_signature(self):
        wolf, clerk = P["wolf"], spis.CLERK
        face = spis.font("liberation")
        stamp = lines_pixmap([spis.COURT, f"Podpis: {wolf.name}", f"Za soud: {clerk}"], size=9, dpi=250, width=260)
        with pymupdf.open() as document:
            page = document.new_page(width=450, height=400)
            page.insert_textbox(pymupdf.Rect(30, 40, 420, 200), f"Smlouva uzavřená dne 3. 3. 2026 mezi stranami. "
                                f"Kontaktní telefon {wolf.phone}.", fontsize=10, fontname="x", fontfile=face)
            page.insert_image(pymupdf.Rect(150, 250, 150 + stamp.width * 72 / 250, 250 + stamp.height * 72 / 250),
                              pixmap=stamp)
            data = document.tobytes()
        self.execute({"razitko.pdf": data}, simulate=True)

    def test_court_template_with_names_in_header_footer_and_certification(self):
        wolf, hlavacek = P["wolf"], P["hlavacek"]
        pages = [f"ROZSUDEK\nJMÉNEM REPUBLIKY\n\nOkresní soud v Kolíně rozhodl ve věci žalobce {hlavacek.name}, "
                 f"nar. {hlavacek.date()}, proti žalovanému {wolf.name}, nar. {wolf.date('padded')}.\n"
                 f"Srov. rozsudek Nejvyššího soudu sp. zn. 25 Cdo 1234/2019.",
                 spis.NOTICE,
                 f"V Kolíně dne 17. 6. 2026\n{spis.JUDGE}\n\n{spis.CERTIFIED}"]
        header = f"{spis.HEADER}, účastník řízení {wolf.name}"
        with pymupdf.open() as document:
            for number, body in enumerate(pages, 1):
                page = document.new_page(width=450, height=400)
                face = spis.font("liberation")
                page.insert_text((30, 26), header, fontsize=9, fontname="x", fontfile=face)
                page.insert_textbox(pymupdf.Rect(30, 40, 420, 365), body, fontsize=10, fontname="x", fontfile=face)
                page.insert_text((30, 388), f"{hlavacek.name} / {wolf.name}, strana {number}", fontsize=9, fontname="x",
                                 fontfile=face)
            data = document.tobytes()
        results, markdown, _ = self.execute({"sablona.pdf": data, "sablona_sken.pdf": scan_of([lines_pixmap(
            [header, f"Žalovaný {wolf.name}", spis.CERTIFIED, f"strana 1, {hlavacek.name}"], size=10, width=520)])},
            simulate=True)
        self.assert_gone(markdown, wolf.name, hlavacek.name, spis.CLERK, "Alena Rýdlová")
        self.assertIn("25 Cdo 1234/2019", markdown, "Citace judikatury zmizela.")
        self.assertIn("Poučení", markdown)


if __name__ == "__main__":
    unittest.main()
