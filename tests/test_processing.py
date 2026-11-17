import hashlib
import io
import os
import re
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

from anonymizer.batch import process_batch, process_folders
from anonymizer.documents import ocr_path, recognize_image
from anonymizer import czech, rules as rules_module
from anonymizer.rules import Rules


def synthetic_file(paragraphs=1000):
    """Deterministic synthetic text that mixes every kind of personal data with filler."""
    names = ["Jan Novák", "Petra Svobodová", "Josef Dvořák", "Karolína Bílá", "Zdeněk Veselý",
             "Marie Černá", "Hynek Kučera", "ZKUŠEBNÍ Pavel Horák"]
    streets = ["Dlouhá 12", "Na Příkopě 7/3", "Školní 128", "U Lesa 5a"]
    cities = ["Praha 1", "Brno", "Nový Bydžov", "Ústí nad Labem"]
    forms = ["a.s.", "a. s.", "s.r.o.", "s. r. o.", "spol. s r.o.", "A.S."]
    firms = ["Zkušební Leasing", "Alfa & Beta", "Stavby Morava", "Hutní závody"]
    filler = ["Soud dále konstatoval, že dokazování bylo provedeno řádně a v souladu se zákonem.",
              "Podle § 12 odst. 3 zákona č. 99/1963 Sb. je třeba postupovat dle zvláštních předpisů.",
              "Odvolání bylo podáno včas a obsahuje všechny náležitosti.", "Částka 12 345 Kč byla uhrazena.",
              "Na jednání dne 3. května 2021 v Praze se dostavili všichni účastníci. Zkušební Leasing ve věci rozhodl."]
    state = 12345
    out = []
    for _ in range(paragraphs):
        state = (state * 1103515245 + 12345) % 2 ** 31
        pick = state >> 8
        name, street, city = names[pick % 8], streets[pick // 8 % 4], cities[pick // 32 % 4]
        firm, form = firms[pick // 128 % 4], forms[pick // 512 % 6]
        day, month, year = pick % 28 + 1, pick % 12 + 1, 1950 + pick % 60
        kind = pick % 14
        if kind == 0:
            line = f"Žalobce {name}, nar. {day}. {month}. {year}, bytem {street}, 11{pick % 10} 00 {city}, podal žalobu."
        elif kind == 1:
            line = f"Žalovaná společnost {firm} {form}, IČO {10000000 + pick % 8999999}, se sídlem {street}, {city}."
        elif kind == 2:
            line = f"Kontakt: tel. +420 777 {pick % 900 + 100} {pick % 900 + 100}, e-mail osoba{pick % 50}@example.test."
        elif kind == 3:
            line = (f"Ve věci sp. zn. {pick % 90 + 1} C {pick % 900 + 1}/20{pick % 20 + 10} rozhodl soud. "
                    f"Srov. rozsudek sp. zn. 21 Cdo {pick % 9000 + 1}/2019.")
        elif kind == 4:
            line = f"Rodné číslo: {pick % 90 + 10}0101/{pick % 9000 + 1000}\nJméno: {name}\nTelefon: 602 {pick % 900 + 100} 222\n"
        elif kind == 5:
            line = f"Rozsudek převzal JUDr. K. Bílá a také {name.split()[-1]} J. dne {day}. {month}. {year}."
        elif kind == 6:
            line = (f"Platba na účet {pick % 900 + 100}-1234567890/0100 proběhla; IBAN CZ65 0800 0000 1920 0014 5399, "
                    f"doklad: OP: AB{pick % 900000 + 100000}, DIČ CZ{10000000 + pick % 8999999}, "
                    f"rodné číslo {pick % 90 + 10}0101/{pick % 9000 + 1000} uvedeno.")
        elif kind == 7:
            line = f"meno: {name}  dátum narodenia: {day}. {month}. {year}\n"
        elif kind == 8:
            line = f"Svědek {name} uvedl, že {firm}\t{form},\nsídlem {street}, {city}, neplnila povinnosti."
        else:
            line = filler[pick % 5]
        out.append(line)
    return "\n".join(out) + "\n"


def token_strings(count):
    """Deterministic strings of fragments that probe the boundaries of every pattern."""
    tokens = ["a.", "s.", "r.", "o.", "a. s.", "s. r. o.", "spol. s r. o.", "spol.", "A.S.", "S.R.O.", "ſ. r. o.",
              ",", " ", "  ", "\t", "\n", "\r\n", ";", ")", "(", "A", "Firma", "Alfa-Beta", "x", "@", "+", ".", "-",
              "_", "1", "7", "123456", "123 456", "/", "12/", "J.", "K.", "J. K.", "Novák", "NOVÁK", "Nov-ák", "ſ",
              "\u212a", "\u0130", "\u0131", "é", ":", ": ", "IČO", "ič", "CZ", "cz", "CZ1234567890", "nar", "NAR.",
              "dat", "DAT.", "datum narození", "bytem", "Bydlí", "sídlem", "trvalé bytem", "adresa", "účet", "účtu",
              "č. ú.", "č.", "ú.", "bankovní spojení", "ban", "tel", "doklad", "OP", "pas", "AB123456", "dne",
              "1. 2. 1980", "1. ledna 1980", "12345678", "+420", "777", "800101/1234", "a@b.cz", "x@y",
              "user.name+tag@sub.example.cz", "Jméno", "Telefon", "Rodné číslo", "meno", "priezvisko", "\u00a0",
              "\u2028", "Ⅰ", "Ⓐ", "JUDr.", "ZKUŠEBNÍ LEASING", "CZ12 3456 7890 1234 5678 9012"]
    state = 987654321
    for _ in range(count):
        state = (state * 1103515245 + 12345) % 2 ** 31
        length = state % 40 + 1
        parts = []
        for _ in range(length):
            state = (state * 1103515245 + 12345) % 2 ** 31
            parts.append(tokens[(state >> 8) % len(tokens)])
        if state % 7 == 0:
            # Company names around the 100 character reach of the pattern.
            state = (state * 1103515245 + 12345) % 2 ** 31
            body = "x" * (85 + state % 30)
            parts.append(f"\n{body}{' ' * ((state >> 8) % 5)} a. s.{',' if state % 2 else ''}")
        yield "".join(parts)


class ProcessingTests(unittest.TestCase):
    def test_company_and_grouped_identifier(self):
        output, _ = Rules().replace("Oprávněná: ZKUŠEBNÍ LEASING a.s., IČO 123 45 678")
        self.assertEqual(output, "Oprávněná: [ORGANIZACE-1], IČO [ICO-1]")

    def test_slovak_register_pdf_is_withheld(self):
        source = self.root / "register.pdf"
        with pdf.open() as document:
            document.new_page().insert_text((60, 90), "Register trestov - synteticky formular")
            document.save(source)
        _, target, error = self.run_batch(source)[0]
        self.assertIsNone(target)
        self.assertIn("nebyl vydán", error)

    def test_selected_batch_only_processes_ready_files(self):
        source, output = self.root / "input", self.root / "output"
        source.mkdir()
        output.mkdir()
        ready = source / "ready.txt"
        ready.write_text(self.email, encoding="utf-8")
        (source / "copying.txt").write_text(self.email, encoding="utf-8")
        results = list(process_folders(source, output, selected=[ready.resolve()]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1].read_text(encoding="utf-8"), "[EMAIL-1]")

    def test_mixed_structured_formats_and_renamed_zip(self):
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        for name, payload in {
            "plain.txt": "Kontakt osoba@example.test",
            "table.csv": "Kontakt;Poznámka\nosoba@example.test;hodnota\n",
            "table.tsv": "Kontakt\tPoznámka\nosoba@example.test\thodnota\n",
            "data.json": '{"contact":"osoba@example.test"}',
            "data.xml": '<contact value="osoba@example.test"/>',
        }.items():
            (source / name).write_text(payload, encoding="utf-8")
        with ZipFile(source / "archive.bin", "w") as archive:
            archive.writestr("osoba@example.test.txt", "osoba@example.test")
        with ZipFile(source / "bad.zip", "w") as archive:
            archive.writestr("unknown.bin", b"unknown")
        results = list(process_folders(source, target, audit_root=self.root / "audit"))
        self.assertEqual(len(results), 7)
        for original, output, reason in results:
            if original.name == "bad.zip":
                self.assertIsNone(output)
                continue
            self.assertIsNotNone(output, reason)
            if output.suffix == ".zip":
                with ZipFile(output) as archive:
                    self.assertEqual(archive.namelist(), ["polozka-0001.txt"])
                    self.assertEqual(archive.read("polozka-0001.txt"), b"[EMAIL-1]")
            else:
                self.assertIn(b"[EMAIL-1]", output.read_bytes())
                self.assertNotIn(b"osoba@example.test", output.read_bytes())

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.email = "osoba@example.test"

    def make_pdf(self, name, scanned=False, rotation=0):
        source = self.root / name
        with pdf.open() as doc, pdf.open() as scan:
            page = doc.new_page()
            page.insert_text((72, 90), self.email, fontsize=22)
            page.set_rotation(rotation)
            if scanned:
                sheet = scan.new_page()
                sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=240))
                scan.save(source)
            else:
                doc.set_metadata({"author": "NEPRENOSIT"})
                doc.embfile_add("test.txt", b"NEPRENOSIT")
                doc.save(source)
        return source

    def run_batch(self, *paths, **kwargs):
        return list(process_batch(paths, self.root, **kwargs))

    def check_image(self, target):
        with pdf.open(target) as result:
            page = result[0]
            self.assertEqual(page.get_text(), "")
            self.assertFalse(result.embfile_count())
            self.assertIsNone(page.first_annot)
            self.assertNotIn("NEPRENOSIT", str(result.metadata))
            image = pdf.Pixmap(result, page.get_images()[0][0])
            self.assertGreater(image.color_count(colors=True).get(b"\0\0\0", 0), 10_000)
            return image

    def test_docx_rebuild_and_consistent_aliases(self):
        doc = Document()
        paragraph = doc.add_paragraph("Kontakt ")
        paragraph.add_run("osoba@")
        paragraph.add_run("example.test")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "Testovací Osoba"
        doc.core_properties.author = "NEPRENOSIT"
        doc.sections[0].header.paragraphs[0].text = "Testovací Osoby"
        first = self.root / "01.docx"
        doc.save(first)
        doc = Document()
        doc.add_paragraph("Testovací Osoby, " + self.email)
        second = self.root / "02.docx"
        doc.save(second)
        results = self.run_batch(second, first, alias_lines=["Testovací Osoba | Testovací Osoby"])
        for _, target, reason in results:
            self.assertIsNotNone(target, reason)
            text = "\n".join(p.text for p in Document(target).paragraphs)
            self.assertIn("[EMAIL-1]", text)
            self.assertIn("[VLASTNI-1]", text)
            with ZipFile(target) as archive:
                for entry in archive.namelist():
                    self.assertNotIn(b"NEPRENOSIT", archive.read(entry))
        self.assertIn(self.email, "".join(p.text for p in Document(second).paragraphs))

    def test_pdf_redacts_pixels_and_drops_hidden_data(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source = self.make_pdf(f"rot{rotation}.pdf", rotation=rotation)
                _, target, reason = self.run_batch(source)[0]
                self.assertIsNotNone(target, reason)
                self.check_image(target)

    def test_ocr_required_and_missing(self):
        source = self.make_pdf("scan.pdf", scanned=True)
        _, target, reason = self.run_batch(source)[0]
        self.assertIsNone(target)
        self.assertIn("ces.traineddata", reason)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_ocr_redacts_scan(self):
        source = self.make_pdf("scan.pdf", scanned=True)
        _, target, reason = self.run_batch(source, tessdata=os.environ["SPISREDAKT_TEST_TESSDATA"])[0]
        self.assertIsNotNone(target, reason)
        image = self.check_image(target)
        with pdf.open("pdf", image.pdfocr_tobytes(language="ces", tessdata=ocr_path(os.environ["SPISREDAKT_TEST_TESSDATA"]))) as checked:
            self.assertNotIn(self.email, checked[0].get_text())

    def test_rules_merge_overlaps_and_normalize_phone(self):
        rules = Rules()
        self.assertEqual(rules.replace("+420 777 222 111 a 777222111")[0],
                         "[TELEFON-1] a [TELEFON-1]")
        self.assertEqual(rules.replace("123456/9999")[0], "[RC-1]")
        self.assertEqual(rules.replace("IČO: 12345678")[0], "IČO: [ICO-1]")

    def test_alias_conflict(self):
        with self.assertRaises(ValueError):
            Rules(["Test A | Test B", "Test C | Test B"])

    def test_czech_legal_context(self):
        rules = Rules()
        source = ("Ke sp. zn.: Nd 321/2026. V Táboře dne 13. 11. 2026. "
                  "Po Františku Hradilovi, nar. dne 12. února 1960, "
                  "posledně bytem Jabloňová 1027, Velké Lhotky, který dne 6. 4. 2020 zemřel. "
                  "Jednání 7. 5. 2026 u JUDr. Petrem Novákem – Tábor, J. Vrby 4.")
        output, count = rules.replace(source)
        self.assertEqual(count, 6)
        for value in ("Hradilovi", "Novákem", "12. února 1960", "Jabloňová", "Velké Lhotky",
                      "J. Vrby", "321/2026", "[UCET-"):
            self.assertNotIn(value, output)
        for value in ("13. 11. 2026", "6. 4. 2020", "7. 5. 2026", "[SPIS-", "[NAROZENI-"):
            self.assertIn(value, output)
        self.assertEqual(rules.replace("František Hradil a Petr Novák")[0], "[OSOBA-2] a [OSOBA-1]")
        self.assertEqual(rules.replace("Datum narození: 12.2.1960")[0], "Datum narození: [NAROZENI-1]")
        self.assertEqual(rules.replace("Účet: 12345678/0800")[0], "Účet: [UCET-1]")

    def test_folders_keep_originals_and_share_labels(self):
        source = self.root / "input"
        destination = self.root / "output"
        source.mkdir()
        destination.mkdir()
        (source / "nested").mkdir()
        (source / "ignore.bin").write_text("ignored")
        (source / "~$lock.docx").write_text("ignored")
        originals = {}
        for path, line in ((source / "a.docx", "Anna Hradilová, bytem Tábor, J. Vrby 4."),
                           (source / "nested" / "b.docx", "Podepsáno Annou Hradilovou.")):
            doc = Document()
            doc.add_paragraph(line)
            doc.save(path)
            originals[path] = path.read_bytes()
        results = list(process_folders(source, destination))
        self.assertEqual(len(results), 2)
        for original, target, reason in results:
            self.assertIsNotNone(target, reason)
            relative = original.relative_to(source.resolve())
            self.assertEqual(target, destination.resolve() / relative.with_stem(relative.stem + "_anonymizovano"))
            self.assertEqual(original.read_bytes(), originals[source / relative])
            self.assertIn("[OSOBA-1]", Document(target).paragraphs[0].text)
        import json
        audit_root = self.root / "audit"
        repeated = list(process_folders(source, destination, audit_root=audit_root))
        for _, target, reason in repeated:
            self.assertIsNotNone(target, reason)
            self.assertTrue(target.name.endswith("_anonymizovano_2.docx"))
        log = next(audit_root.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("a.docx", log)
        self.assertNotIn("Hradil", log)
        events = [json.loads(line) for line in log.splitlines()]
        self.assertEqual(events[-1]["counts"], {"released": 2, "skipped": 1})
        self.assertEqual(sum(event.get("fallback") == "numbered_filename" for event in events), 2)
        with self.assertRaises(ValueError):
            list(process_folders(source, source / "nested"))

    def test_word_keeps_formatting_and_redacts_across_runs(self):
        from docx.shared import Pt
        from anonymizer.office import redact_office

        source = self.root / "styled.docx"
        target = self.root / "redacted.docx"
        doc = Document()
        paragraph = doc.add_heading(level=2)
        name = paragraph.add_run("Šárkou ")
        name.font.name = "Cambria"
        name.font.size = Pt(16)
        name.bold = True
        paragraph.add_run("Hrubou")
        paragraph.add_run(" a důležitý text").italic = True
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "ŠÁRKA HRUBÁ"
        doc.save(source)
        redact_office(source, target, Rules())
        result = Document(target)
        paragraph = result.paragraphs[0]
        self.assertEqual(paragraph.style.name, "Heading 2")
        self.assertEqual(paragraph.text, "[OSOBA-1] a důležitý text")
        self.assertEqual(paragraph.runs[0].font.name, "Cambria")
        self.assertEqual(paragraph.runs[0].font.size, Pt(16))
        self.assertTrue(paragraph.runs[0].bold)
        self.assertTrue(paragraph.runs[2].italic)
        self.assertEqual(result.tables[0].cell(0, 0).text, "[OSOBA-1]")

    def test_spreadsheet_and_slide_keep_style_parts(self):
        from anonymizer.office import DRAW, SHEET, redact_office

        cases = [
            (".xlsx", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml",
             f'<worksheet xmlns="{SHEET}"><sheetData><row><c r="A1" s="4" t="inlineStr"><is><r><rPr><b/></rPr><t>Šárka </t></r><r><t>Hrubá</t></r></is></c><c r="B1" s="2"><f>SUM(C1:C3)</f><v>12</v></c></row></sheetData></worksheet>',
             ('s="4"', '<b', 'SUM(C1:C3)')),
            (".pptx", "ppt/presentation.xml", "ppt/slides/slide1.xml", "ppt/theme/theme1.xml",
             f'<slide xmlns:a="{DRAW}"><a:p><a:r><a:rPr b="1" i="1"/><a:t>Šárka Hrubá</a:t></a:r></a:p></slide>',
             ('b="1"', 'i="1"')),
        ]
        for suffix, main, part, styles, xml, preserved in cases:
            with self.subTest(suffix=suffix):
                source = self.root / ("test" + suffix)
                target = self.root / ("redacted" + suffix)
                with ZipFile(source, "w") as archive:
                    archive.writestr(main, "<root/>")
                    archive.writestr(part, xml)
                    archive.writestr(styles, '<styles><font name="Cambria"/></styles>')
                redact_office(source, target, Rules())
                with ZipFile(target) as archive:
                    data = archive.read(part).decode()
                    self.assertIn("[OSOBA-1]", data)
                    self.assertNotIn("Hrubá", data)
                    for marker in preserved:
                        self.assertIn(marker, data)
                    self.assertIn(b"Cambria", archive.read(styles))

    def test_spreadsheet_columns_by_header(self):
        from anonymizer.office import SHEET, redact_office

        source, target = self.root / "registr.xlsx", self.root / "registr_out.xlsx"
        shared = ["Jméno a příjmení", "Datum narození:", "Datum podpisu", "IČO", "Poznámka",
                  "Karolína Bílá", "Telefon"]
        rows = [
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>6</v></c></row>',
            '<row r="2"><c r="A2" t="s"><v>5</v></c><c r="B2" s="3"><v>29252</v></c><c r="C2" s="3"><v>43831</v></c>'
            '<c r="D2"><v>12345678</v></c><c r="E2"><v>12345678</v></c>'
            '<c r="F2" t="inlineStr"><is><t>777 123 456</t></is></c></row>',
            # Omitted references continue from the previous cell; a repeated value keeps its label.
            '<row r="3"><c t="s"><v>5</v></c><c s="3"><v>29252</v></c></row>',
        ]
        with ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{SHEET}"><workbookPr/></workbook>')
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{SHEET}">' + "".join(
                f"<si><t>{value}</t></si>" for value in shared) + "</sst>")
            archive.writestr("xl/worksheets/sheet1.xml",
                             f'<worksheet xmlns="{SHEET}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
        redact_office(source, target, Rules())
        with ZipFile(target) as archive:
            strings = archive.read("xl/sharedStrings.xml").decode()
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("Karolína", strings)
        self.assertIn("[OSOBA-1]", strings)
        self.assertIn("Datum podpisu", strings)
        self.assertEqual(sheet.count("[NAROZENI-1]"), 2)
        self.assertNotIn("29252", sheet)
        self.assertIn("<v>43831</v>", sheet)
        self.assertIn("[ICO-1]", sheet)
        self.assertIn('<c r="E2"><v>12345678</v></c>', sheet)
        self.assertIn("[TELEFON-1]", sheet)
        self.assertNotIn("777", sheet)

        formula = self.root / "vzorec.xlsx"
        with ZipFile(formula, "w") as archive:
            archive.writestr("xl/workbook.xml", "<root/>")
            archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{SHEET}"><sheetData>'
                             '<row><c t="inlineStr"><is><t>Rodné číslo</t></is></c></row>'
                             '<row><c t="str"><f>A5</f><v>800101/1234</v></c></row></sheetData></worksheet>')
        with self.assertRaisesRegex(ValueError, "Vzorec"):
            redact_office(formula, self.root / "vzorec_out.xlsx", Rules())

    def test_word_and_slide_tables_by_header(self):
        from docx.shared import Pt
        from anonymizer.office import DRAW, redact_office

        document = Document()
        table = document.add_table(rows=4, cols=4)
        for cell, text in zip(table.rows[0].cells, ["Poznámka", "Jméno a příjmení", "Číslo OP", "Datum podpisu"]):
            cell.text = text
        values = [["bez poznámky", "Karolína Bílá", "AB123456", "3. 4. 2020"],
                  ["", "Karolína Bílá", "OP", "3. 4. 2020"]]
        for row, texts in zip(table.rows[1:], values):
            for cell, text in zip(row.cells, texts):
                cell.text = text
        run = table.cell(1, 1).paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(13)
        # Cells spanning two grid columns keep later columns aligned with their headers.
        table.cell(3, 0).merge(table.cell(3, 1)).text = "Souhrn"
        table.cell(3, 2).text = "AB999999"
        source, target = self.root / "tabulka.docx", self.root / "tabulka_out.docx"
        document.save(source)
        redact_office(source, target, Rules())
        result = Document(target).tables[0]
        self.assertEqual([cell.text for cell in result.rows[1].cells],
                         ["bez poznámky", "[OSOBA-1]", "[DOKLAD-1]", "3. 4. 2020"])
        self.assertEqual([cell.text for cell in result.rows[2].cells],
                         ["", "[OSOBA-1]", "OP", "3. 4. 2020"])
        self.assertEqual(result.cell(3, 0).text, "Souhrn")
        self.assertEqual(result.cell(3, 2).text, "[DOKLAD-2]")
        self.assertTrue(result.cell(1, 1).paragraphs[0].runs[0].bold)
        self.assertEqual(result.cell(1, 1).paragraphs[0].runs[0].font.size, Pt(13))

        slide = self.root / "snimek.pptx"
        cell = '<a:tc{attr}><a:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></a:txBody></a:tc>'
        rows = [["Poznámka", "", "Číslo OP"], ["Karolína", "", "XY777888"]]
        xml = f'<slide xmlns:a="{DRAW}"><a:tbl>' + "".join(
            "<a:tr>" + "".join(cell.format(attr="", text=text) for text in row) + "</a:tr>" for row in rows
        ) + "<a:tr>" + cell.format(attr=' gridSpan="2"', text="Souhrn") + cell.format(attr=' hMerge="1"', text="")
        xml += cell.format(attr="", text="XY608111") + "</a:tr></a:tbl></slide>"
        with ZipFile(slide, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<root/>")
            archive.writestr("ppt/slides/slide1.xml", xml)
        redact_office(slide, self.root / "snimek_out.pptx", Rules())
        with ZipFile(self.root / "snimek_out.pptx") as archive:
            data = archive.read("ppt/slides/slide1.xml").decode()
        self.assertIn("[DOKLAD-1]", data)
        self.assertIn("[DOKLAD-2]", data)
        self.assertNotIn("XY", data)
        self.assertIn("Karolína", data)
        self.assertIn("Souhrn", data)

    def test_form_layouts(self):
        from anonymizer.czech import plan_table

        def plan(*rows):
            return plan_table([list(enumerate(row)) for row in rows])

        # A form table without colons: labels in the first column.
        self.assertEqual(plan(["Jméno a příjmení", "Karolína Bílá"], ["Rodné číslo", "800101/1234"],
                              ["Poznámka", "bez poznámky"]),
                         {(0, 1): "OSOBA", (1, 1): "RC"})
        # Two pairs in a row, with an empty spacer cell as in merged spreadsheet forms.
        self.assertEqual(plan(["Jméno", "", "Karolína Bílá", "Telefon", "777 123 456"],
                              ["Adresa", "", "Dlouhá 5, Praha", "E-mail", "kb@example.test"]),
                         {(0, 2): "OSOBA", (0, 4): "TELEFON", (1, 2): "ADRESA", (1, 4): "EMAIL"})
        # A single "Label:" row above a column table is labelled without breaking the columns.
        self.assertEqual(plan(["Účastník:", "Karolína Bílá", ""], ["Jméno", "Telefon", "Poznámka"],
                              ["Ota Hrubý", "608 111 222", "x"], ["Iva Malá", "", "y"]),
                         {(0, 1): "OSOBA", (2, 0): "OSOBA", (2, 1): "TELEFON", (3, 0): "OSOBA"})
        # Column headers are not mistaken for form pairs, with or without colons.
        self.assertEqual(plan(["Datum narození:", "Datum podpisu", "Jméno", "Poznámka"],
                              ["1. 2. 1980", "3. 4. 2020", "Ota Hrubý", "x"]),
                         {(1, 0): "NAROZENI", (1, 2): "OSOBA"})

        rules = Rules()
        text = ("Jméno a příjmení: Karolína Bílá\nDatum narození: 1. 2. 1980\n"
                "Adresa: Dlouhá 5, 110 00 Praha 1; Tel.: 777 123 456\nDatum podpisu: 3. 4. 2020\nJméno: Poznámka")
        self.assertEqual(rules.replace(text)[0],
                         "Jméno a příjmení: [OSOBA-1]\nDatum narození: [NAROZENI-1]\n"
                         "Adresa: [ADRESA-1]; Tel.: [TELEFON-1]\nDatum podpisu: 3. 4. 2020\nJméno: Poznámka")

        document = Document()
        table = document.add_table(rows=2, cols=2)
        for row, texts in zip(table.rows, [["Jméno", "Karolína Bílá"], ["Číslo OP", "XY777888"]]):
            for cell, value in zip(row.cells, texts):
                cell.text = value
        source, target = self.root / "formular.docx", self.root / "formular_out.docx"
        document.save(source)
        from anonymizer.office import redact_office
        redact_office(source, target, Rules())
        result = Document(target).tables[0]
        self.assertEqual([[cell.text for cell in row.cells] for row in result.rows],
                         [["Jméno", "[OSOBA-1]"], ["Číslo OP", "[DOKLAD-1]"]])

    def test_generated_given_names(self):
        from anonymizer.names import female_forms, male_forms

        self.assertEqual(male_forms("Hynek"), ["hynek", "hynka", "hynkovi", "hynku", "hynkem"])
        self.assertEqual(male_forms("Leoš")[1:], ["leoše", "leošovi", "leoši", "leošem"])
        self.assertIn("olze", female_forms("Olga"))
        self.assertIn("karolíně", female_forms("Karolína"))
        self.assertEqual(female_forms("Natálie"), ["natálie", "natálii", "natálií"])
        self.assertEqual(female_forms("Dagmar"), ["dagmar"])

        rules = Rules()
        text, count = rules.replace("Smlouvu uzavřeli Karolína Bílá a Hynek Dvořák. Hynka Dvořáka zastupuje "
                                    "Olga Kratochvílová, Bohumila Malého zastupuje Ivo Novák. "
                                    "Věc projednal Okresní soud v Praze.")
        # Labels are numbered from the end of the text; declined forms share a label.
        self.assertEqual(text, "Smlouvu uzavřeli [OSOBA-5] a [OSOBA-4]. [OSOBA-4] zastupuje "
                               "[OSOBA-3], [OSOBA-2] zastupuje [OSOBA-1]. Věc projednal Okresní soud v Praze.")
        self.assertEqual(count, 6)

    @staticmethod
    def write_tiff(path, pixmaps):
        """Write an uncompressed multi-page greyscale TIFF without extra dependencies."""
        import struct

        data = bytearray(b"II*\0\0\0\0\0")
        previous = 4
        for pixmap in pixmaps:
            pixels = pixmap.samples
            offset = len(data)
            data += pixels
            if len(data) % 2:
                data += b"\0"
            entries = [(256, 4, pixmap.width), (257, 4, pixmap.height), (258, 3, 8), (259, 3, 1),
                       (262, 3, 1), (273, 4, offset), (277, 3, 1), (278, 4, pixmap.height),
                       (279, 4, len(pixels)), (282, 5, 0), (283, 5, 0), (296, 3, 2)]
            rational = len(data) + 2 + 12 * len(entries) + 4
            directory = len(data)
            struct.pack_into("<I", data, previous, directory)
            data += struct.pack("<H", len(entries))
            for tag, kind, value in entries:
                if kind == 5:
                    value = rational
                data += struct.pack("<HHI", tag, kind, 1) + (struct.pack("<HH", value, 0) if kind == 3
                                                             else struct.pack("<I", value))
            previous = len(data)
            data += b"\0\0\0\0" + struct.pack("<II", 200, 1)
        path.write_bytes(bytes(data))

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_tiff_scan_becomes_redacted_pdf(self):
        pages = []
        for text in (self.email, "Telefon 777 123 456"):
            with pdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 90), text, fontsize=22)
                pages.append(page.get_pixmap(dpi=200, colorspace=pdf.csGRAY))
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        self.write_tiff(source / "sken.tif", pages)
        (source / "prejmenovany.bin").write_bytes((source / "sken.tif").read_bytes())
        results = list(process_folders(source, target, tessdata=os.environ["SPISREDAKT_TEST_TESSDATA"]))
        self.assertEqual(len(results), 2)
        for original, output, reason in results:
            self.assertIsNotNone(output, reason)
            self.assertEqual(output.suffix, ".pdf")
            with pdf.open(output) as result:
                self.assertEqual(len(result), 2)
                for page in result:
                    self.assertEqual(page.get_text(), "")
                    image = pdf.Pixmap(result, page.get_images()[0][0])
                    self.assertGreater(image.color_count(colors=True).get(b"\0\0\0", 0), 5_000)
                    remaining = recognize_image(image, os.environ["SPISREDAKT_TEST_TESSDATA"])[0]
                    self.assertEqual(Rules().find(remaining), [], remaining)

    def test_rtf_keeps_formatting(self):
        from anonymizer.rtf import redact_rtf

        document = (rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}"
                    rb"{\info{\author Karol\'edna B\'edl\'e1}{\title Spis}}" b"\n"
                    rb"\uc1\pard\b Smlouvu uzav\'f8eli {\i Karol\'edna} B\u237?l\u225? a Hynek Dvo\'f8\'e1k.\b0\par" b"\n"
                    rb"Tel.: 777 123 456\par" b"\n"
                    rb"{\field{\*\fldinst {HYPERLINK " b'"mailto:kb@example.test"' rb"}}{\fldrslt kb@example.test}}\par"
                    rb"{\*\bkmkstart Poznamka}{\*\bkmkend Poznamka}Datum podpisu: 3. 4. 2020\par}")
        output = redact_rtf(document, Rules())
        self.assertEqual(output, (
            rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
            rb"\uc1\pard\b Smlouvu uzav\'f8eli {\i [OSOBA-2]} a [OSOBA-1].\b0\par" b"\n"
            rb"Tel.: [TELEFON-1]\par" b"\n"
            rb"{\field{\*\fldinst { HYPERLINK " b'"https://example.invalid/"' rb" }}{\fldrslt [EMAIL-1]}}\par"
            rb"{\*\bkmkstart Poznamka}{\*\bkmkend Poznamka}Datum podpisu: 3. 4. 2020\par}"))
        with self.assertRaisesRegex(ValueError, "skryté"):
            redact_rtf(rb"{\rtf1{\*\bkmkstart kb@example.test}Text\par}", Rules())
        with self.assertRaisesRegex(ValueError, "objekt"):
            redact_rtf(rb"{\rtf1{\object\objemb{\*\objdata 00}}\par}", Rules())

    def test_rtf_tables_use_headers_and_form_labels(self):
        from anonymizer.rtf import collect, parse, redact_rtf
        from anonymizer.formats import redact_file

        def row(*cells):
            body = rb"\trowd\cellx2000\cellx4000\cellx6000" b"\n"
            for cell in cells:
                body += rb"\pard\intbl " + cell + rb"\cell" + b"\n"
            return body + rb"\row" + b"\n"

        head = rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
        table = (rb"Pred tabulkou\par" b"\n"
                 + row(rb"{\b Jm\'e9no}", rb"Telefon", rb"Pozn\'e1mka")
                 + row(rb"{\b Xaver Blum}", rb"777 123 456", rb"bez pozn\'e1mky")
                 + rb"\pard Za tabulkou\par}")
        output = redact_rtf(head + table, Rules())
        self.assertNotIn(b"Xaver", output)
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[OSOBA-", output)
        self.assertIn(b"[TELEFON-1]", output)
        self.assertIn(b"bez pozn\\'e1mky", output)
        self.assertIn(rb"\trowd\cellx2000\cellx4000\cellx6000", output)
        self.assertIn(rb"{\b [OSOBA-", output)
        self.assertIn(b"Za tabulkou", output)
        collect(parse(output))

        form = (head + row(rb"Rodn\'e9 \'e8\'edslo:", rb"800101/1234", b"")
                + row(rb"Jm\'e9no", rb"Xaver Blum", b"") + rb"\pard\par}")
        output = redact_rtf(form, Rules())
        self.assertNotIn(b"800101", output)
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[RC-1]", output)
        self.assertIn(b"[OSOBA-", output)

        split = head + row(rb"Jm\'e9no", b"", b"") + row(rb"Xaver\par Blum", b"", b"") + rb"\pard\par}"
        output = redact_rtf(split, Rules())
        self.assertNotIn(b"Xaver", output)
        self.assertNotIn(b"Blum", output)
        self.assertEqual(output.count(b"[OSOBA-1]"), 1)

        grouped = (head + rb"\trowd\cellx3000\cellx6000" b"\n"
                   rb"\pard\intbl {Jm\'e9no\cell}{Telefon\cell}\row" b"\n"
                   rb"\trowd\cellx3000\cellx6000" b"\n"
                   rb"\pard\intbl {Xaver Blum\cell}{777 123 456\cell}\row\pard Konec\par}")
        output = redact_rtf(grouped, Rules())
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[OSOBA-1]", output)
        self.assertIn(b"Konec", output)

        nested = (head + row(rb"Jm\'e9no", rb"Telefon", b"")
                  + rb"\trowd\cellx2000\cellx4000" b"\n"
                  rb"\pard\intbl Xaver Blum\par" b"\n"
                  rb"\pard\intbl\itap2 Jan Tester 777 123 456\nestcell"
                  rb"{\*\nesttableprops\trowd\cellx1000\nestrow}"
                  rb"\pard\intbl\cell\pard\intbl x\cell\row\pard\par}")
        output = redact_rtf(nested, Rules())
        self.assertNotIn(b"777 123", output)
        self.assertNotIn(b"Blum", output)
        collect(parse(output))

        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory, "a.rtf"), Path(directory, "b.rtf")
            source.write_bytes(head + table)
            redact_file(source, target, ".rtf", Rules(), None)
            self.assertNotIn(b"Blum", target.read_bytes())

    def test_rtf_adjacent_tables_are_planned_separately(self):
        from anonymizer.rtf import redact_rtf

        def row(edges, *cells):
            body = rb"\trowd" + b"".join(rb"\cellx%d" % edge for edge in edges) + b"\n"
            for cell in cells:
                body += rb"\pard\intbl " + cell + rb"\cell" + b"\n"
            return body + rb"\row" + b"\n"

        columns, form = (1000, 2000, 3000), (1500, 3000)
        document = (rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
                    + row(columns, rb"Jm\'e9no", rb"Telefon", rb"Pozn\'e1mka")
                    + row(columns, rb"Xaver\par Blum", rb"777 123 456", rb"bez pozn\'e1mky")
                    + row(form, rb"Rodn\'e9 \'e8\'edslo:", rb"800101/1234")
                    + row(form, rb"Jm\'e9no", rb"Oto Kramerius") + rb"\pard\par}")
        output = redact_rtf(document, Rules())
        for word in (b"Xaver", b"Blum", b"Kramerius", b"800101", b"777 123"):
            self.assertNotIn(word, output)
        self.assertIn(b"bez pozn\\'e1mky", output)
        self.assertEqual(output.count(b"[OSOBA-"), 2)

    def test_opendocument_text_and_spreadsheet(self):
        from anonymizer.formats import identify, redact_file

        office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
        xlink = "http://www.w3.org/1999/xlink"
        namespaces = f'xmlns:office="{office}" xmlns:text="{text}" xmlns:table="{table}" xmlns:xlink="{xlink}"'
        content = (
            f'<office:document-content {namespaces}><office:body><office:text>'
            '<text:p text:style-name="P1">Smlouvu uzavřeli <text:span text:style-name="T1">Karolína</text:span>'
            '<text:s/>Bílá a<text:s text:c="2"/>Hynek Dvořák.</text:p>'
            f'<text:p><text:a xlink:href="mailto:kb@example.test">kb@example.test</text:a></text:p>'
            '<table:table table:name="Seznam"><table:table-row>'
            '<table:table-cell office:value-type="string"><text:p>Jméno</text:p></table:table-cell>'
            '<table:table-cell office:value-type="string"><text:p>Datum narození</text:p></table:table-cell>'
            '</table:table-row><table:table-row>'
            '<table:table-cell office:value-type="string"><text:p>Ota Hrubý</text:p></table:table-cell>'
            '<table:table-cell office:value-type="date" office:date-value="1980-02-01"><text:p>01.02.80</text:p></table:table-cell>'
            '</table:table-row></table:table></office:text></office:body></office:document-content>')
        meta = (f'<office:document-meta {namespaces} xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:creator>Karolína Bílá</dc:creator>'
                '<meta:generator>Test</meta:generator></office:meta></office:document-meta>')
        manifest = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                    '<manifest:file-entry manifest:full-path="Thumbnails/thumbnail.png" manifest:media-type="image/png"/>'
                    '</manifest:manifest>')
        source, target = self.root / "smlouva.bin", self.root / "smlouva_out.odt"
        with ZipFile(source, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("content.xml", content)
            archive.writestr("meta.xml", meta)
            archive.writestr("styles.xml", f'<office:document-styles {namespaces}/>')
            archive.writestr("META-INF/manifest.xml", manifest)
            archive.writestr("Thumbnails/thumbnail.png", b"\x89PNG")
        self.assertEqual(identify(source.read_bytes(), ".bin"), ".odt")
        redact_file(source, target, ".odt", Rules(), None)
        with ZipFile(target) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertNotIn("Thumbnails/thumbnail.png", archive.namelist())
            self.assertNotIn(b"Thumbnails", archive.read("META-INF/manifest.xml"))
            self.assertNotIn("Karolína".encode(), archive.read("meta.xml"))
            result = archive.read("content.xml").decode()
        for value in ("Karolína", "Bílá", "Dvořák", "kb@example", "Hrubý", "1980-02-01", "01.02.80"):
            self.assertNotIn(value, result)
        # Tables are planned first; the removed single space keeps the following text.
        self.assertIn('text:style-name="T1">[OSOBA-3]</text:span> a<text:s text:c="2"/>[OSOBA-2].', result)
        self.assertIn("<text:p>[OSOBA-1]</text:p>", result)
        self.assertIn('office:value-type="string"><text:p>[NAROZENI-1]</text:p>', result)
        self.assertIn("https://example.invalid/", result)

    @staticmethod
    def compound_file(streams):
        """Build a minimal OLE compound file; ``streams`` maps "a/b" paths to bytes."""
        import struct

        tree = {"children": {}}
        for path, data in streams.items():
            node = tree
            parts = path.split("/")
            for part in parts[:-1]:
                node = node["children"].setdefault(part, {"children": {}})
            node["children"][parts[-1]] = {"data": data}
        entries = []

        def add(name, node, kind):
            index = len(entries)
            entries.append({"name": name, "kind": kind, "node": node, "child": 0xFFFFFFFF, "right": 0xFFFFFFFF})
            previous = None
            for child_name, child in node.get("children", {}).items():
                child_index = add(child_name, child, 2 if "data" in child else 1)
                if previous is None:
                    entries[index]["child"] = child_index
                else:
                    entries[previous]["right"] = child_index
                previous = child_index
            return index

        add("Root Entry", tree, 5)
        sector, mini = 512, 64
        # Streams below 4096 bytes live in the mini stream; the test data are all small.
        ministream, minifat = b"", []
        for entry in entries:
            data = entry["node"].get("data")
            if data is None:
                entry["start"], entry["size"] = 0xFFFFFFFE, 0
                continue
            assert len(data) < 4096
            count = max(1, -(-len(data) // mini))
            first = len(ministream) // mini
            minifat += [first + i + 1 for i in range(count - 1)] + [0xFFFFFFFE]
            ministream += data.ljust(count * mini, b"\0")
            entry["start"], entry["size"] = first, len(data)
        directory_sectors = -(-len(entries) * 128 // sector)
        minifat_sectors = max(1, -(-len(minifat) * 4 // sector))
        stream_sectors = max(1, -(-len(ministream) // sector))
        total = directory_sectors + minifat_sectors + stream_sectors
        fat_sectors = 1
        while -(-(total + fat_sectors) * 4 // sector) > fat_sectors:
            fat_sectors += 1
        fat = [0xFFFFFFFD] * fat_sectors
        position = fat_sectors
        layout = {}
        for name, count in (("directory", directory_sectors), ("minifat", minifat_sectors),
                            ("stream", stream_sectors)):
            layout[name] = position
            fat += [position + i + 1 for i in range(count - 1)] + [0xFFFFFFFE]
            position += count
        fat += [0xFFFFFFFF] * (fat_sectors * sector // 4 - len(fat))
        entries[0]["start"], entries[0]["size"] = layout["stream"], len(ministream)
        header = struct.pack("<8s16sHHHHH6sIIIIIIIII", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\0" * 16, 0x3E, 3,
                             0xFFFE, 9, 6, b"\0" * 6, 0, fat_sectors, layout["directory"], 0, 4096,
                             layout["minifat"], minifat_sectors, 0xFFFFFFFE, 0)
        header += b"".join(struct.pack("<I", i) for i in range(fat_sectors))
        header += b"\xff" * (sector - len(header))
        minifat += [0xFFFFFFFF] * (minifat_sectors * sector // 4 - len(minifat))
        chunks = [b"".join(struct.pack("<I", value) for value in minifat),
                  ministream.ljust(stream_sectors * sector, b"\0")]
        directory = b""
        for entry in entries:
            name = (entry["name"] + "\0").encode("utf-16-le")
            directory += struct.pack("<64sHBBIII16sIQQIQ", name, len(name), entry["kind"], 1, 0xFFFFFFFF,
                                     entry["right"], entry["child"], b"\0" * 16, 0, 0, 0,
                                     entry.get("start", 0xFFFFFFFE), entry.get("size", 0))
        directory = directory.ljust(directory_sectors * sector, b"\0")
        return header + b"".join(struct.pack("<I", value) for value in fat) + directory + b"".join(chunks)

    def word_file(self, main, header="", paragraphs=None, bad_page=False):
        """Build a minimal DOC. ``paragraphs`` lists (in_table, ttp, itap) for the leading
        paragraphs (ending with \\r or \\x07); None leaves out PlcBtePapx."""
        import struct

        text = main + header
        ends = [index + 1 for index, char in enumerate(text) if char in "\r\x07"]
        starts = [0] + ends[:-1]
        ranges = list(zip(starts, ends))
        flags = list(paragraphs or []) + [(0, 0, 0)] * (len(ranges) - len(paragraphs or []))
        runs = []
        for (first, last), (in_table, ttp, itap) in zip(ranges, flags):
            grpprl = b""
            if in_table:
                grpprl += struct.pack("<HB", 0x2416, 1)
            if ttp:
                grpprl += struct.pack("<HB", 0x2417, 1)
            if itap:
                grpprl += struct.pack("<Hi", 0x6649, itap)
            runs.append((first, last, grpprl))
        chunks = [runs[index:index + 6] for index in range(0, len(runs), 6)]
        base = 1024 + 512 * len(chunks)
        pages = []
        for chunk in chunks:
            page = bytearray(512)
            edges = [base + 2 * chunk[0][0]] + [base + 2 * last for _, last, _ in chunk]
            struct.pack_into(f"<{len(edges)}I", page, 0, *edges)
            top = 511
            for number, (_, _, grpprl) in enumerate(chunk):
                content = b"\0\0" + grpprl
                if len(content) % 2 == 0:
                    content += b"\0"
                top -= len(content) + 1
                top -= top % 2
                page[top] = (len(content) + 1) // 2
                page[top + 1:top + 1 + len(content)] = content
                page[4 * (len(chunk) + 1) + 13 * number] = top // 2
            page[511] = len(chunk)
            pages.append(bytes(page))
        word = bytearray(1024)
        struct.pack_into("<HHH", word, 0, 0xA5EC, 0xC1, 0)
        struct.pack_into("<H", word, 0x0A, 0x0200)
        struct.pack_into("<H", word, 32, 14)
        struct.pack_into("<H", word, 62, 22)
        struct.pack_into("<ii", word, 64 + 12, len(main), 0)
        struct.pack_into("<i", word, 64 + 20, len(header))
        struct.pack_into("<H", word, 152, 93)
        plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, base, 0)
        clx = b"\x02" + struct.pack("<I", len(plc)) + plc
        struct.pack_into("<II", word, 154 + 33 * 8, 0, len(clx))
        table = clx
        if paragraphs is not None:
            numbers = [2 + index + (100 if bad_page else 0) for index in range(len(chunks))]
            edges = [base + 2 * chunk[0][0] for chunk in chunks] + [base + 2 * len(text)]
            papx = struct.pack(f"<{len(edges)}I", *edges) + struct.pack(f"<{len(numbers)}I", *numbers)
            struct.pack_into("<II", word, 154 + 13 * 8, len(clx), len(papx))
            table += papx
        for page in pages:
            word += page
        word += text.encode("utf-16-le")
        return self.compound_file({"WordDocument": bytes(word), "1Table": table})

    def doc_structure(self, doc):
        """Convert DOC to DOCX and check that no visible character was lost or duplicated."""
        from collections import Counter
        from docx.oxml.ns import qn
        from anonymizer.legacy import clean, doc_text, doc_to_docx

        result = Document(io.BytesIO(doc_to_docx(doc)))
        shown = "".join(node.text or "" for node in result.element.body.iter(qn("w:t")))
        given = clean(doc_text(doc)[0])

        def count(value):
            return Counter(char for char in value if not char.isspace() and char != "\x07")

        self.assertEqual(count(given), count(shown))
        return result

    def test_legacy_word_tables_by_paragraph_properties(self):
        from anonymizer.formats import redact_file
        from anonymizer.legacy import doc_to_docx

        T, ROW = (1, 0, 1), (1, 1, 1)
        grid = "Jméno\ra příjmení\x07Telefon\x07\x07Karolína\rBílá\x07777 123 456\x07\x07"
        props = [T, T, T, ROW, T, T, T, ROW]
        doc = self.word_file(grid, "", props)
        result = self.doc_structure(doc)
        self.assertEqual(len(result.tables), 1)
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["Jméno\na příjmení", "Telefon"], ["Karolína\nBílá", "777 123 456"]])
        source, target = self.root / "tabulka.doc", self.root / "tabulka_out.docx"
        source.write_bytes(doc)
        redact_file(source, target, ".doc", Rules(), None)
        cells = [[cell.text for cell in row.cells] for row in Document(target).tables[0].rows]
        self.assertEqual(cells[1][1], "[TELEFON-1]")
        # A two-line heading ("Jméno" / "a příjmení") is not recognized as a name column,
        # so only the structure is checked for the name cell.
        self.assertEqual(cells[1][0], "Karolína\nBílá")

        # Text around the table stays in paragraphs and the table does not end early.
        doc = self.word_file("Před\r" + grid + "Za\r", "", [(0, 0, 0)] + props)
        result = self.doc_structure(doc)
        self.assertEqual([paragraph.text for paragraph in result.paragraphs if paragraph.text], ["Před", "Za"])
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(len(result.tables[0].rows), 2)

        # A nested table is flattened into the cell of the outer table.
        main = "Úvod\rX\rVnitřní1\x07Vnitřní2\x07\x07\x07Druhá\x07\x07Konec\r"
        props = [(0, 0, 0), T, (1, 0, 2), (1, 0, 2), (1, 1, 2), T, T, ROW]
        result = self.doc_structure(self.word_file(main, "", props))
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["X\nVnitřní1\nVnitřní2", "Druhá"]])
        self.assertEqual([paragraph.text for paragraph in result.paragraphs if paragraph.text], ["Úvod", "Konec"])

        # Damaged paragraph properties give the same result as no properties at all.
        main = "Jméno\x07Telefon\x07\x07Karolína\rBílá\x07777\x07\x07Konec\r"
        expected = self.word_file(main)
        damaged = self.word_file(main, "", [T, T, ROW, T, T, T, ROW], bad_page=True)

        def body(data):
            with ZipFile(io.BytesIO(doc_to_docx(data))) as archive:
                return archive.read("word/document.xml")

        self.assertEqual(body(damaged), body(expected))
        self.doc_structure(damaged)

    def test_email_formats(self):
        from email import policy
        from email.message import EmailMessage
        from email.parser import BytesParser
        from anonymizer.formats import identify, redact_file

        inner = EmailMessage()
        inner["From"] = "Ota Hrubý <ota@example.test>"
        inner["Subject"] = "Přeposlaná zpráva"
        inner.set_content("Volejte 777 123 456.")
        message = EmailMessage()
        message["From"] = "Xaver Blum <xb@example.test>"
        message["To"] = "Karolína Bílá <kb@example.test>, ota@example.test"
        message["Subject"] = "Spis Karolíny Bílé"
        message["Received"] = "from mail.example.test (10.0.0.1)"
        message["Message-ID"] = "<abc@example.test>"
        message.set_content("Dobrý den, Karolína Bílá, rodné číslo 800101/1234.")
        message.add_alternative('<p>Dobrý den, <b>Karolína</b> Bílá. <a href="mailto:kb@example.test">napište</a>'
                                '<img src="https://tracker.example.test/x.png"><script>alert(1)</script></p>',
                                subtype="html")
        message.make_mixed()
        attachment = EmailMessage()
        attachment.set_content(b"Kontakt kb@example.test", "text", "plain", disposition="attachment",
                               filename="Karolína Bílá.txt")
        message.attach(attachment)
        wrapper = EmailMessage()
        wrapper.set_content(inner)
        message.attach(wrapper)
        source, target = self.root / "posta.eml", self.root / "posta_out.eml"
        source.write_bytes(message.as_bytes(policy=policy.SMTP))
        self.assertEqual(identify(source.read_bytes(), ".eml"), ".eml")
        redact_file(source, target, ".eml", Rules(), None)
        output = target.read_bytes()
        result = BytesParser(policy=policy.default).parsebytes(output)
        for value in (b"Karol", b"Xaver", b"example.test", b"800101", b"10.0.0.1", b"tracker", b"<script"):
            self.assertNotIn(value, output)
        self.assertIsNone(result["Received"])
        self.assertIsNone(result["Message-ID"])
        self.assertEqual(str(result["From"]), '"[OSOBA-1]" <email-1@anonymizovano.invalid>')
        self.assertEqual(str(result["To"]), '"[OSOBA-2]" <email-2@anonymizovano.invalid>, '
                                            "email-3@anonymizovano.invalid")
        parts = {}
        for part in result.walk():
            parts.setdefault(part.get_content_type(), part)
        self.assertIn("[OSOBA-2], rodné číslo [RC-1].", parts["text/plain"].get_content())
        self.assertIn("<b>[OSOBA-2]</b>.", parts["text/html"].get_content())
        attached = [part for part in result.walk() if part.get_filename()]
        self.assertEqual(attached[0].get_filename(), "priloha-0001.txt")
        self.assertEqual(attached[0].get_content(), b"Kontakt [EMAIL-2]")
        nested = next(part for part in result.walk() if part.get_content_type() == "message/rfc822")
        self.assertIn("[TELEFON-1]", nested.get_content().get_content())

        def utf16(text):
            return text.encode("utf-16-le")

        msg = self.compound_file({
            "__substg1.0_0037001F": utf16("Spis Karolíny Bílé"),
            "__substg1.0_0C1A001F": utf16("Xaver Blum"),
            "__substg1.0_0C1F001F": utf16("xb@example.test"),
            "__substg1.0_1000001F": utf16("Paní Karolína Bílá, tel. 777 123 456."),
            "__recip_version1.0_#00000000/__substg1.0_3001001F": utf16("Karolína Bílá"),
            "__recip_version1.0_#00000000/__substg1.0_3003001F": utf16("kb@example.test"),
            "__attach_version1.0_#00000000/__substg1.0_3707001F": utf16("seznam.csv"),
            "__attach_version1.0_#00000000/__substg1.0_37010102": "Jméno;Telefon\nOta Hrubý;608 111 222\n".encode(),
        })
        source, target = self.root / "zprava.msg", self.root / "zprava_out.eml"
        source.write_bytes(msg)
        self.assertEqual(identify(msg, ".bin"), ".msg")
        redact_file(source, target, ".msg", Rules(), None)
        output = target.read_bytes()
        self.assertNotIn(b"example.test", output)
        result = BytesParser(policy=policy.default).parsebytes(output)
        self.assertEqual(str(result["To"]), '"[OSOBA-2]" <email-2@anonymizovano.invalid>')
        body = next(part for part in result.walk() if part.get_content_type() == "text/plain")
        self.assertEqual(body.get_content().strip(), "Paní [OSOBA-2], tel. [TELEFON-1].")
        attached = next(part for part in result.walk() if part.get_filename())
        self.assertEqual(attached.get_content(), "Jméno;Telefon\r\n[OSOBA-3];[TELEFON-2]\r\n".encode())

    def test_legacy_word_and_excel(self):
        import struct
        from anonymizer.formats import identify, output_suffix, redact_file
        from anonymizer.office import SHEET

        main = ("Smlouva s Karolínou Bílou\r"
                "Jméno\x07Telefon\x07\x07Xaver Blum\x07777 123 456\x07\x07"
                "Pole \x13 HYPERLINK \"mailto:kb@example.test\" \x14odkaz\x15 a obrázek \x01\r")
        header = "Zpracovala Karolína Bílá\r"
        text = main + header
        word = bytearray(1024)
        struct.pack_into("<HHH", word, 0, 0xA5EC, 0xC1, 0)
        struct.pack_into("<H", word, 0x0A, 0x0200)
        struct.pack_into("<H", word, 32, 14)
        struct.pack_into("<H", word, 62, 22)
        struct.pack_into("<ii", word, 64 + 12, len(main), 0)
        struct.pack_into("<i", word, 64 + 20, len(header))
        struct.pack_into("<H", word, 152, 93)
        plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, 1024, 0)
        clx = b"\x02" + struct.pack("<I", len(plc)) + plc
        struct.pack_into("<II", word, 154 + 33 * 8, 0, len(clx))
        word += text.encode("utf-16-le")
        doc = self.compound_file({"WordDocument": bytes(word), "1Table": clx})
        self.assertEqual(identify(doc, ".bin"), ".doc")
        self.assertEqual(output_suffix(".doc"), ".docx")
        source, target = self.root / "stary.doc", self.root / "stary_out.docx"
        source.write_bytes(doc)
        redact_file(source, target, ".doc", Rules(), None)
        result = Document(target)
        paragraphs = [paragraph.text for paragraph in result.paragraphs]
        self.assertEqual(paragraphs[0], "Smlouva s [OSOBA-2]")
        self.assertEqual(paragraphs[1], "Pole odkaz a obrázek [OBRÁZEK VYNECHÁN]")
        self.assertEqual(paragraphs[-1], "Zpracovala [OSOBA-2]")
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["Jméno", "Telefon"], ["[OSOBA-1]", "[TELEFON-1]"]])
        with ZipFile(target) as archive:
            self.assertNotIn(b"example.test", archive.read("word/document.xml"))

        try:
            import xlwt
        except ImportError:
            self.skipTest("Pro vytvoření testovacího XLS chybí xlwt.")
        from datetime import date
        from io import BytesIO

        book = xlwt.Workbook()
        sheet = book.add_sheet("Seznam")
        for column, value in enumerate(["Jméno", "Datum narození", "Telefon", "Poznámka"]):
            sheet.write(0, column, value)
        sheet.write(1, 0, "Xaver Blum")
        sheet.write(1, 1, date(1980, 2, 1), xlwt.easyxf(num_format_str="DD.MM.YYYY"))
        sheet.write(1, 2, 777123456)
        sheet.write(1, 3, "bez poznámky")
        stream = BytesIO()
        book.save(stream)
        self.assertEqual(identify(stream.getvalue(), ".bin"), ".xls")
        source, target = self.root / "stary.xls", self.root / "stary_out.xlsx"
        source.write_bytes(stream.getvalue())
        redact_file(source, target, ".xls", Rules(), None)
        with ZipFile(target) as archive:
            data = archive.read("xl/worksheets/sheet1.xml").decode()
        for value in ("Xaver", "29252", "777123456"):
            self.assertNotIn(value, data)
        for value in ("[OSOBA-1]", "[NAROZENI-1]", "[TELEFON-1]", "bez poznámky", f'xmlns="{SHEET}"'):
            self.assertIn(value, data)

    def test_word_from_office_template(self):
        """Typical office document: template path, mail merge, co-authors, split fields, EMF logo."""
        from anonymizer.office import redact_office
        from anonymizer.reasons import reason_code

        document = Document()
        paragraph = document.add_paragraph("Kontakt: ")
        source, target = self.root / "sablona.docx", self.root / "sablona_out.docx"
        document.save(source)
        relationship = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        patched = self.root / "sablona_patched.docx"
        with ZipFile(source) as original, ZipFile(patched, "w") as archive:
            for entry in original.infolist():
                data = original.read(entry)
                if entry.filename == "word/document.xml":
                    field = (f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> HYPER'
                             f'</w:instrText></w:r><w:r><w:instrText xml:space="preserve">LINK "mailto:kb@</w:instrText></w:r>'
                             f'<w:r><w:instrText>example.test"</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                             f'<w:r><w:t>napište nám</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
                             f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> MERGEFIELD '
                             f'</w:instrText></w:r><w:r><w:instrText>"Karolína Bílá"</w:instrText></w:r>'
                             f'<w:r><w:fldChar w:fldCharType="end"/></w:r>')
                    marker = '<w:t xml:space="preserve">Kontakt: </w:t></w:r>'
                    self.assertIn(marker, data.decode())
                    data = data.decode().replace(marker, marker + field).encode()
                elif entry.filename == "word/settings.xml":
                    data = data.decode().replace("</w:settings>",
                                                 f'<w:attachedTemplate xmlns:r="{relationship}" r:id="rIdTemplate"/>'
                                                 '<w:mailMerge><w:query w:val="SELECT * FROM C:\\Users\\karolina.bila\\adresy.xlsx"/>'
                                                 "</w:mailMerge></w:settings>").encode()
                elif entry.filename == "word/_rels/document.xml.rels":
                    data = data.decode().replace("</Relationships>",
                                                 f'<Relationship Id="rIdLogo" Type="{relationship}/image" Target="media/image1.emf"/>'
                                                 f'<Relationship Id="rIdPeople" Type="http://schemas.microsoft.com/office/2011/relationships/people" Target="people.xml"/>'
                                                 "</Relationships>").encode()
                elif entry.filename == "[Content_Types].xml":
                    data = data.decode().replace("</Types>", '<Default Extension="emf" ContentType="image/x-emf"/>'
                                                 '<Override PartName="/word/people.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"/>'
                                                 "</Types>").encode()
                archive.writestr(entry, data)
            archive.writestr("word/_rels/settings.xml.rels",
                             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             f'<Relationship Id="rIdTemplate" Type="{relationship}/attachedTemplate" '
                             'Target="file:///C:\\Users\\karolina.bila\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm" '
                             'TargetMode="External"/></Relationships>')
            archive.writestr("word/media/image1.emf", b"\x01\x00\x00\x00EMF")
            archive.writestr("word/people.xml", '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
                             '<w15:person w15:author="Karolína Bílá"><w15:presenceInfo w15:providerId="AD" '
                             'w15:userId="kb@example.test"/></w15:person></w15:people>')
        redact_office(patched, target, Rules())
        with ZipFile(target) as archive:
            names = archive.namelist()
            everything = b"".join(archive.read(name) for name in names)
            self.assertIn("word/media/image1_emf.png", names)
            self.assertNotIn("word/media/image1.emf", names)
            self.assertNotIn("word/people.xml", names)
            self.assertIn(b'Target="media/image1_emf.png"', archive.read("word/_rels/document.xml.rels"))
            self.assertIn(b'Extension="png"', archive.read("[Content_Types].xml"))
            self.assertNotIn(b"attachedTemplate", archive.read("word/settings.xml"))
        for leaked in (b"karolina.bila", b"Karol\xc3\xadna", b"example.test", b"Normal.dotm", b"people.xml"):
            self.assertNotIn(leaked, everything)
        self.assertIn(b"https://example.invalid/", everything)
        self.assertEqual(Document(target).paragraphs[0].text, "Kontakt: napište nám")
        self.assertEqual(reason_code(ValueError("OCR nenašlo čitelný text. Stránku zkontrolujte ručně.")),
                         "pdf_page_without_ocr_text")

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_pdf_scanner_quirks(self):
        """Repaired cross-references, a blank back page, a photo page and an oversized drawing."""
        from anonymizer.documents import redact_pdf

        tessdata = os.environ["SPISREDAKT_TEST_TESSDATA"]
        with pdf.open() as text_doc, pdf.open() as scan:
            page = text_doc.new_page()
            page.insert_text((72, 90), self.email, fontsize=22)
            sheet = scan.new_page()
            sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=200))
            white = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 560), False)
            white.clear_with(255)
            scan.new_page().insert_image(pdf.Rect(0, 0, 595, 842), pixmap=white)
            photo = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 560), False)
            photo.clear_with(90)
            scan.new_page().insert_image(pdf.Rect(0, 0, 595, 842), pixmap=photo)
            data = scan.tobytes()
        # Break the cross-reference offset; MuPDF repairs such files when opening them.
        source = self.root / "sken.pdf"
        source.write_bytes(data.replace(b"startxref\n", b"startxref\n9").replace(b"startxref\r\n", b"startxref\r\n9"))
        with pdf.open(source) as check:
            self.assertTrue(check.is_repaired)
        target = self.root / "sken_out.pdf"
        redact_pdf(source, target, Rules(), tessdata)
        with pdf.open(target) as result:
            self.assertEqual(len(result), 3)
            pages = [pdf.Pixmap(result, page.get_images()[0][0]) for page in result]
        self.assertGreater(pages[0].color_count(colors=True).get(b"\0\0\0", 0), 5_000)
        self.assertEqual(set(pages[1].color_count(colors=True)), {b"\xff\xff\xff"})
        self.assertEqual(set(pages[2].color_count(colors=True)), {b"\0\0\0"})

        poster = self.root / "vykres.pdf"
        with pdf.open() as doc:
            doc.new_page(width=4000, height=4000).insert_text((72, 90), self.email, fontsize=40)
            doc.save(poster)
        redact_pdf(poster, self.root / "vykres_out.pdf", Rules(), tessdata)
        with pdf.open(self.root / "vykres_out.pdf") as result:
            image = pdf.Pixmap(result, result[0].get_images()[0][0])
            self.assertLessEqual(image.width * image.height, 30_000_000)

    def test_archive_with_documents(self):
        from io import BytesIO
        from anonymizer.formats import ARCHIVE_BYTES, size_limit

        self.assertEqual(size_limit(".zip"), ARCHIVE_BYTES)
        self.assertEqual(ARCHIVE_BYTES, 500_000_000)
        word = BytesIO()
        document = Document()
        document.add_paragraph("Smlouvu uzavřela Karolína Bílá.")
        document.save(word)
        with pdf.open() as doc:
            doc.new_page().insert_text((72, 90), self.email, fontsize=16)
            portable = doc.tobytes()
        inner = BytesIO()
        with ZipFile(inner, "w") as archive:
            archive.writestr("poznamka.txt", "Telefon 777 123 456")
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        with ZipFile(source / "spis.zip", "w") as archive:
            archive.writestr("1/smlouva.docx", word.getvalue())
            archive.writestr("2/podani.pdf", portable)
            archive.writestr("3/prilohy.zip", inner.getvalue())
        [(original, output, reason)] = list(process_folders(source, target))
        self.assertIsNotNone(output, reason)
        with ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ["polozka-0001.docx", "polozka-0002.pdf", "polozka-0003.zip"])
            self.assertEqual(Document(BytesIO(archive.read("polozka-0001.docx"))).paragraphs[0].text,
                             "Smlouvu uzavřela [OSOBA-1].")
            with pdf.open(stream=archive.read("polozka-0002.pdf")) as result:
                self.assertEqual(result[0].get_text(), "")
            with ZipFile(BytesIO(archive.read("polozka-0003.zip"))) as nested:
                self.assertEqual(nested.read("polozka-0001.txt"), b"Telefon [TELEFON-1]")

    def test_case_law_citations_stay(self):
        rules = Rules()
        text = ("nikoliv však již dobu přesahující 12 let (srov. usnesení Krajského soudu v Brně ze dne "
                "26. 5. 2023, č. j. 70 Co 123/2023-45).\nV řešené věci byla exekuce vedena na základě usnesení "
                "Okresního soudu v Uherském Hradišti ze dne 22. 3. 2010, č. j. 12 Nc 3456/2010-7, které nabylo "
                "právní moci. Náhrada nepříslušela (mutatis mutandis usnesení Nejvyššího soudu ze dne 20. 12. 2016, "
                "sp. zn. 20 Cdo 4567/2016). Dovolání (sp. zn. 26 Cdo 12/2024) bylo odmítnuto. Nejvyšší soud zrušil "
                "usnesení Okresního soudu v Kroměříži ze dne 1. 2. 2020, č. j. 12 C 1/2020-5. Rozsudek Nejvyššího "
                "soudu ze dne 1. 1. 2020, sp. zn. 21 Ko 5/2020. Návrh ze dne 3. 2. 2023, sp. zn. 120 EX 1234/10, "
                "exekutor zamítl.")
        result = rules.replace(text)[0]
        for kept in ("70 Co 123/2023-45", "20 Cdo 4567/2016", "26 Cdo 12/2024", "21 Ko 5/2020"):
            self.assertIn(kept, result)
        for removed in ("12 Nc 3456/2010-7", "12 C 1/2020-5", "120 EX 1234/10"):
            self.assertNotIn(removed, result)
        self.assertIn("Hradišti ze dne 22. 3. 2010, č. j. [SPIS-", result)

    def test_initials(self):
        rules = Rules()
        lines = ["Žalobce Jan Novák podal žalobu.", "Rozsudek převzal J. Novák dne 3. 4.",
                 "Podle Nováka J. je věc jasná.", "Zástupce: JUDr. K. Bílá, advokátka",
                 "Podepsala M. Dvořáková.", "Svědek P. Horák, nar. 1. 1. 1970, uvedl",
                 "A. Úvod", "Č. Budějovice", "Viz bod B. Smlouvy"]
        self.assertEqual([rules.replace(line)[0] for line in lines], [
            "Žalobce [OSOBA-1] podal žalobu.", "Rozsudek převzal [OSOBA-1] dne 3. 4.",
            "Podle [OSOBA-1] je věc jasná.", "Zástupce: JUDr. [OSOBA-2], advokátka",
            "Podepsala [OSOBA-3].", "Svědek [OSOBA-4], nar. [NAROZENI-1], uvedl",
            "A. Úvod", "Č. Budějovice", "Viz bod B. Smlouvy"])
        # Two known people with the same surname and initial: no guess, a separate label.
        rules = Rules()
        rules.replace("Jan Novák a Josef Novák")
        self.assertEqual(rules.replace("Podepsal J. Novák.")[0], "Podepsal [OSOBA-3].")

    def test_table_columns_by_header(self):
        from anonymizer.formats import redact_text

        rules = Rules()
        data = ("Jméno a příjmení;Dat. nar.;Datum podpisu;E-mail;Poznámka\n"
                "Karolína Bílá;1. 2. 1980;3. 4. 2020;kb;bez poznámky\n"
                "Karolína Bílá;01.02.1980;3. 4. 2020;;\n").encode()
        output = redact_text(data, ".csv", rules)
        lines = output.decode().splitlines()
        self.assertEqual(lines[1], "[OSOBA-1];[NAROZENI-1];3. 4. 2020;[EMAIL-1];bez poznámky")
        self.assertEqual(lines[2], "[OSOBA-1];[NAROZENI-1];3. 4. 2020;;")
        self.assertEqual(redact_text(output, ".csv", rules), output)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_word_image_and_hyperlink(self):
        from io import BytesIO
        from docx.oxml import OxmlElement
        from docx.opc.constants import RELATIONSHIP_TYPE
        from docx.shared import Inches
        from anonymizer.office import redact_office

        source = self.root / "image.docx"
        target = self.root / "clean.docx"
        doc = Document()
        paragraph = doc.add_paragraph("Kontakt ")
        link = OxmlElement("w:hyperlink")
        link.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
                 doc.part.relate_to("mailto:" + self.email, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
        run = paragraph.add_run(self.email)
        run.bold = True
        link.append(run._element)
        paragraph._element.append(link)
        with pdf.open() as sample:
            page = sample.new_page(width=430, height=90)
            page.insert_text((40, 50), self.email, fontsize=22)
            image = doc.add_picture(BytesIO(page.get_pixmap(dpi=240).tobytes("png")), width=Inches(4))
        crop = OxmlElement("a:srcRect")
        crop.set("l", "5000")
        image._inline.graphic.graphicData.pic.blipFill.insert(1, crop)
        doc.save(source)
        redact_office(source, target, Rules(), os.environ["SPISREDAKT_TEST_TESSDATA"])
        output = Document(target)
        self.assertEqual(output.inline_shapes[0].width, Inches(4))
        with ZipFile(target) as archive:
            xml = archive.read("word/document.xml")
            self.assertIn(b'l="5000"', xml)
            self.assertIn(b"[EMAIL-1]", xml)
            self.assertNotIn(self.email.encode(), archive.read("word/_rels/document.xml.rels"))
            media = next(name for name in archive.namelist() if name.startswith("word/media/"))
            pixmap = pdf.Pixmap(archive.read(media))
            colors = pixmap.color_count(colors=True)
            self.assertGreater(colors.get(bytes(3), 0), 7_000)
            self.assertGreater(colors.get(b"\xff\xff\xff", 0), 10_000)
            text, _, _ = recognize_image(pixmap, os.environ["SPISREDAKT_TEST_TESSDATA"])
            self.assertNotIn(self.email, text)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_bmp_ocr_and_mixed_audit(self):
        import json
        from io import BytesIO
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        source, output, audit = (self.root / part for part in ("input", "output", "audit"))
        source.mkdir()
        output.mkdir()
        with pdf.open() as sample:
            page = sample.new_page(width=430, height=90)
            page.insert_text((40, 50), self.email, fontsize=22)
            image = QImage.fromData(page.get_pixmap(dpi=240).tobytes("png"))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, "BMP"))
        doc = Document()
        doc.add_picture(BytesIO(bytes(buffer.data())))
        doc.save(source / "private-name.docx")
        (source / "broken.docx").write_bytes(b"invalid")
        (source / "unknown.bin").write_bytes(b"unknown")
        results = list(process_folders(source, output, os.environ["SPISREDAKT_TEST_TESSDATA"], audit))
        self.assertEqual(sum(target is not None for _, target, _ in results), 1, results)
        target = next(target for _, target, _ in results if target)
        with ZipFile(target) as archive:
            bitmap = QImage.fromData(archive.read("word/media/image1.bmp"))
            self.assertFalse(bitmap.isNull())
            png = QBuffer()
            png.open(QIODevice.OpenModeFlag.WriteOnly)
            self.assertTrue(bitmap.save(png, "PNG"))
            pixels = pdf.Pixmap(bytes(png.data()))
            colors = pixels.color_count(colors=True)
            self.assertGreater(colors.get(bytes(3), 0), 7_000)
            self.assertGreater(colors.get(bytes([255, 255, 255]), 0), 10_000)
            text, _, _ = recognize_image(pixels, os.environ["SPISREDAKT_TEST_TESSDATA"])
            self.assertNotIn(self.email, text)
        log = next(audit.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("private-name", log)
        self.assertNotIn(self.email, log)
        self.assertEqual(json.loads(log.splitlines()[-1])["counts"],
                         {"released": 1, "failed": 1, "skipped": 1})

    def folders(self, name):
        source, target = self.root / name / "in", self.root / name / "out"
        source.mkdir(parents=True)
        target.mkdir()
        return source, target

    def test_batch_rejects_large_text_without_loading_it(self):
        import tracemalloc

        source, target = self.folders("large")
        with (source / "big.txt").open("wb") as f:
            for _ in range(45):
                f.write(b"a" * 1_000_000)
        tracemalloc.start()
        try:
            results = list(process_folders(source, target))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual([r[1:] for r in results], [(None, "Vstup překračuje limit velikosti.")])
        self.assertLess(peak, 8_000_000)
        self.assertEqual(list(target.rglob("*")), [])

    def test_batch_rejects_oversized_sparse_file_fast(self):
        import time
        from unittest import mock

        source, target = self.folders("sparse")
        with (source / "huge.pdf").open("wb") as f:
            f.write(b"%PDF-")
            f.truncate(600_000_000)
        started = time.monotonic()
        with mock.patch("anonymizer.batch.copy_limited") as copy:
            results = list(process_folders(source, target))
        self.assertFalse(copy.called)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual([r[1:] for r in results], [(None, "Vstup překračuje limit velikosti.")])

    def test_batch_skips_unknown_sparse_file_without_copying(self):
        from unittest import mock

        source, target = self.folders("unknown")
        with (source / "data.bin").open("wb") as f:
            f.truncate(300_000_000)
        audit = self.root / "unknown-audit"
        with mock.patch("anonymizer.batch.copy_limited") as copy:
            results = list(process_folders(source, target, audit_root=audit))
        self.assertFalse(copy.called)
        self.assertEqual(results, [])
        self.assertEqual(list(target.rglob("*")), [])
        log = next(audit.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertIn('"skipped"', log)
        self.assertIn("unsupported_format", log)

    def test_identify_path_matches_bytes(self):
        from anonymizer.formats import identify

        docx = self.root / "a.docx"
        doc = Document()
        doc.add_paragraph("text")
        doc.save(docx)
        archive = self.root / "a.zip"
        with ZipFile(archive, "w") as z:
            z.writestr("x.txt", "text")
        for path, expected in ((docx, ".docx"), (archive, ".zip")):
            self.assertEqual(identify(path, ".bin"), expected)
            self.assertEqual(identify(path.read_bytes(), ".bin"), expected)

    def test_replace_output_is_stable(self):
        # The hashes come from the implementation before the scanning was sped up.
        text = synthetic_file()
        self.assertEqual(len(text), 81225)
        replaced, count = Rules().replace(text)
        self.assertEqual(count, 2193)
        self.assertEqual(hashlib.sha256(replaced.encode()).hexdigest(),
                         "00a60cf0ead5d8303899091355997d9e3804a01bf701e1105692bf011b4d0c92")
        # Paragraph by paragraph with one shared Rules, as the Office formats do.
        shared = Rules()
        pieces = [shared.replace(line)[0] for line in text.split("\n")]
        self.assertEqual(hashlib.sha256("\n".join(pieces).encode()).hexdigest(),
                         "1635a96fcd269c2082dfbb865f07a6584616a5e3ee4f39e6541f552c28a78846")
        for kind in ("OSOBA", "ADRESA", "ORGANIZACE", "TELEFON", "EMAIL", "ICO", "SPIS", "RC", "UCET", "IBAN",
                     "DIC", "DOKLAD", "NAROZENI", "OSOBNI_UDAJ"):
            self.assertRegex(replaced, rf"\[{kind}-\d+\]")
        self.assertIn("Jméno: [OSOBA-1]", replaced)
        self.assertIn("21 Cdo", replaced)

    def test_fast_scanning_finds_what_the_patterns_find(self):
        plain = {
            "ORGANIZACE": (czech.company_matches, czech.COMPANY),
            "SLOVAK": (lambda text: czech.colon_lines(czech.SLOVAK_FIELD, text), czech.SLOVAK_FIELD),
            "FORM": (lambda text: czech.colon_lines(czech.FORM_FIELD, text), czech.FORM_FIELD),
            "BIRTH": (lambda text: czech.anchored(czech.BIRTH, czech.BIRTH_START, text), czech.BIRTH),
            "BIRTH_TEXT": (lambda text: czech.anchored(czech.BIRTH_TEXT, czech.BIRTH_START, text), czech.BIRTH_TEXT),
            "ADDRESS": (lambda text: czech.anchored(czech.ADDRESS_CUE, czech.ADDRESS_START, text), czech.ADDRESS_CUE),
            "INITIAL_FIRST": (czech.initial_first_matches, czech.INITIAL_FIRST),
            "INITIAL_LAST": (czech.initial_last_matches, czech.INITIAL_LAST),
        }
        for kind, scan in rules_module.SCANNERS.items():
            plain[kind] = (scan, rules_module.COMPILED[kind])
        found = dict.fromkeys(plain, 0)
        for text in token_strings(4000):
            for kind, (scan, pattern) in plain.items():
                expected = [match.regs for match in pattern.finditer(text)]
                self.assertEqual([match.regs for match in scan(text)], expected, (kind, text))
                found[kind] += len(expected)
        # Every scanner was exercised on real matches.
        self.assertTrue(all(found.values()), found)

    def test_anchor_letters_cover_ignorecase(self):
        # The anchors spell out both cases of a letter; IGNORECASE may add more (ſ, İ, ı).
        every = "".join(map(chr, range(0x110000)))
        for letter in "abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž":
            expected = set(re.compile(re.escape(letter), re.IGNORECASE).findall(every))
            self.assertEqual(set(re.compile(czech.cls(letter)).findall(every)), expected, letter)
        for source in (r"[\w.+-]", r"[\w-]", r"\d", r"\w"):
            self.assertEqual(set(re.compile(source, re.IGNORECASE).findall(every)),
                             set(re.compile(source).findall(every)), source)


class AdviceTests(unittest.TestCase):
    def test_every_reason_has_advice(self):
        from anonymizer.reasons import ADVICE, REASONS
        for _, code in REASONS:
            self.assertIn(code, ADVICE)
        for code, text in ADVICE.items():
            self.assertIsInstance(text, str)
            self.assertTrue(text.strip(), code)

    def test_every_format_error_has_specific_advice(self):
        import re
        from anonymizer.reasons import ADVICE, advice
        package = Path(__file__).resolve().parent.parent / "anonymizer"
        raised = set()
        for module in package.glob("*.py"):
            raised.update(re.findall(r'FormatError\("(\w+)", "([^"]+)"\)', module.read_text(encoding="utf-8")))
        self.assertGreater(len(raised), 10)
        for code, message in raised:
            self.assertEqual(advice(message), ADVICE[code], code)

    def test_advice_for_messages(self):
        from anonymizer.reasons import ADVICE, GENERAL_ADVICE, PERMISSION_ADVICE, advice
        self.assertEqual(advice("Soubor načítá externí data."), ADVICE["office_external_link"])
        self.assertEqual(advice("Vstup překračuje limit velikosti."), ADVICE["size_limit"])
        self.assertEqual(advice("Obsah neodpovídá deklarovanému formátu."), ADVICE["invalid_signature"])
        self.assertEqual(advice("Výstup Office nelze přečíst."), ADVICE["corrupt_output"])
        self.assertEqual(advice("PermissionError"), PERMISSION_ADVICE)
        self.assertEqual(advice("Něco neznámého"), GENERAL_ADVICE)

    def test_gui_worker_emits_advice(self):
        from PySide6.QtWidgets import QApplication
        from anonymizer.gui import BatchWorker
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, output, audit = root / "in", root / "out", root / "audit"
            source.mkdir()
            output.mkdir()
            (source / "broken.docx").write_bytes(b"invalid")
            (source / "ok.txt").write_text("Běžný text bez údajů.", encoding="utf-8")
            selected = set(source.iterdir())
            worker = BatchWorker(source, output, None, None, selected)
            lines = []
            worker.result.connect(lines.append)
            previous = os.environ.get("SPISREDAKT_AUDIT_DIR")
            os.environ["SPISREDAKT_AUDIT_DIR"] = str(audit)
            try:
                worker.run()
            finally:
                if previous is None:
                    del os.environ["SPISREDAKT_AUDIT_DIR"]
                else:
                    os.environ["SPISREDAKT_AUDIT_DIR"] = previous
        index = next(i for i, line in enumerate(lines) if "Chyba:" in line)
        self.assertTrue(lines[index + 1].startswith("    Rada: "), lines)
        for line in lines:
            if line.startswith("    Rada: "):
                self.assertNotIn("broken", line)


if __name__ == "__main__":
    unittest.main()
