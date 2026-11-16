import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

from anonymizer.batch import process_batch, process_folders
from anonymizer.documents import ocr_path, recognize_image
from anonymizer.rules import Rules


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


if __name__ == "__main__":
    unittest.main()
