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
