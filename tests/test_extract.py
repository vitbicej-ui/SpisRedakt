import io
import os
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

import pymupdf as pdf

from anonymizer import extract
from anonymizer.extract import Block, Document, Skipped, clean_ocr

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' \
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" ' \
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" ' \
      'xmlns:v="urn:schemas-microsoft-com:vml"'


def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in entries:
            target.writestr(name, content)
    return buffer.getvalue()


def run(run_text):
    return f"<w:r><w:t>{run_text}</w:t></w:r>"


def paragraph(*runs):
    return "<w:p>" + "".join(runs) + "</w:p>"


def docx_bytes():
    body = "".join([
        paragraph(run("Viditelný odstavec"),
                  "<w:del><w:r><w:delText>smazaný text</w:delText></w:r></w:del>",
                  '<w:r><w:rPr><w:vanish/></w:rPr><w:t>skrytý text</w:t></w:r>',
                  '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                  '<w:r><w:instrText> HYPERLINK "kod-pole" </w:instrText></w:r>'
                  '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + run("výsledek pole") +
                  '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
                  '<w:commentRangeStart w:id="0"/>', run(" za komentářem"), '<w:commentRangeEnd w:id="0"/>',
                  '<w:r><w:commentReference w:id="0"/></w:r>'),
        "<w:tbl><w:tr><w:tc>" + paragraph(run("A1")) + "</w:tc><w:tc>" + paragraph(run("B1"), ) +
        "<w:tbl><w:tr><w:tc>" + paragraph(run("vnořená")) + "</w:tc></w:tr></w:tbl>"
        "</w:tc></w:tr><w:tr><w:tc>" + paragraph(run("A2")) + paragraph(run("A2 druhý")) +
        "</w:tc><w:tc>" + paragraph(run("B2")) + "</w:tc></w:tr></w:tbl>",
        paragraph('<mc:AlternateContent><mc:Choice Requires="wps"><w:r><w:drawing><wps:txbx><w:txbxContent>'
                  + paragraph(run("Textové pole")) + '</w:txbxContent></wps:txbx></w:drawing></w:r></mc:Choice>'
                  '<mc:Fallback><w:r><w:pict><v:textbox><w:txbxContent>' + paragraph(run("Textové pole")) +
                  '</w:txbxContent></v:textbox></w:pict></w:r></mc:Fallback></mc:AlternateContent>'),
        paragraph('<w:r><w:footnoteReference w:id="2"/></w:r>', run("Konec")),
    ])
    footnotes = (f'<w:footnotes {WNS}><w:footnote w:type="separator" w:id="-1">' + paragraph(run("---")) +
                 '</w:footnote><w:footnote w:id="2">' + paragraph(run("Poznámka pod čarou")) +
                 "</w:footnote></w:footnotes>")
    return archive([
        ("[Content_Types].xml", "<Types/>"),
        ("word/document.xml", f'<w:document {WNS}><w:body>{body}</w:body></w:document>'),
        ("word/footnotes.xml", footnotes),
        ("word/header1.xml", f"<w:hdr {WNS}>" + paragraph(run("Záhlaví")) + "</w:hdr>"),
        ("word/header2.xml", f"<w:hdr {WNS}>" + paragraph(run("Záhlaví")) + "</w:hdr>"),
        ("word/footer1.xml", f"<w:ftr {WNS}>" + paragraph(run("Zápatí")) + "</w:ftr>"),
        ("word/comments.xml", f"<w:comments {WNS}><w:comment w:id='0'>" + paragraph(run("text komentáře")) +
         "</w:comment></w:comments>"),
        ("docProps/core.xml", "<cp><dc:creator>Autor Metadat</dc:creator></cp>"),
        ("customXml/item1.xml", "<x>vlastní xml</x>"),
    ])


def all_text(document):
    parts = list(document.headers) + list(document.footers)
    for page in document.pages:
        for block in page.blocks:
            parts.append(block.text)
            for row in block.rows or ():
                parts.extend(row)
    return "\n".join(parts)


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, name, data):
        path = Path(self.folder.name) / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def one(self, name, data, **options):
        items = list(extract.walk(self.write(name, data), **options))
        self.assertEqual(len(items), 1, items)
        self.assertIsInstance(items[0], Document, items[0])
        return items[0]

    def blocks(self, document, kind=None):
        return [b for page in document.pages for b in page.blocks if kind in (None, b.kind)]


class CleanOcr(unittest.TestCase):
    def test_hyphen_join(self):
        self.assertEqual(clean_ocr("Ondráč-\nková přišla"), "Ondráčková přišla")
        self.assertEqual(clean_ocr("bez - \nmezery"), "bez - mezery")
        self.assertEqual(clean_ocr("Novák-\nNovotný"), "Novák- Novotný")

    def test_paragraphs(self):
        self.assertEqual(clean_ocr("první řádek\ndruhý řádek\n\nnový odstavec"),
                         "první řádek druhý řádek\n\nnový odstavec")

    def test_noise_and_phone(self):
        text = "Text věty\n~ . ' ,\n--\nTel.: +420 777 123 456\n|||| ||"
        self.assertEqual(clean_ocr(text), "Text věty Tel.: +420 777 123 456")

    def test_spaces(self):
        self.assertEqual(clean_ocr("  a    b   c  "), "a b c")

    def test_text_layer_keeps_lines(self):
        self.assertEqual(clean_ocr("Jan Ondráč-\nková\nUlice 1", text_layer=True), "Jan Ondráčková\nUlice 1")


class Office(Base):
    def test_docx(self):
        document = self.one("a.docx", docx_bytes())
        text = all_text(document)
        for expected in ("Viditelný odstavec", "výsledek pole", "za komentářem", "Textové pole", "Konec"):
            self.assertIn(expected, text)
        for hidden in ("smazaný", "skrytý", "kod-pole", "HYPERLINK", "text komentáře", "Autor Metadat", "vlastní xml"):
            self.assertNotIn(hidden, text)
        self.assertEqual(text.count("Textové pole"), 1)
        table = self.blocks(document, "table")[0]
        self.assertEqual(table.rows, [["A1", "B1\nvnořená"], ["A2\nA2 druhý", "B2"]])
        self.assertEqual([b.text for b in self.blocks(document, "note")], ["Poznámka pod čarou"])
        self.assertEqual(document.headers, ["Záhlaví"])
        self.assertEqual(document.footers, ["Zápatí"])
        self.assertEqual(document.format, "DOCX")
        self.assertIsNone(document.parent)

    def test_xlsx(self):
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' \
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        sheet1 = (f'<worksheet {ns}><sheetData>'
                  '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>inline</t></is></c></row>'
                  '<row r="2" hidden="1"><c r="A2"><v>12</v></c><c r="B2" s="1"><v>43905</v></c>'
                  '<c r="C2" t="b"><v>1</v></c></row></sheetData>'
                  '<headerFooter><oddHeader>&amp;L&amp;"Arial,Bold"Hlavička&amp;P</oddHeader></headerFooter></worksheet>')
        sheet2 = f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>1</v></c></row></sheetData></worksheet>'
        data = archive([
            ("[Content_Types].xml", "<Types/>"),
            ("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="Viditelný" sheetId="1" r:id="rId1"/>'
                                '<sheet name="Skrytý" sheetId="2" state="hidden" r:id="rId2"/></sheets></workbook>'),
            ("xl/_rels/workbook.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/>'
             '<Relationship Id="rId2" Type="x/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'),
            ("xl/sharedStrings.xml", f'<sst {ns}><si><t>Sdílený</t></si><si><t>Ve skrytém listu</t></si></sst>'),
            ("xl/styles.xml", f'<styleSheet {ns}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'),
            ("xl/worksheets/sheet1.xml", sheet1),
            ("xl/worksheets/sheet2.xml", sheet2),
        ])
        document = self.one("a.xlsx", data)
        blocks = self.blocks(document)
        self.assertEqual([(b.kind, b.text or b.rows) for b in blocks],
                         [("p", "Viditelný"), ("table", [["Sdílený", "", "inline"], ["12", "15. 3. 2020", "PRAVDA"]]),
                          ("p", "Skrytý"), ("table", [["Ve skrytém listu"]])])
        self.assertEqual(document.headers, ["Hlavička"])

    def test_pptx(self):
        ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
             'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' \
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

        def slide(text):
            return (f'<p:sld {ns}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>'
                    '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')

        data = archive([
            ("[Content_Types].xml", "<Types/>"),
            ("ppt/presentation.xml", f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                                     '<p:sldId id="257" r:id="rId2"/></p:sldIdLst></p:presentation>'),
            ("ppt/_rels/presentation.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="x/slide" Target="slides/slide1.xml"/>'
             '<Relationship Id="rId2" Type="x/slide" Target="slides/slide2.xml"/></Relationships>'),
            ("ppt/slides/slide1.xml", slide("První snímek")),
            ("ppt/slides/slide2.xml", slide("Druhý snímek")),
            ("ppt/slides/_rels/slide2.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="x/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>'),
            ("ppt/notesSlides/notesSlide1.xml", slide("Poznámka řečníka")),
            ("ppt/comments/comment1.xml", "<c>komentář</c>"),
        ])
        document = self.one("a.pptx", data)
        self.assertEqual([(p.number, [(b.kind, b.text) for b in p.blocks]) for p in document.pages],
                         [(1, [("p", "První snímek")]), (2, [("p", "Druhý snímek"), ("note", "Poznámka řečníka")])])

    def test_odt(self):
        ns = 'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" ' \
             'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" ' \
             'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" ' \
             'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" ' \
             'xmlns:dc="http://purl.org/dc/elements/1.1/"'
        content = (f'<office:document-content {ns}><office:body><office:text>'
                   '<text:h>Nadpis</text:h>'
                   '<text:p>Text<text:s/>s mezerou<office:annotation><text:p>anotace</text:p></office:annotation>'
                   '<text:note><text:note-body><text:p>Vysvětlivka</text:p></text:note-body></text:note></text:p>'
                   '<text:tracked-changes><text:changed-region><text:deletion><text:p>smazáno</text:p></text:deletion>'
                   '</text:changed-region></text:tracked-changes>'
                   '<text:list><text:list-item><text:p>Položka</text:p></text:list-item></text:list>'
                   '<table:table><table:table-row><table:table-cell><text:p>x</text:p></table:table-cell>'
                   '<table:table-cell><text:p>y</text:p></table:table-cell></table:table-row></table:table>'
                   '</office:text></office:body></office:document-content>')
        styles = (f'<office:document-styles {ns}><office:master-styles><style:master-page>'
                  '<style:header><text:p>Horní</text:p></style:header><style:footer><text:p>Dolní</text:p>'
                  '</style:footer></style:master-page></office:master-styles></office:document-styles>')
        data = archive([("mimetype", "application/vnd.oasis.opendocument.text"), ("content.xml", content),
                        ("styles.xml", styles), ("meta.xml", "<m><dc:creator>Tvůrce</dc:creator></m>")])
        document = self.one("a.odt", data)
        text = all_text(document)
        self.assertNotIn("anotace", text)
        self.assertNotIn("smazáno", text)
        self.assertNotIn("Tvůrce", text)
        self.assertEqual([(b.kind, b.text or b.rows) for b in self.blocks(document)],
                         [("p", "Nadpis"), ("p", "Text s mezerou"), ("note", "Vysvětlivka"), ("p", "Položka"),
                          ("table", [["x", "y"]])])
        self.assertEqual((document.headers, document.footers, document.format), (["Horní"], ["Dolní"], "ODT"))

    def test_rtf(self):
        data = (rb"{\rtf1\ansi\ansicpg1250{\fonttbl{\f0 Arial;}}{\info{\author Autor}}"
                rb"{\header Hlavi\'e8ka}\pard Prvn\'ed odstavec\par{\*\hidden skryt\'fd}"
                rb"{\annotation{\*\atnid X}Koment\'e1\'f8}\pard\intbl A\cell B\cell\row\pard Konec\par}")
        document = self.one("a.rtf", data)
        text = all_text(document)
        self.assertIn("První odstavec", text)
        self.assertIn("Konec", text)
        self.assertNotIn("skrytý", text)
        self.assertNotIn("Komentář", text)
        self.assertNotIn("Autor", text)
        self.assertEqual(document.headers, ["Hlavička"])
        self.assertEqual(self.blocks(document, "table")[0].rows, [["A", "B"]])

    def test_text_formats(self):
        text = self.one("a.txt", "první\nřádek\n\ndruhý odstavec".encode("cp1250"))
        self.assertEqual([b.text for b in self.blocks(text)], ["první\nřádek", "druhý odstavec"])
        table = self.one("a.csv", "a;b\n1;2\n\n")
        self.assertEqual(self.blocks(table)[0].rows, [["a", "b"], ["1", "2"]])
        self.assertIn('"k": "v"', self.one("a.json", '{"k": "v"}').pages[0].blocks[0].text)
        self.assertEqual([b.text for b in self.blocks(self.one("a.xml", "<a>x<b>y</b></a>"))], ["x", "y"])
        items = list(extract.walk(self.write("b.xml", '<!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>')))
        self.assertEqual([(type(i), i.code) for i in items], [(Skipped, "xml_dtd")])

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_docx_image_ocr(self):
        png = scan_pixmap().tobytes("png")
        data = archive([
            ("[Content_Types].xml", "<Types/>"),
            ("word/document.xml", f'<w:document {WNS}><w:body><w:p><w:r><w:drawing><a:blip '
                                  'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" r:embed="rId5"/>'
                                  '</w:drawing></w:r></w:p></w:body></w:document>'),
            ("word/_rels/document.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId5" Type="x/image" Target="media/image1.png"/></Relationships>'),
            ("word/media/image1.png", png)])
        block = self.blocks(self.one("a.docx", data, tessdata=TESSDATA), "image")[0]
        self.assertTrue(block.ocr)
        self.assertIn("Novák", block.text)


def scan_pixmap():
    with pdf.open() as document:
        page = document.new_page(width=400, height=120)
        page.insert_text((20, 60), "Jan Novák bydlí v Praze", fontsize=22)
        return page.get_pixmap(dpi=200, alpha=False)


def qt_image(pixmap, fmt):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    image = QImage.fromData(pixmap.tobytes("png"), "PNG")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(buffer.data())


def tiff_bytes(pixmaps):
    """Uncompressed multi-page greyscale TIFF without extra dependencies."""
    import struct

    data = bytearray(b"II*\0\0\0\0\0")
    previous = 4
    for pixmap in pixmaps:
        pixmap = pdf.Pixmap(pdf.csGRAY, pixmap)
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
    return bytes(data)


def text_pdf(*pages):
    document = pdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


class Pdf(Base):
    def test_text_layer_and_blank_page(self):
        document = self.one("a.pdf", text_pdf("Tohle je první strana dokumentu", "", "Druhá strana je také textová"))
        self.assertEqual([p.number for p in document.pages], [1, 2, 3])
        self.assertIn("první strana", document.pages[0].blocks[0].text)
        self.assertFalse(document.pages[0].blocks[0].ocr)
        self.assertEqual(document.pages[1].blocks, [])
        self.assertIn("Druhá strana", document.pages[2].blocks[0].text)

    def test_slovak_register_withheld(self):
        items = list(extract.walk(self.write("a.pdf", text_pdf("Výpis z Registra trestov Slovenskej republiky"))))
        self.assertEqual([(type(i), i.code) for i in items], [(Skipped, "withheld_slovak_register")])

    def test_encrypted_pdf(self):
        document = pdf.open()
        document.new_page().insert_text((72, 100), "Tajný dokument je zašifrovaný")
        data = document.tobytes(encryption=pdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b")
        items = list(extract.walk(self.write("a.pdf", data)))
        self.assertEqual([(type(i), i.code) for i in items], [(Skipped, "pdf_encrypted_or_empty")])

    def test_scan_needs_ocr_data(self):
        data = scanned_pdf()
        items = list(extract.walk(self.write("a.pdf", data), tessdata=None))
        self.assertEqual([(type(i), i.code) for i in items], [(Skipped, "ocr_data_missing")])

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_scanned_page_ocr(self):
        document = self.one("a.pdf", scanned_pdf(), tessdata=TESSDATA)
        blocks = document.pages[0].blocks
        self.assertTrue(blocks and all(b.ocr for b in blocks))
        self.assertIn("Novák", " ".join(b.text for b in blocks))

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_gray_rgba_and_gray_tiff_images_are_read(self):
        rgb = scan_pixmap()
        gray = pdf.Pixmap(pdf.csGRAY, rgb)
        rgba = pdf.Pixmap(rgb, 1)
        for name, pixmap in (("g.png", gray), ("a.png", rgba)):
            document = self.one(name, pixmap.tobytes("png"), tessdata=TESSDATA)
            self.assertIn("Novák", document.pages[0].blocks[0].text, name)
        tiff = self.one("g.tif", tiff_bytes([gray]), tessdata=TESSDATA)
        self.assertIn("Novák", tiff.pages[0].blocks[0].text)
        # a gray picture embedded in a DOCX
        data = archive([
            ("[Content_Types].xml", "<Types/>"),
            ("word/document.xml", f'<w:document {WNS}><w:body>' + paragraph(
                '<w:r><w:drawing><a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'r:embed="rId5"/></w:drawing></w:r>') + '</w:body></w:document>'),
            ("word/_rels/document.xml.rels",
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId5" Type="x/image" Target="media/image1.png"/></Relationships>'),
            ("word/media/image1.png", gray.tobytes("png"))])
        block = self.blocks(self.one("a.docx", data, tessdata=TESSDATA), "image")[0]
        self.assertIn("Novák", block.text)

    def test_pixmap_is_rgb_without_alpha(self):
        rgb = scan_pixmap()
        for source in (pdf.Pixmap(pdf.csGRAY, rgb), pdf.Pixmap(rgb, 1), rgb):
            pix = extract._pixmap(source.tobytes("png"))
            self.assertEqual((pix.colorspace.name, pix.alpha), ("DeviceRGB", 0))

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_png_and_tiff(self):
        pixmap = scan_pixmap()
        png = self.one("a.png", pixmap.tobytes("png"), tessdata=TESSDATA)
        self.assertIn("Novák", png.pages[0].blocks[0].text)
        self.assertEqual(png.format, "PNG")
        blank = pdf.Pixmap(pixmap.colorspace, pixmap.irect, False)
        blank.clear_with(255)
        tiff = self.one("a.tif", tiff_bytes([pixmap, blank]), tessdata=TESSDATA)
        self.assertEqual(tiff.format, "TIFF")
        self.assertIn("Novák", tiff.pages[0].blocks[0].text)
        self.assertTrue(tiff.pages[0].blocks[0].ocr)
        self.assertEqual([p.number for p in tiff.pages], [1, 2])
        self.assertEqual(tiff.pages[1].blocks, [])
        bmp = self.one("a.bmp", qt_image(pixmap, "BMP"), tessdata=TESSDATA)
        self.assertIn("Novák", bmp.pages[0].blocks[0].text)

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_text_page_with_images(self):
        def build(image_text):
            with pdf.open() as document:
                page = document.new_page()
                page.insert_text((72, 80), "Úvodní text strany s textovou vrstvou", fontsize=12)
                with pdf.open() as inner:
                    small = inner.new_page(width=400, height=120)
                    small.insert_text((20, 60), image_text, fontsize=22)
                    pixmap = small.get_pixmap(dpi=200, alpha=False)
                page.insert_image(pdf.Rect(72, 200, 472, 320), pixmap=pixmap)
                return document.tobytes()

        document = self.one("a.pdf", build("Jan Novák bydlí v Praze"), tessdata=TESSDATA)
        blocks = document.pages[0].blocks
        self.assertEqual([b.ocr for b in blocks], [False, True])
        self.assertIn("Novák", blocks[1].text)
        # An image that only repeats the text layer is not extracted twice.
        with pdf.open() as document:
            with pdf.open() as inner:
                small = inner.new_page(width=600, height=120)
                small.insert_text((20, 60), "Jan Novák bydlí v Praze v ulici", fontsize=22)
                pixmap = small.get_pixmap(dpi=200, alpha=False)
            page = document.new_page(width=600, height=120)
            page.insert_image(page.rect, pixmap=pixmap)
            page.insert_text((20, 60), "Jan Novák bydlí v Praze v ulici", fontsize=22, render_mode=3)
            data = document.tobytes()
        repeated = self.one("b.pdf", data, tessdata=TESSDATA)
        self.assertEqual(len(repeated.pages[0].blocks), 1)
        self.assertFalse(repeated.pages[0].blocks[0].ocr)


def scanned_pdf():
    with pdf.open() as document:
        page = document.new_page(width=400, height=120)
        page.insert_image(page.rect, pixmap=scan_pixmap())
        return document.tobytes()


class Legacy(Base):
    def test_doc_xls_msg_use_existing_converters(self):
        message = EmailMessage()
        message["Subject"] = "Zpráva z MSG"
        message.set_content("tělo zprávy")
        sheet = archive([("[Content_Types].xml", "<Types/>"), ("xl/workbook.xml",
                         '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')])
        with mock.patch("anonymizer.extract.identify", side_effect=[".doc", ".xls", ".msg"]), \
                mock.patch("anonymizer.legacy.doc_to_docx", return_value=docx_bytes()), \
                mock.patch("anonymizer.legacy.xls_to_xlsx", return_value=sheet), \
                mock.patch("anonymizer.mail.msg_to_eml", return_value=message):
            doc = extract.extract_file(self.write("a.doc", b"x"), ".doc")[0]
            xls = extract.extract_file(self.write("a.xls", b"x"), ".xls")[0]
            msg = extract.extract_file(self.write("a.msg", b"x"), ".msg")[0]
        self.assertEqual((doc.format, xls.format, msg.format), ("DOC", "XLS", "MSG"))
        self.assertIn("Viditelný odstavec", all_text(doc))
        self.assertIn("tělo zprávy", all_text(msg))
        self.assertIn("Předmět: Zpráva z MSG", all_text(msg))


class Mail(Base):
    def message(self, attach=None, html=True):
        message = EmailMessage()
        message["From"] = "Jan Novák <jan@example.invalid>"
        message["To"] = "Eva Nováková <eva@example.invalid>"
        message["Subject"] = "Věc k řešení"
        message["Date"] = "Tue, 17 Mar 2020 10:30:00 +0100"
        message["Message-ID"] = "<tajne-id@example.invalid>"
        message["Received"] = "from tajny-server.example.invalid"
        message.set_content("prostý text")
        if html:
            message.add_alternative("<html><head><title>t</title><style>p{}</style></head><body><p>První</p>"
                                    "<script>x()</script><div>Druhý<br>řádek</div>"
                                    "<blockquote>Citace</blockquote></body></html>", subtype="html")
        if attach:
            message.add_attachment(attach, maintype="application", subtype="pdf", filename="tajny-nazev.pdf")
        return message

    def test_eml_with_attachment(self):
        raw = self.message(text_pdf("Příloha obsahuje textovou vrstvu"), html=True).as_bytes()
        path = self.write("zprava.eml", raw)
        items = list(extract.walk(path))
        self.assertEqual([type(i) for i in items], [Document, Document])
        mail, attachment = items
        self.assertEqual((mail.format, mail.index, mail.parent, mail.date), ("EML", 1, None, "2020-03-17"))
        header = mail.pages[0].blocks[0]
        self.assertEqual(header.kind, "mailheader")
        self.assertIn("Od: Jan Novák <jan@example.invalid>", header.text)
        self.assertIn("Komu: Eva Nováková", header.text)
        self.assertIn("Předmět: Věc k řešení", header.text)
        self.assertIn("Datum: 17. 3. 2020 10:30", header.text)
        text = all_text(mail)
        self.assertIn("prostý text", text)          # text/plain is preferred over HTML
        for secret in ("tajne-id", "tajny-server", "tajny-nazev"):
            self.assertNotIn(secret, text + repr(items))
        self.assertEqual((attachment.format, attachment.index, attachment.parent), ("PDF", 2, 1))
        self.assertIn("textovou vrstvu", all_text(attachment))

    def test_html_body(self):
        message = EmailMessage()
        message["From"] = "a@example.invalid"
        message.set_content("<html><body><p>První</p><script>x()</script><div>Druhý</div>"
                            "<blockquote>Citace</blockquote></body></html>", subtype="html")
        mail = self.one("a.eml", message.as_bytes())
        self.assertEqual([b.text for b in self.blocks(mail, "p")], ["První", "Druhý", "> Citace"])

    def test_plain_body_keeps_quotes(self):
        message = EmailMessage()
        message["From"] = "a@example.invalid"
        message.set_content("Odpověď\n\n> citovaný řádek\n> další")
        mail = self.one("a.eml", message.as_bytes())
        self.assertEqual([b.text for b in self.blocks(mail, "p")], ["Odpověď", "> citovaný řádek\n> další"])

    def test_nested_zip_attachment(self):
        message = self.message(archive([("a.txt", "v archivu v příloze")]), html=False)
        items = list(extract.walk(self.write("a.eml", message.as_bytes())))
        self.assertEqual([(type(i), i.index, i.parent) for i in items], [(Document, 1, None), (Document, 2, 1)])
        self.assertEqual(items[1].format, "TXT")


class Archives(Base):
    def test_nested_zip_and_skips(self):
        inner = archive([("vnitrni.txt", "text ve vnořeném archivu")])
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w") as target:
            target.writestr("slozka/", "")
            target.writestr("a.txt", "první soubor")
            target.writestr("vnoreny.zip", inner)
            target.writestr("neznamy.bin", b"\x00\x01\x02nic")
            target.writestr("heslo.txt", "tajné")
            target.writestr("posledni.txt", "poslední soubor")
        path = self.write("spis.zip", outer.getvalue())
        # Mark the "heslo.txt" entry as encrypted without a real cipher.
        original = zipfile.ZipFile.infolist

        def marked(self):
            result = original(self)
            for info in result:
                if info.filename == "heslo.txt":
                    info.flag_bits |= 1
            return result

        with mock.patch.object(zipfile.ZipFile, "infolist", marked):
            items = list(extract.walk(path))
        summary = [(type(i).__name__, getattr(i, "format", None), getattr(i, "code", None)) for i in items]
        self.assertEqual(summary, [("Document", "TXT", None), ("Document", "TXT", None),
                                   ("Skipped", "UNKNOWN", "archive_member_unsupported"),
                                   ("Skipped", "UNKNOWN", "archive_encrypted"), ("Document", "TXT", None)])
        self.assertEqual([i.index for i in items], [1, 2, 3, 4, 5])
        self.assertEqual(all_text(items[1]), "text ve vnořeném archivu")

    def test_streamed_not_read_whole(self):
        path = self.write("spis.zip", archive([("a.txt", "obsah"), ("b.txt", "další")]))
        seen = []
        original = zipfile.ZipFile.read

        def spy(self, name, *args, **kwargs):
            seen.append(str(getattr(self, "filename", "")))
            return original(self, name, *args, **kwargs)

        with mock.patch.object(zipfile.ZipFile, "read", spy):
            items = list(extract.walk(path))
        self.assertEqual(len(items), 2)
        self.assertNotIn(str(path), seen)

    def test_limits(self):
        path = self.write("spis.zip", archive([("a.txt", "x"), ("b.txt", "y"), ("c.txt", "z")]))
        with mock.patch("anonymizer.formats.ARCHIVE_MEMBERS", 2):
            items = list(extract.walk(path))
        self.assertEqual([(type(i).__name__, getattr(i, "code", None)) for i in items],
                         [("Document", None), ("Document", None), ("Skipped", "archive_limit")])
        with mock.patch("anonymizer.formats.ARCHIVE_BYTES", 3):
            items = list(extract.walk(self.write("b.zip", archive([("a.txt", "dlouhý obsah")]))))
        self.assertEqual([i.code for i in items], ["size_limit"])

    def test_depth(self):
        data = archive([("a.txt", "hluboko")])
        for level in range(4):
            data = archive([("z.zip", data)])
        items = list(extract.walk(self.write("a.zip", data)))
        self.assertEqual([(type(i).__name__, getattr(i, "code", None)) for i in items], [("Skipped", "archive_depth")])

    def test_progress_and_failure_isolation(self):
        calls = []
        path = self.write("a.zip", archive([("a.docx", b"PK\x03\x04neplatne"), ("b.txt", "ok")]))
        items = list(extract.walk(path, progress=lambda items, pages: calls.append((items, pages))))
        self.assertEqual([type(i).__name__ for i in items], ["Skipped", "Document"])
        self.assertEqual(calls[-1][0], 2)

    def test_names_never_stored(self):
        path = self.write("tajne-jmeno-spisu.zip", archive([("slozka-novak/jan-novak.txt", "obsah")]))
        items = list(extract.walk(path))
        for item in items:
            for text in (repr(item), str(item)):
                self.assertNotIn("novak", text)
                self.assertNotIn("tajne", text)

    def test_unsupported_top_level(self):
        items = list(extract.walk(self.write("a.bin", b"\x00\x01")))
        self.assertEqual([(type(i).__name__, i.format, i.code) for i in items],
                         [("Skipped", "UNKNOWN", "format_unsupported")])


if __name__ == "__main__":
    unittest.main()
