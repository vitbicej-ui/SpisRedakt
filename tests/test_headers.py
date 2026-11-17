"""Column headings split over several paragraphs or lines are still recognised."""
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from docx import Document

from anonymizer import czech, markdown
from anonymizer.extract import Block, Document as Extracted, Page
from anonymizer.rules import Rules

NAME = "Vojmír Pštross"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


class ColumnKindTests(unittest.TestCase):
    def test_line_breaks_are_normalised(self):
        for header in ("Jméno\na příjmení", "Jméno\r\na příjmení", "Jméno a\tpříjmení", "Jméno\n\na příjmení",
                       "Jméno a\npříjmení:", "Jméno\va příjmení"):
            self.assertEqual(czech.column_kind(header), "OSOBA", repr(header))
        self.assertEqual(czech.column_kind("Datum\nnarození"), "NAROZENI")
        self.assertEqual(czech.column_kind("Datum naro-\nzení"), "NAROZENI")
        self.assertEqual(czech.column_kind("Rodné\nčíslo"), "RC")

    def test_incomplete_match_is_not_a_heading(self):
        self.assertIsNone(czech.column_kind("Poznámka\nk osobě"))
        self.assertIsNone(czech.column_kind("Jméno\na příjmení svědka"))

    def test_plan_table_uses_multiline_headers(self):
        rows = [["Jméno\na příjmení", "Datum\nnarození", "Poznámka\nk osobě"], [NAME, "1. 2. 1980", NAME]]
        plan = czech.plan_table([list(enumerate(row)) for row in rows])
        self.assertEqual(plan, {(1, 0): "OSOBA", (1, 1): "NAROZENI"})


class HeaderFormatTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def test_docx_paragraphs_and_line_breaks(self):
        from anonymizer.office import redact_office

        document = Document()
        table = document.add_table(rows=2, cols=3)
        first = table.cell(0, 0)
        first.text = "Jméno"
        first.add_paragraph("a příjmení")
        second = table.cell(0, 1)
        second.text = "Datum"
        second.add_paragraph("")
        second.add_paragraph("narození")
        third = table.cell(0, 2)
        third.paragraphs[0].add_run("Jméno")
        third.paragraphs[0].add_run().add_break()
        third.paragraphs[0].add_run("a příjmení")
        table.cell(1, 0).text = NAME
        table.cell(1, 1).text = "1. 2. 1980"
        table.cell(1, 2).text = "Alois Vomáčka"
        source, target = self.root / "a.docx", self.root / "a_out.docx"
        document.save(source)
        redact_office(source, target, Rules())
        result = Document(target).tables[0]
        self.assertEqual([cell.text for cell in result.rows[1].cells], ["[OSOBA-1]", "[NAROZENI-1]", "[OSOBA-2]"])
        self.assertEqual(result.cell(0, 0).text, "Jméno\na příjmení")

    def test_docx_note_column_is_not_a_heading(self):
        from anonymizer.office import redact_office

        document = Document()
        table = document.add_table(rows=2, cols=1)
        table.cell(0, 0).text = "Poznámka"
        table.cell(0, 0).add_paragraph("k osobě")
        table.cell(1, 0).text = "bez poznámky"
        source, target = self.root / "n.docx", self.root / "n_out.docx"
        document.save(source)
        redact_office(source, target, Rules())
        self.assertEqual(Document(target).tables[0].cell(1, 0).text, "bez poznámky")

    def test_odt(self):
        from anonymizer.formats import redact_file

        def cell(*paragraphs):
            body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
            return f'<table:table-cell office:value-type="string">{body}</table:table-cell>'

        namespaces = f'xmlns:office="{OFFICE}" xmlns:text="{TEXT}" xmlns:table="{TABLE}"'
        content = (f'<office:document-content {namespaces}><office:body><office:text><table:table>'
                   '<table:table-row>' + cell("Jméno", "a příjmení") + cell("Datum", "narození")
                   + cell("Poznámka", "k osobě") + '</table:table-row><table:table-row>'
                   + cell(NAME) + cell("1. 2. 1980") + cell("Bez změny") + '</table:table-row>'
                   '</table:table></office:text></office:body></office:document-content>')
        source, target = self.root / "t.odt", self.root / "t_out.odt"
        with ZipFile(source, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("content.xml", content)
            archive.writestr("styles.xml", f'<office:document-styles {namespaces}/>')
            archive.writestr("META-INF/manifest.xml",
                             '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                             '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                             '</manifest:manifest>')
        redact_file(source, target, ".odt", Rules(), None)
        with ZipFile(target) as archive:
            result = archive.read("content.xml").decode()
        self.assertNotIn("Pštross", result)
        self.assertIn("[OSOBA-1]", result)
        self.assertIn("[NAROZENI-1]", result)
        self.assertIn("Bez změny", result)

    def test_rtf(self):
        from anonymizer.rtf import redact_rtf

        def row(*cells):
            body = rb"\trowd\cellx2000\cellx4000\cellx6000" b"\n"
            for item in cells:
                body += rb"\pard\intbl " + item + rb"\cell" + b"\n"
            return body + rb"\row" + b"\n"

        head = rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
        data = (head + row(rb"Jm\'e9no\par a p\'f8\'edjmen\'ed", rb"Datum\par narozen\'ed", rb"Pozn\'e1mka\par k osob\u283?\'ec")
                + row(rb"Vojm\'edr P\'9atross", rb"1. 2. 1980", rb"Bez zm\'ecny") + rb"\pard\par}")
        output = redact_rtf(data, Rules())
        self.assertNotIn(b"Vojm", output)
        self.assertNotIn(b"tross", output)
        self.assertIn(b"[OSOBA-1]", output)
        self.assertIn(b"[NAROZENI-1]", output)
        self.assertIn(b"Bez zm", output)

    def test_xlsx_cell_with_line_break(self):
        from anonymizer.office import SHEET, redact_office

        shared = ["Jméno\na příjmení", "Datum\nnarození", "Poznámka\nk osobě", NAME, "Bez změny"]
        rows = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2" t="inlineStr"><is><t>1. 2. 1980</t></is></c>'
                '<c r="C2" t="s"><v>4</v></c></row>')
        source, target = self.root / "t.xlsx", self.root / "t_out.xlsx"
        with ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{SHEET}"><workbookPr/></workbook>')
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{SHEET}">' + "".join(
                f"<si><t>{value}</t></si>" for value in shared) + "</sst>")
            archive.writestr("xl/worksheets/sheet1.xml",
                             f'<worksheet xmlns="{SHEET}"><sheetData>{rows}</sheetData></worksheet>')
        redact_office(source, target, Rules())
        with ZipFile(target) as archive:
            strings = archive.read("xl/sharedStrings.xml").decode()
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("Pštross", strings)
        self.assertIn("[OSOBA-1]", strings)
        self.assertIn("[NAROZENI-1]", sheet)
        self.assertIn("Bez změny", strings)

    def test_doc_converted_from_html(self):
        from anonymizer.formats import redact_file

        soffice = shutil.which("soffice")
        if not soffice:
            self.skipTest("LibreOffice není k dispozici.")
        html = ("<html><body><table border=1><tr><td>Jméno<br>a příjmení</td><td><p>Datum</p><p>narození</p></td>"
                "<td>Poznámka<br>k osobě</td></tr>"
                f"<tr><td>{NAME}</td><td>1. 2. 1980</td><td>Bez změny</td></tr></table></body></html>")
        source = self.root / "t.html"
        source.write_text(html, encoding="utf-8")
        profile = self.root / "profile"
        subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to",
                        "doc:MS Word 97", "--outdir", str(self.root), str(source)],
                       check=True, capture_output=True, timeout=180)
        doc = self.root / "t.doc"
        if not doc.exists():
            self.skipTest("LibreOffice nevytvořil soubor DOC.")
        target = self.root / "t_out.docx"
        redact_file(doc, target, ".doc", Rules(), None)
        cells = [[cell.text for cell in row.cells] for row in Document(target).tables[0].rows]
        self.assertEqual(cells[1], ["[OSOBA-1]", "[NAROZENI-1]", "Bez změny"])

    def test_doc_built_from_paragraph_properties(self):
        from anonymizer.formats import redact_file
        from tests.test_processing import ProcessingTests

        helper = ProcessingTests("word_file")
        T, ROW = (1, 0, 1), (1, 1, 1)
        grid = ("Jméno\ra příjmení\x07Datum\rnarození\x07Poznámka\rk osobě\x07\x07"
                f"{NAME}\x071. 2. 1980\x07Bez změny\x07\x07")
        props = [T, T, T, T, T, T, ROW, T, T, T, ROW]
        source, target = self.root / "p.doc", self.root / "p_out.docx"
        source.write_bytes(helper.word_file(grid, "", props))
        redact_file(source, target, ".doc", Rules(), None)
        cells = [[cell.text for cell in row.cells] for row in Document(target).tables[0].rows]
        self.assertEqual(cells[1], ["[OSOBA-1]", "[NAROZENI-1]", "Bez změny"])

    def test_markdown_table(self):
        rows = [["Jméno\na příjmení", "Poznámka\nk osobě"], [NAME, "bez poznámky"]]
        document = Extracted(1, "DOCX", [Page(1, [Block("table", rows=rows)])], [], [])
        rules = Rules()
        markdown.collect([document], rules)
        output = markdown.short_labels(markdown.render([document], rules).text)
        self.assertNotIn("Pštross", output)
        self.assertIn("[O", output)
        self.assertIn("bez poznámky", output)


if __name__ == "__main__":
    unittest.main()
