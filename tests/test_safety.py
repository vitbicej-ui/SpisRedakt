"""Safety of the control output (copies): metadata, hidden layers and images without readable text.

Every case plants a synthetic value into a disputed layer, runs the whole batch with copies and checks all
output files with the independent detector. Messages never contain document values.
"""
import io
import os
import re
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from email.message import EmailMessage
from pathlib import Path

import pymupdf
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anonymizer import documents  # noqa: E402
from anonymizer.batch import process_folders, safe_name  # noqa: E402
from anonymizer.leaks import Value, layers, scan_texts  # noqa: E402
from anonymizer.rules import Rules  # noqa: E402
import synthetic_spis as spis  # noqa: E402

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
needs_ocr = unittest.skipUnless(TESSDATA, "Chybí testovací jazyková data.")

# Names that appear only in the disputed layer (never in a visible paragraph).
AUTHOR = "Radmila Pokorná"
EDITOR = "Bohumil Vondráček"
HIDDEN = "Ctibor Nečesaný"
COMMENT = "Drahomíra Zelenková"
DELETED = "Evženie Bartošová"
COMPANY = "Firma Čapek"
SIDE = "Zbyněk Mrázek"
NAMES = (AUTHOR, EDITOR, HIDDEN, COMMENT, DELETED, SIDE)
VALUES = [Value("JMENO", name) for name in NAMES]
VALUES += [Value("PRIJMENI", name.split()[1]) for name in NAMES]
VALUES.append(Value("JMENO", COMPANY))
RC = Value("RC", "850314/1237")
PHONE = Value("TELEFON", "721 345 678")
WOLF = Value("JMENO", "Tadeáš Wolf")
BASE = "Tento dokument je anonymizovaný."
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def rezip(data, edits):
    """Return the ZIP with members replaced (bytes or function), added or removed (None)."""
    source = zipfile.ZipFile(io.BytesIO(data))
    stream = io.BytesIO()
    done = set()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename in edits:
                done.add(info.filename)
                change = edits[info.filename]
                if change is None:
                    continue
                content = change(content) if callable(change) else change
                if isinstance(content, str):
                    content = content.encode("utf-8")
            target.writestr(info, content)
        for name, change in edits.items():
            if name not in done and change is not None:
                target.writestr(name, change.encode("utf-8") if isinstance(change, str) else change)
    return stream.getvalue()


def png_chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png_with_metadata(pixmap_png, text):
    """PNG with tEXt, iTXt (XMP) and eXIf chunks that carry ``text``."""
    marker = pixmap_png.index(b"IDAT") - 4
    chunks = (png_chunk(b"tEXt", b"Author\0" + text.encode("latin-1", "replace"))
              + png_chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + f"<x:xmpmeta>{text}</x:xmpmeta>".encode())
              + png_chunk(b"eXIf", b"MM\0*" + text.encode("ascii", "replace")))
    return pixmap_png[:marker] + chunks + pixmap_png[marker:]


def jpeg_with_metadata(jpeg, text):
    """JPEG with EXIF (APP1), XMP (APP1) and a comment (COM) that carry ``text``."""
    def segment(code, payload):
        return b"\xff" + bytes([code]) + struct.pack(">H", len(payload) + 2) + payload

    extra = (segment(0xE1, b"Exif\0\0MM\0*" + text.encode("ascii", "replace"))
             + segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + f"<x:xmpmeta>{text}</x:xmpmeta>".encode())
             + segment(0xFE, text.encode("ascii", "replace")))
    return jpeg[:2] + extra + jpeg[2:]


def plain_pixmap(width=300, height=200, gray=False, color=(200, 220, 240)):
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY if gray else pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pixmap.clear_with(220 if gray else 255)
    pixmap.set_rect(pymupdf.IRect(40, 40, 160, 100), (90,) if gray else color)
    return pixmap


class Batch(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.target = self.root / "in", self.root / "out"
        self.source.mkdir()
        self.target.mkdir()

    def run_batch(self, files, markdown=False):
        for name, data in files.items():
            (self.source / name).write_bytes(data)
        results = list(process_folders(self.source, self.target, TESSDATA, markdown=markdown, copies=True))
        self.errors = [error for _, _, error in results if error]
        return results

    def outputs(self):
        return sorted(path for path in self.target.rglob("*") if path.is_file())

    def leaks(self, values=VALUES):
        texts = []
        for path in self.outputs():
            texts.append(str(path.relative_to(self.target)))
            texts.extend(layers(path, TESSDATA))
        return dict(scan_texts(texts, values))

    def assert_clean(self, files, values=VALUES, expect_output=True):
        self.run_batch(files)
        if expect_output:
            self.assertFalse(self.errors, "Dávka vrátila chybu.")
            self.assertTrue(self.outputs(), "Dávka nevydala žádný výstup.")
        self.assertEqual(self.leaks(values), {}, "Ve výstupu zůstal údaj ze sporné vrstvy.")

    def member(self, suffix, name):
        (path,) = [p for p in self.outputs() if p.suffix == suffix]
        with zipfile.ZipFile(path) as archive:
            return archive.read(name) if name in archive.namelist() else None

    def names(self, suffix):
        (path,) = [p for p in self.outputs() if p.suffix == suffix]
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()


def docx_bytes(body_xml="", comments=False, header_hidden=False):
    document = Document()
    document.add_paragraph(BASE)
    properties = document.core_properties
    properties.author, properties.last_modified_by = AUTHOR, EDITOR
    properties.title, properties.subject, properties.keywords = f"Spis {AUTHOR}", SIDE, SIDE
    properties.comments, properties.category, properties.revision = f"Poznámka {SIDE}", SIDE, 7
    from docx.oxml import parse_xml

    body = document.element.body
    for fragment in re.findall(r"<w:p>.*?</w:p>", body_xml, re.S):
        body.insert(len(body) - 1, parse_xml(f'<w:p xmlns:w="{W}">' + fragment[5:]))
    stream = io.BytesIO()
    document.save(stream)
    edits = {
        "docProps/app.xml": XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                            f"<Company>{COMPANY}</Company><Manager>{SIDE}</Manager><Application>Test</Application>"
                            "<TotalTime>120</TotalTime></Properties>",
        "docProps/custom.xml": XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
                               f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="Klient"><lpwstr xmlns="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">{SIDE}</lpwstr></property></Properties>',
    }
    data = stream.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        types = archive.read("[Content_Types].xml").decode()
        rels = archive.read("word/_rels/document.xml.rels").decode()
        docs = archive.read("document.xml".join(("word/", ""))).decode()
    types = types.replace("</Types>", '<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/></Types>')
    if comments:
        edits["word/comments.xml"] = (XML + f'<w:comments xmlns:w="{W}"><w:comment w:id="0" w:author="{COMMENT}" w:date="2026-01-01T10:00:00Z" w:initials="DZ">'
                                      f"<w:p><w:r><w:t>Poznámka k věci: {SIDE}</w:t></w:r></w:p></w:comment></w:comments>")
        types = types.replace("</Types>", '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>')
        rels = rels.replace("</Relationships>", f'<Relationship Id="rIdC1" Type="{REL}/comments" Target="comments.xml"/></Relationships>')
        docs = docs.replace(BASE + "</w:t></w:r>", BASE + '</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>', 1)
        docs = docs.replace("<w:r><w:t>" + BASE, '<w:commentRangeStart w:id="0"/><w:r><w:t>' + BASE, 1) if "<w:r><w:t>" + BASE in docs else docs
        edits["word/document.xml"] = docs
    edits["[Content_Types].xml"] = types
    edits["word/_rels/document.xml.rels"] = rels
    return rezip(data, edits)


def run(text, props=""):
    return f"<w:r>{('<w:rPr>' + props + '</w:rPr>') if props else ''}<w:t xml:space=\"preserve\">{text}</w:t></w:r>"


class DocxTests(Batch):
    def test_properties_and_custom_data(self):
        self.assert_clean({"a.docx": docx_bytes()})
        names = self.names(".docx")
        self.assertFalse([name for name in names if name.startswith("customXml/")])

    def test_comments_removed(self):
        self.assert_clean({"a.docx": docx_bytes(comments=True)})
        self.assertFalse([name for name in self.names(".docx") if "comments" in name])
        self.assertNotIn(b"comment", self.member(".docx", "word/document.xml").lower())

    def test_hidden_text_removed(self):
        body = (f"<w:p>{run(BASE)}{run(HIDDEN + ' ' + RC.text, '<w:vanish/>')}</w:p>"
                f"<w:p><w:pPr><w:rPr><w:vanish/></w:rPr></w:pPr>{run(SIDE)}</w:p>")
        self.assert_clean({"a.docx": docx_bytes(body)}, VALUES + [RC])
        self.assertNotIn(b"vanish", self.member(".docx", "word/document.xml"))

    def test_hidden_style_document_variables_and_moves(self):
        data = docx_bytes(f'<w:p>{run(BASE)}<w:r><w:rPr><w:rStyle w:val="Skryty"/></w:rPr><w:t>{HIDDEN}</w:t></w:r></w:p>'
                          f'<w:p><w:pPr><w:pStyle w:val="SkrytyOdstavec"/></w:pPr>{run(SIDE)}</w:p>'
                          f'<w:p><w:moveFrom w:id="5" w:author="{EDITOR}" w:date="2026-01-01T10:00:00Z">{run(DELETED)}</w:moveFrom>'
                          f'<w:moveTo w:id="6" w:author="{EDITOR}" w:date="2026-01-01T10:00:00Z">{run("Přesunuto viditelně.")}</w:moveTo></w:p>')
        styles = ('<w:style xmlns:w="%s" w:type="character" w:styleId="Skryty"><w:name w:val="Skryty"/><w:rPr><w:vanish/></w:rPr></w:style>'
                  '<w:style xmlns:w="%s" w:type="paragraph" w:styleId="SkrytyOdstavec"><w:name w:val="SkrytyOdstavec"/><w:basedOn w:val="Skryty"/></w:style>' % (W, W))
        data = rezip(data, {
            "word/styles.xml": lambda raw: raw.replace(b"</w:styles>", styles.encode() + b"</w:styles>"),
            "word/settings.xml": lambda raw: raw.replace(b"</w:settings>", f'<w:docVars><w:docVar w:name="Klient" w:val="{SIDE}"/></w:docVars></w:settings>'.encode()),
        })
        self.assert_clean({"a.docx": data})
        self.assertNotIn(b"docVar", self.member(".docx", "word/settings.xml"))
        document = self.member(".docx", "word/document.xml")
        self.assertNotIn(b"moveFrom", document)
        self.assertIn("Přesunuto viditelně.".encode(), document)

    def test_tracked_changes_accepted(self):
        body = (f'<w:p>{run(BASE)}<w:ins w:id="1" w:author="{EDITOR}" w:date="2026-01-01T10:00:00Z">{run("Vloženo viditelně.")}</w:ins>'
                f'<w:del w:id="2" w:author="{COMMENT}" w:date="2026-01-01T10:00:00Z"><w:r><w:delText>{DELETED}</w:delText></w:r></w:del>'
                f'<w:r><w:rPr><w:b/><w:rPrChange w:id="3" w:author="{SIDE}" w:date="2026-01-01T10:00:00Z"><w:rPr/></w:rPrChange></w:rPr><w:t>x</w:t></w:r></w:p>')
        self.assert_clean({"a.docx": docx_bytes(body)})
        document = self.member(".docx", "word/document.xml")
        for tag in (b"w:ins", b"w:del", b"delText", b"rPrChange", b"w:author", b"w:date"):
            self.assertNotIn(tag, document)
        self.assertIn("Vloženo viditelně.".encode(), document)


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PROPERTIES = (
    ("docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml",
     "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
     XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
     'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
     f"<dc:creator>{AUTHOR}</dc:creator><cp:lastModifiedBy>{EDITOR}</cp:lastModifiedBy><dc:title>{SIDE}</dc:title>"
     f"<cp:keywords>{SIDE}</cp:keywords><dc:description>{SIDE}</dc:description><cp:revision>7</cp:revision>"
     '<dcterms:created xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">2026-01-01T10:00:00Z</dcterms:created>'
     "</cp:coreProperties>"),
    ("docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml",
     REL + "/extended-properties",
     XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
     f"<Company>{COMPANY}</Company><Manager>{SIDE}</Manager><TitlesOfParts><vt:vector xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\" size=\"1\" baseType=\"lpstr\"><vt:lpstr>{SIDE}</vt:lpstr></vt:vector></TitlesOfParts>"
     "</Properties>"),
)


def package(parts, main_type_parts, root_rels):
    """Assemble an Office package: ``parts`` is {name: xml or bytes}, content types are given per part."""
    overrides = "".join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind in main_type_parts.items())
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", XML + f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                         '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>'
                         f"{overrides}</Types>")
        relations = "".join(f'<Relationship Id="rId{index}" Type="{kind}" Target="{target}"/>'
                            for index, (kind, target) in enumerate(root_rels, 1))
        archive.writestr("_rels/.rels", XML + f'<Relationships xmlns="{PKG_RELS}">{relations}</Relationships>')
        for name, kind, relation, content in PROPERTIES:
            archive.writestr(name, content)
        for name, content in parts.items():
            archive.writestr(name, content)
    return rezip(stream.getvalue(), {})


def relationships(items):
    return XML + f'<Relationships xmlns="{PKG_RELS}">' + "".join(
        f'<Relationship Id="{identifier}" Type="{REL}/{kind}" Target="{target}"/>' for identifier, kind, target in items
    ) + "</Relationships>"


def with_properties(data):
    """Register the core and extended properties in the root relationships and content types."""
    def root(raw):
        return raw.decode().replace("</Relationships>", "".join(
            f'<Relationship Id="rIdP{index}" Type="{relation}" Target="{name}"/>'
            for index, (name, kind, relation, _) in enumerate(PROPERTIES)) + "</Relationships>")

    def types(raw):
        return raw.decode().replace("</Types>", "".join(
            f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, kind, _, _ in PROPERTIES) + "</Types>")

    return rezip(data, {"_rels/.rels": root, "[Content_Types].xml": types})


def xlsx_bytes(sheets, names=(), comment=False, image=None):
    """Workbook of ``sheets``: dict(name, state, rows=[(cells, hidden)], hidden_columns=[numbers])."""
    parts, kinds, workbook_rels, sheet_tags = {}, {}, [], []
    kinds["xl/workbook.xml"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    for number, sheet in enumerate(sheets, 1):
        rows = []
        for row_number, (cells, hidden) in enumerate(sheet["rows"], 1):
            cells_xml = "".join(
                f'<c r="{"ABCDEFG"[column]}{row_number}" t="inlineStr"><is><t>{value}</t></is></c>'
                for column, value in enumerate(cells))
            rows.append(f'<row r="{row_number}"{" hidden=\"1\"" if hidden else ""}>{cells_xml}</row>')
        columns = "".join(f'<col min="{c}" max="{c}" width="12" hidden="1" customWidth="1"/>' for c in sheet.get("hidden_columns", ()))
        extra, sheet_rels = "", []
        if number == 1 and comment:
            extra += '<legacyDrawing r:id="rIdV"/>'
            sheet_rels += [("rIdC", "comments", "../comments1.xml"), ("rIdV", "vmlDrawing", "../drawings/vmlDrawing1.vml")]
            parts["xl/comments1.xml"] = (XML + f'<comments xmlns="{MAIN}"><authors><author>{COMMENT}</author></authors><commentList>'
                                         f'<comment ref="A1" authorId="0"><text><r><t>Poznámka {SIDE}</t></r></text></comment></commentList></comments>')
            kinds["xl/comments1.xml"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"
            parts["xl/drawings/vmlDrawing1.vml"] = '<xml xmlns:v="urn:schemas-microsoft-com:vml"><v:shape id="s1"/></xml>'
        if number == 1 and image:
            extra = '<drawing r:id="rIdD"/>' + extra
            sheet_rels.append(("rIdD", "drawing", "../drawings/drawing1.xml"))
            parts["xl/drawings/drawing1.xml"] = (XML + '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
                                                 f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{REL}"><xdr:oneCellAnchor>'
                                                 '<xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                                                 '<xdr:ext cx="1900000" cy="400000"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Obrazek"/><xdr:cNvPicPr/></xdr:nvPicPr>'
                                                 '<xdr:blipFill><a:blip r:embed="rIdI"/></xdr:blipFill><xdr:spPr/></xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>')
            kinds["xl/drawings/drawing1.xml"] = "application/vnd.openxmlformats-officedocument.drawing+xml"
            parts["xl/drawings/_rels/drawing1.xml.rels"] = relationships([("rIdI", "image", "../media/image1.png")])
            parts["xl/media/image1.png"] = image
        parts[f"xl/worksheets/sheet{number}.xml"] = (
            XML + f'<worksheet xmlns="{MAIN}" xmlns:r="{REL}">' + (f"<cols>{columns}</cols>" if columns else "")
            + f'<sheetData>{"".join(rows)}</sheetData>{extra}</worksheet>')
        kinds[f"xl/worksheets/sheet{number}.xml"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
        if sheet_rels:
            parts[f"xl/worksheets/_rels/sheet{number}.xml.rels"] = relationships(sheet_rels)
        workbook_rels.append((f"rId{number}", "worksheet", f"worksheets/sheet{number}.xml"))
        state = f' state="{sheet["state"]}"' if sheet.get("state") else ""
        sheet_tags.append(f'<sheet name="{sheet["name"]}" sheetId="{number}"{state} r:id="rId{number}"/>')
    defined = "".join(f'<definedName name="{name}">{value}</definedName>' for name, value in names)
    parts["xl/workbook.xml"] = (XML + f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{"".join(sheet_tags)}</sheets>'
                                + (f"<definedNames>{defined}</definedNames>" if defined else "") + "</workbook>")
    parts["xl/_rels/workbook.xml.rels"] = relationships(workbook_rels)
    data = package(parts, kinds, [(REL + "/officeDocument", "xl/workbook.xml")])
    return with_properties(data)


def plain_xlsx(rows):
    return xlsx_bytes([dict(name="Přehled", rows=[(row, False) for row in rows])])


class XlsxTests(Batch):
    def values(self):
        return VALUES + [WOLF, RC, PHONE, Value("PRIJMENI", "Wolf")]

    def test_hidden_sheet_row_column_and_header(self):
        header = ["Jméno a příjmení", "Telefon"]
        sheets = [
            dict(name="Přehled", rows=[([BASE], False)]),
            dict(name="Skryté", state="hidden", rows=[(header, False), (["Tadeáš Wolf", PHONE.text], False),
                                                     (["Poznámka", f"rodné číslo {RC.text}, tel. {PHONE.text}"], False)]),
            dict(name="Viditelný", rows=[(header, False), (["Tadeáš Wolf", PHONE.text], False), ([f"RČ {RC.text}"], True),
                                         (["Tadeáš Wolf"], True)], hidden_columns=[2]),
            dict(name="Velmi skrytý", state="veryHidden", rows=[(["Jméno"], False), (["Tadeáš Wolf"], False)]),
        ]
        self.assert_clean({"a.xlsx": xlsx_bytes(sheets)}, self.values())

    def test_sheet_name_and_defined_name(self):
        base = dict(name="Přehled", rows=[([BASE], False)])
        self.run_batch({"a.xlsx": xlsx_bytes([base, dict(name="Tadeáš Wolf", rows=[(["x"], False)])])})
        self.assertEqual(self.leaks(self.values()), {}, "Název listu zůstal ve výstupu.")
        self.run_batch({"b.xlsx": xlsx_bytes([base], names=[("Klient_Tadeáš_Wolf", "Přehled!$A$1"), ("Hodnota", '"Tadeáš Wolf"')])})
        self.assertEqual(self.leaks(self.values()), {}, "Definované jméno zůstalo ve výstupu.")

    def test_comments_removed(self):
        self.assert_clean({"a.xlsx": xlsx_bytes([dict(name="Přehled", rows=[([BASE], False)])], comment=True)})
        names = self.names(".xlsx")
        self.assertFalse([name for name in names if "comments" in name or name.endswith(".vml")])
        self.assertNotIn(b"legacyDrawing", self.member(".xlsx", "xl/worksheets/sheet1.xml"))


def pptx_bytes(comments=False, image=None):
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          f'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="{REL}"')
    picture = ('<p:pic><p:nvPicPr><p:cNvPr id="3" name="Obrazek"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rIdI"/></p:blipFill>'
               '<p:spPr/></p:pic>') if image else ""
    parts = {
        "ppt/presentation.xml": XML + f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": relationships([("rId1", "slide", "slides/slide1.xml")]
                                                         + ([("rIdA", "commentAuthors", "commentAuthors.xml")] if comments else [])),
        "ppt/slides/slide1.xml": XML + f'<p:sld {ns}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/>'
                                f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{BASE}</a:t></a:r></a:p></p:txBody></p:sp>{picture}</p:spTree></p:cSld></p:sld>",
    }
    kinds = {"ppt/presentation.xml": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
             "ppt/slides/slide1.xml": "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"}
    slide_rels = ([("rIdB", "comments", "../comments/comment1.xml")] if comments else []) + ([("rIdI", "image", "../media/image1.png")] if image else [])
    if slide_rels:
        parts["ppt/slides/_rels/slide1.xml.rels"] = relationships(slide_rels)
    if image:
        parts["ppt/media/image1.png"] = image
    if comments:
        parts["ppt/commentAuthors.xml"] = (XML + f'<p:cmAuthorLst {ns}><p:cmAuthor id="0" name="{COMMENT}" initials="DZ" lastIdx="1" clrIdx="0"/></p:cmAuthorLst>')
        parts["ppt/comments/comment1.xml"] = (XML + f'<p:cmLst {ns}><p:cm authorId="0" dt="2026-01-01T10:00:00.000" idx="1"><p:pos x="10" y="10"/>'
                                              f"<p:text>{SIDE}</p:text></p:cm></p:cmLst>")
        kinds["ppt/commentAuthors.xml"] = "application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"
        kinds["ppt/comments/comment1.xml"] = "application/vnd.openxmlformats-officedocument.presentationml.comments+xml"
    return with_properties(package(parts, kinds, [(REL + "/officeDocument", "ppt/presentation.xml")]))


class PptxTests(Batch):
    def test_properties_and_comments(self):
        self.assert_clean({"a.pptx": pptx_bytes(comments=True)})
        self.assertFalse([name for name in self.names(".pptx") if "omment" in name])
        self.assertNotIn(b"omments", self.member(".pptx", "ppt/slides/_rels/slide1.xml.rels"))


ODF_NAMES = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
             'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
             'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
             'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
             'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
             'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
             'xmlns:dc="http://purl.org/dc/elements/1.1/"')


def odf_bytes(kind, body, styles="", extra=None, mimetype=None):
    mimetype = mimetype or f"application/vnd.oasis.opendocument.{kind}"
    content = (f'<office:document-content {ODF_NAMES}><office:automatic-styles>{styles}</office:automatic-styles>'
               f"<office:body><office:{kind}>{body}</office:{kind}></office:body></office:document-content>")
    meta = (f'<office:document-meta {ODF_NAMES}><office:meta><dc:creator>{AUTHOR}</dc:creator>'
            f"<meta:initial-creator>{EDITOR}</meta:initial-creator><dc:title>{SIDE}</dc:title>"
            f'<meta:user-defined meta:name="Klient">{SIDE}</meta:user-defined>'
            "<meta:creation-date>2026-01-01T10:00:00</meta:creation-date><dc:date>2026-01-02T10:00:00</dc:date>"
            '<meta:editing-cycles>9</meta:editing-cycles><meta:generator>Test</meta:generator>'
            '<meta:document-statistic meta:page-count="1"/></office:meta></office:document-meta>')
    settings = (f'<office:document-settings {ODF_NAMES}><office:settings><config:config-item-set config:name="x">'
                f'<config:config-item config:name="PrinterName" config:type="string">{SIDE}</config:config-item>'
                "</config:config-item-set></office:settings></office:document-settings>")
    manifest = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                "</manifest:manifest>")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", mimetype, zipfile.ZIP_STORED)
        archive.writestr("content.xml", content)
        archive.writestr("meta.xml", meta)
        archive.writestr("settings.xml", settings)
        archive.writestr("styles.xml", f"<office:document-styles {ODF_NAMES}/>")
        archive.writestr("META-INF/manifest.xml", manifest)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return stream.getvalue()


class OdfTests(Batch):
    def test_meta_settings_and_annotations(self):
        body = (f"<text:p>{BASE}<office:annotation><dc:creator>{COMMENT}</dc:creator><dc:date>2026-01-01T10:00:00</dc:date>"
                f"<text:p>Poznámka {SIDE}</text:p></office:annotation><office:annotation-end/></text:p>")
        self.assert_clean({"a.odt": odf_bytes("text", body)})
        content = self.member(".odt", "content.xml")
        self.assertNotIn(b"annotation", content)
        meta = self.member(".odt", "meta.xml")
        for tag in (b"creation-date", b"dc:date", b"editing-cycles", b"user-defined", b"initial-creator"):
            self.assertNotIn(tag, meta)
        self.assertNotIn("settings.xml", self.names(".odt"))

    def test_tracked_changes_accepted(self):
        info = f"<office:change-info><dc:creator>{EDITOR}</dc:creator><dc:date>2026-01-01T10:00:00</dc:date></office:change-info>"
        body = ("<text:tracked-changes>"
                f'<text:changed-region text:id="a"><text:insertion>{info}</text:insertion></text:changed-region>'
                f'<text:changed-region text:id="b"><text:deletion>{info}<text:p>Smazáno: {DELETED}</text:p></text:deletion></text:changed-region>'
                "</text:tracked-changes>"
                f'<text:p>{BASE}<text:change-start text:change-id="a"/>Vloženo viditelně.<text:change-end text:change-id="a"/>'
                '<text:change text:change-id="b"/></text:p>')
        self.assert_clean({"a.odt": odf_bytes("text", body)})
        content = self.member(".odt", "content.xml")
        for tag in (b"tracked-changes", b"changed-region", b"change-start", b"text:change", b"dc:creator"):
            self.assertNotIn(tag, content)
        self.assertIn("Vloženo viditelně.".encode(), content)

    def test_hidden_text_removed(self):
        styles = ('<style:style style:name="Skryty" style:family="text"><style:text-properties text:display="none"/></style:style>'
                  '<style:style style:name="Skryty2" style:family="text" style:parent-style-name="Skryty"/>'
                  '<style:style style:name="SkrytyOdstavec" style:family="paragraph"><style:text-properties text:display="none"/></style:style>')
        body = (f'<text:p>{BASE}<text:span text:style-name="Skryty">{HIDDEN}</text:span>'
                f'<text:span text:style-name="Skryty2">{SIDE}</text:span>'
                f'<text:hidden-text text:condition="true" text:string-value="{COMMENT}">{COMMENT}</text:hidden-text></text:p>'
                f'<text:hidden-paragraph text:condition="true">{DELETED}</text:hidden-paragraph>'
                f'<text:p text:style-name="SkrytyOdstavec">{AUTHOR}</text:p>'
                f'<text:section text:name="S" text:display="none"><text:p>{EDITOR}</text:p></text:section>'
                f'<text:section text:name="T" text:display="condition" text:condition="true"><text:p>{COMPANY}</text:p></text:section>')
        self.assert_clean({"a.odt": odf_bytes("text", body, styles)})
        content = self.member(".odt", "content.xml")
        self.assertNotIn(b"hidden-", content)
        self.assertNotIn(b"OSOBA", content, "Skrytý text se měl odstranit, ne nahradit štítkem.")

    def test_field_declarations(self):
        body = (f'<text:user-field-decls><text:user-field-decl text:name="Klient" office:value-type="string" office:string-value="{SIDE}"/>'
                f'</text:user-field-decls><text:p>{BASE}<text:user-field-get text:name="Klient">{SIDE}</text:user-field-get></text:p>')
        self.assert_clean({"a.odt": odf_bytes("text", body)})

    def test_ods_hidden_sheet_row_column(self):
        styles = ('<style:style style:name="ta-hidden" style:family="table"><style:table-properties table:display="false"/></style:style>')
        cell = lambda text: f'<table:table-cell office:value-type="string"><text:p>{text}</text:p></table:table-cell>'
        body = ('<table:table table:name="Viditelný">'
                f"<table:table-row>{cell('Jméno a příjmení')}{cell('Telefon')}</table:table-row>"
                f"<table:table-row>{cell('Tadeáš Wolf')}{cell(PHONE.text)}</table:table-row>"
                f'<table:table-row table:visibility="collapse">{cell(f"RČ {RC.text}")}</table:table-row></table:table>'
                '<table:table table:name="Skrytý" table:style-name="ta-hidden">'
                f"<table:table-row>{cell('Jméno a příjmení')}</table:table-row>"
                f"<table:table-row>{cell('Tadeáš Wolf')}</table:table-row>"
                f"<table:table-row>{cell(f'tel. {PHONE.text}, RČ {RC.text}')}</table:table-row></table:table>")
        self.assert_clean({"a.ods": odf_bytes("spreadsheet", body, styles)}, VALUES + [WOLF, RC, PHONE])


def rtf_bytes(body, header=""):
    return ("{\\rtf1\\ansi\\ansicpg1250\\deff0{\\fonttbl{\\f0 Arial;}}" + header + "\\f0\\fs22\n" + rtf_ascii(body) + "\\par}").encode("ascii")


def rtf_ascii(text):
    return "".join(c if c.isascii() else "\\'" + c.encode("cp1250").hex() for c in text)


class RtfTests(Batch):
    def test_info_template_and_revision_table(self):
        header = ("{\\info{\\title " + rtf_ascii(SIDE) + "}{\\author " + rtf_ascii(AUTHOR) + "}{\\operator " + rtf_ascii(EDITOR)
                  + "}{\\company " + rtf_ascii(COMPANY) + "}{\\doccomm " + rtf_ascii(SIDE) + "}{\\creatim\\yr2026\\mo1\\dy1}}"
                  "{\\*\\template C:\\\\Users\\\\" + rtf_ascii(AUTHOR) + "\\\\Normal.dot}"
                  "{\\*\\revtbl{Unknown;}{" + rtf_ascii(EDITOR) + ";}}"
                  "{\\*\\userprops{\\propname Klient}\\proptype30{\\staticval " + rtf_ascii(SIDE) + "}}")
        self.assert_clean({"a.rtf": rtf_bytes(BASE, header)})
        data = next(path for path in self.outputs() if path.suffix == ".rtf").read_bytes()
        for word in (b"\\info", b"\\template", b"\\revtbl", b"\\userprops"):
            self.assertNotIn(word, data)

    def test_tracked_changes_and_comments(self):
        header = "{\\*\\revtbl{Unknown;}{" + rtf_ascii(EDITOR) + ";}}"
        body = (BASE + " {\\revised\\revauth1\\revdttm1234 Vloženo viditelně.}"
                "{\\deleted\\revauthdel1\\revdttmdel1234 " + rtf_ascii(DELETED) + "}"
                "{\\*\\atrfstart 1}Text{\\*\\atrfend 1}"
                "{\\*\\annotation{\\*\\atnid DZ}{\\*\\atnauthor " + rtf_ascii(COMMENT) + "}\\pard Poznámka "
                + rtf_ascii(SIDE) + "\\par}")
        body = body.replace("Vloženo viditelně.", "Vlozeno viditelne.")
        self.assert_clean({"a.rtf": rtf_bytes(body, header)})
        data = next(path for path in self.outputs() if path.suffix == ".rtf").read_bytes()
        for word in (b"\\revauth", b"\\revtbl", b"\\annotation", b"\\revdttm"):
            self.assertNotIn(word, data)
        self.assertIn(b"Vlozeno viditelne.", data)

    def test_hidden_text_removed(self):
        body = BASE + " {\\v " + rtf_ascii(HIDDEN) + "}\\par {\\v\\fs20 " + rtf_ascii(SIDE) + "}"
        self.assert_clean({"a.rtf": rtf_bytes(body)})


def text_png(gray=False, lines=("Tadeáš Wolf, tel. 721 345 678",)):
    pixmap = spis.image_pixmaps([lines])[0]
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap) if pixmap.colorspace != pymupdf.csRGB else pixmap
    return (pymupdf.Pixmap(pymupdf.csGRAY, pixmap) if gray else pixmap).tobytes("png")


def dark_share(data):
    pixmap = pymupdf.Pixmap(data)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap) if pixmap.colorspace != pymupdf.csRGB else pixmap
    samples = pixmap.samples
    count = pixmap.width * pixmap.height
    return sum(1 for index in range(0, len(samples), pixmap.n) if samples[index] == 0) / count


@needs_ocr
class ImageTests(Batch):
    def test_gray_pixmap_is_recognized(self):
        for gray in (False, True):
            pixmap = pymupdf.Pixmap(text_png(gray))
            self.assertEqual(pixmap.colorspace.n, 1 if gray else 3)
            text = documents.recognize_image(pixmap, TESSDATA)[0]
            self.assertIn("Wolf", text, "OCR nepřečetlo obraz bez převodu na RGB.")
        converted = pymupdf.Pixmap(pymupdf.csGRAY, plain_pixmap())
        transparent = pymupdf.Pixmap(plain_pixmap(), 1)
        for pixmap in (converted, transparent):
            self.assertEqual(documents.recognize_image(pixmap, TESSDATA)[0].strip(), "")


def output_images(path):
    """Raster images found in an output file (media of packages, RTF pictures, attachments, the file itself)."""
    data = path.read_bytes()
    found = []
    if data.startswith(b"{\\rtf"):
        for match in re.finditer(rb"\\(?:png|jpeg)blip(?:\\[a-z]+\d+)*\n((?:[0-9a-f]+\n?)+)", data):
            found.append(bytes.fromhex(match[1].decode().replace("\n", "")))
    elif data.startswith(b"PK"):
        with zipfile.ZipFile(path) as archive:
            found = [archive.read(name) for name in archive.namelist()
                     if re.search(r"(?:media|Pictures)/[^/]+\.(?:png|jpe?g|bmp)$", name, re.I)]
    elif path.suffix == ".eml":
        import email
        from email import policy

        message = email.message_from_bytes(data, policy=policy.default)
        found = [part.get_payload(decode=True) for part in message.walk()
                 if part.get_content_maintype() in {"image", "application"}]
    else:
        found = [data]
    return found


def rtf_with_picture(image, kind="pngblip"):
    hexdata = image.hex()
    lines = "\n".join(hexdata[i:i + 128] for i in range(0, len(hexdata), 128))
    return rtf_bytes("Text {\\pict\\" + kind + "\\picw100\\pich40\n" + lines + "\n}")


def odt_with_picture(image):
    body = (f'<text:p>{BASE}<draw:frame xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"><draw:image xlink:href="Pictures/obrazek.png" xlink:type="simple"/></draw:frame></text:p>')
    return odf_bytes("text", body, extra={"Pictures/obrazek.png": image})


def docx_with_picture(image):
    document = Document()
    document.add_paragraph(BASE)
    document.add_picture(io.BytesIO(image))
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def eml_with_picture(image, subtype="png"):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "a@example.test", "b@example.test", "Obrazek"
    message["Date"] = "Tue, 16 Jun 2026 09:30:00 +0200"
    message.set_content(BASE)
    message.add_attachment(image, maintype="image", subtype=subtype, filename="foto.png")
    return message.as_bytes()


def bmp_bytes(png):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    QImage.fromData(png, "PNG").save(buffer, "BMP")
    return bytes(buffer.data())


def jpeg_bytes(pixmap):
    return pixmap.tobytes("jpeg")


@needs_ocr
class ImageOutputTests(Batch):
    """Pictures without readable text are blacked out whole; pictures with text only where the text is."""

    def carriers(self, image):
        yield "docx", "a.docx", docx_with_picture(image)
        yield "xlsx", "a.xlsx", xlsx_bytes([dict(name="Přehled", rows=[([BASE], False)])], image=image)
        yield "pptx", "a.pptx", pptx_bytes(image=image)
        yield "odt", "a.odt", odt_with_picture(image)
        yield "rtf", "a.rtf", rtf_with_picture(image)
        yield "eml", "a.eml", eml_with_picture(image)
        yield "png", "a.png", image

    def released_images(self):
        return [data for path in self.outputs() for data in output_images(path)]

    def test_picture_without_text_is_black_everywhere(self):
        for gray in (False, True):
            image = plain_pixmap(gray=gray).tobytes("png")
            for kind, name, data in self.carriers(image):
                with self.subTest(kind=kind, gray=gray):
                    self.setUp()
                    self.run_batch({name: data})
                    self.assertFalse(self.errors)
                    pictures = self.released_images()
                    self.assertTrue(pictures, "Obrázek chybí ve výstupu.")
                    for picture in pictures:
                        self.assertGreater(dark_share(picture), 0.999, "Obrázek bez čitelného textu nebyl celý začerněn.")

    def test_gray_and_color_picture_with_text_is_blacked_only_where_text_is(self):
        for gray in (False, True):
            image = text_png(gray)
            for kind, name, data in self.carriers(image):
                with self.subTest(kind=kind, gray=gray):
                    self.setUp()
                    self.run_batch({name: data})
                    self.assertFalse(self.errors)
                    self.assertEqual(self.leaks([WOLF, PHONE, Value("PRIJMENI", "Wolf")]), {})
                    for picture in self.released_images():
                        share = dark_share(picture)
                        self.assertGreater(share, 0.005, "Text v obrázku nebyl začerněn.")
                        self.assertLess(share, 0.5, "Celý obrázek se zbytečně začernil.")

    def test_bmp_and_jpeg_pictures(self):
        for kind, builder in (("bmp", bmp_bytes), ("jpg", None)):
            for text in (False, True):
                with self.subTest(kind=kind, text=text):
                    self.setUp()
                    pixmap = pymupdf.Pixmap(text_png(False)) if text else plain_pixmap()
                    data = builder(pixmap.tobytes("png")) if builder else jpeg_bytes(pixmap)
                    self.run_batch({"a." + kind: data})
                    self.assertFalse(self.errors)
                    (picture,) = self.released_images()
                    share = dark_share(picture)
                    if text:
                        self.assertTrue(0.005 < share < 0.5)
                        self.assertEqual(self.leaks([WOLF, PHONE]), {})
                    else:
                        self.assertGreater(share, 0.999)


@needs_ocr
class ImageMetadataTests(Batch):
    SENTINEL = "Radmila Pokorna"

    def check(self, files):
        self.run_batch(files)
        self.assertFalse(self.errors)
        self.assertEqual(self.leaks(VALUES), {}, "Metadata obrázku zůstala ve výstupu.")
        for path in self.outputs():
            for picture in output_images(path):
                self.assertNotIn(b"Exif", picture)
                self.assertNotIn(b"xmpmeta", picture)
                self.assertNotIn(b"tEXt", picture)
                self.assertNotIn(b"iTXt", picture)
                self.assertNotIn(b"eXIf", picture)

    def test_png_and_jpeg_metadata_is_dropped(self):
        png = text_png(False)
        jpeg = jpeg_bytes(pymupdf.Pixmap(png))
        tagged_png = png_with_metadata(png, self.SENTINEL)
        tagged_jpeg = jpeg_with_metadata(jpeg, self.SENTINEL)
        self.assertIn(self.SENTINEL.encode(), tagged_png)
        self.assertIn(self.SENTINEL.encode(), tagged_jpeg)
        self.check({"a.png": tagged_png, "b.jpg": tagged_jpeg})

    def test_embedded_pictures_metadata_is_dropped(self):
        png = png_with_metadata(text_png(False), self.SENTINEL)
        self.check({"a.docx": docx_with_picture(png), "b.rtf": rtf_with_picture(png), "c.odt": odt_with_picture(png),
                    "d.eml": eml_with_picture(png)})


@needs_ocr
class PdfMetadataTests(Batch):
    def test_metadata_attachments_and_outline(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), BASE, fontname="helv")
            document.set_metadata({"author": AUTHOR, "title": SIDE, "subject": SIDE, "keywords": SIDE,
                                   "creator": EDITOR, "producer": COMPANY})
            document.set_xml_metadata(f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><dc>{AUTHOR}</dc></x:xmpmeta>")
            document.embfile_add("priloha.txt", SIDE.encode())
            document.set_toc([[1, SIDE, 1]])
            note = page.add_text_annot((100, 100), f"Poznámka {SIDE}")
            note.set_info(title=COMMENT)
            data = document.tobytes()
        self.assert_clean({"a.pdf": data})
        (path,) = self.outputs()
        with pymupdf.open(path) as result:
            self.assertFalse(any((result.metadata or {}).get(key) for key in ("author", "title", "subject", "keywords", "creator")))
            self.assertEqual(result.get_toc(), [])
            self.assertEqual(result.embfile_count(), 0)
            self.assertFalse(result.get_xml_metadata())
            self.assertIsNone(result[0].first_annot)


class MailTests(Batch):
    def test_headers_attachment_names_and_nested_message(self):
        message = EmailMessage()
        message["From"] = f"{AUTHOR} <radmila.pokorna@example.test>"
        message["To"] = f"{EDITOR} <bohumil@example.test>"
        message["Subject"] = "Dokument"
        message["Date"] = "Tue, 16 Jun 2026 09:30:00 +0200"
        message["Received"] = f"from pc-{HIDDEN.split()[1].lower()}.example.test ([10.1.2.3]) by mx.example.test"
        message["X-Mailer"] = f"Klient {SIDE}"
        message["User-Agent"] = SIDE
        message["Message-ID"] = "<abc123@pc-nesaly.example.test>"
        message["Return-Path"] = "<radmila.pokorna@example.test>"
        message["X-Originating-IP"] = "[10.1.2.3]"
        message["Thread-Topic"] = SIDE
        message["Organization"] = COMPANY
        message["List-Unsubscribe"] = "<mailto:radmila.pokorna@example.test>"
        message.set_content(BASE)
        message.add_attachment(b"data", maintype="application", subtype="octet-stream", filename=f"{SIDE}.txt")
        message.add_attachment(docx_bytes(), maintype="application", subtype="octet-stream", filename=f"{AUTHOR}.docx")
        self.assert_clean({"a.eml": message.as_bytes()}, VALUES + [Value("EMAIL", "radmila.pokorna@example.test")])
        (output,) = [path for path in self.outputs() if path.suffix == ".eml"]
        data = output.read_bytes().lower()
        if True:
            for header in (b"received:", b"x-mailer", b"user-agent", b"message-id", b"return-path", b"x-originating-ip",
                           b"thread-topic", b"organization", b"list-unsubscribe"):
                self.assertNotIn(header, data)
            self.assertEqual(re.findall(rb'filename="([^"]*)"', data).__len__(), len(re.findall(rb'filename="priloha-\d{4}\.', data)))

    def test_attachment_metadata_is_dropped(self):
        # An attached DOCX keeps no metadata or comments, whatever the carrier is.
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = "a@example.test", "b@example.test", "x"
        message["Date"] = "Tue, 16 Jun 2026 09:30:00 +0200"
        message.set_content(BASE)
        message.add_attachment(docx_bytes(comments=True), maintype="application", subtype="octet-stream", filename="p.docx")
        self.assert_clean({"a.eml": message.as_bytes()})


class NamesTests(Batch):
    def archive(self, members):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.comment = SIDE.encode()
            for name, data in members.items():
                info = zipfile.ZipInfo(name)
                info.comment = COMMENT.encode()
                archive.writestr(info, data)
        return stream.getvalue()

    def test_control_zip_has_fixed_name_and_numbered_members(self):
        members = {f"{AUTHOR}/{EDITOR}.docx": docx_bytes(comments=True), f"{SIDE}.txt": f"Text {SIDE}".encode(),
                   "Spis/Tadeáš Wolf.xlsx": plain_xlsx([[BASE]])}
        self.run_batch({f"{HIDDEN} spis.zip": self.archive(members)})
        self.assertFalse(self.errors)
        self.assertEqual([path.name for path in self.outputs()], ["spis_kontrola.zip"])
        with zipfile.ZipFile(self.outputs()[0]) as archive:
            self.assertEqual(sorted(archive.namelist()), ["polozka-0001.docx", "polozka-0002.txt", "polozka-0003.xlsx"])
            self.assertEqual(archive.comment, b"")
            self.assertFalse(any(info.comment for info in archive.infolist()))
        self.assertEqual(self.leaks(VALUES + [WOLF]), {})
        # A second run does not overwrite the first archive.
        self.run_batch({})
        self.assertEqual([path.name for path in self.outputs()], ["spis_kontrola.zip", "spis_kontrola_2.zip"])

    def test_standalone_names_are_cleaned_by_safe_name(self):
        rules = Rules()
        rules.replace(f"{AUTHOR} {RC.text}")
        cleaned = safe_name("Tadeáš Wolf RČ 850314-1237 tel 721 345 678", rules)
        self.assertEqual(scan_texts([cleaned], [WOLF, RC, PHONE]), {})
        self.assertNotEqual(cleaned, "Tadeáš Wolf RČ 850314-1237 tel 721 345 678")
        self.run_batch({"Tadeáš Wolf 850314-1237.txt": b"Text", "Slozíka/Tadeáš Wolf.txt": b"Text"} if False else
                       {"Tadeáš Wolf 850314-1237.txt": f"Spis {BASE}. Tadeáš Wolf, RČ {RC.text}".encode()})
        self.assertFalse(self.errors)
        self.assertEqual(self.leaks([WOLF, RC, Value("PRIJMENI", "Wolf")]), {}, "Název souboru obsahuje osobní údaj.")


class PrinterSettingsTests(Batch):
    def test_printer_settings_part_is_dropped(self):
        data = xlsx_bytes([dict(name="Přehled", rows=[([BASE], False)])])
        settings = b"\x00\x01\\\\PRINTSRV\\Radmila Pokorna-HP\x00" * 3
        data = rezip(data, {
            "xl/printerSettings/printerSettings1.bin": settings,
            "xl/worksheets/_rels/sheet1.xml.rels": relationships([("rIdP", "printerSettings", "../printerSettings/printerSettings1.bin")]),
            "xl/worksheets/sheet1.xml": lambda raw: raw.replace(b"</worksheet>", f'<pageSetup orientation="landscape" r:id="rIdP"/></worksheet>'.encode()),
            "[Content_Types].xml": lambda raw: raw.replace(b"</Types>", b'<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"/></Types>'),
        })
        self.assert_clean({"a.xlsx": data})
        names = self.names(".xlsx")
        self.assertFalse([name for name in names if "printerSettings" in name])
        sheet = self.member(".xlsx", "xl/worksheets/sheet1.xml")
        self.assertIn(b'orientation="landscape"', sheet)
        self.assertNotIn(b"r:id", sheet)
        self.assertNotIn(b"printerSettings", self.member(".xlsx", "xl/worksheets/_rels/sheet1.xml.rels") or b"")


@needs_ocr
class RtfPictureTests(Batch):
    def test_picture_size_words_stay_intact(self):
        self.run_batch({"a.rtf": rtf_with_picture(plain_pixmap().tobytes("png"))})
        self.assertFalse(self.errors)
        (path,) = self.outputs()
        self.assertRegex(path.read_bytes(), rb"\\pich40\n[0-9a-f]{128}\n", "Data obrázku se slila s rozměrem v RTF.")
