import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

from anonymizer.batch import process_batch, process_folders
from anonymizer.documents import ocr_path
from anonymizer.rules import Rules


class ProcessingTests(unittest.TestCase):
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
        (source / "ignore.txt").write_text("ignored")
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
        self.assertTrue(all(target is None for _, target, _ in process_folders(source, destination)))
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


if __name__ == "__main__":
    unittest.main()
