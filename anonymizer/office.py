"""Redact supported Office XML parts while retaining their formatting."""
import re
from datetime import date, timedelta
from zipfile import ZipFile

from lxml import etree
import pymupdf as pdf

from .czech import column_kind
from .documents import MAX_PIXELS, recognize_image, redact_geometry

WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
BLOCKS = {
    ".docx": (WORD, "p"),
    ".xlsx": (SHEET, "si"),
    ".pptx": (DRAW, "p"),
}
UNSUPPORTED = re.compile(r"(?:^|/)(?:embeddings|activeX|externalLinks|pivotCache|queryTables|threadedComments|diagrams|_xmlsignatures)(?:/|$)|vbaProject", re.I)
MEDIA = re.compile(r"^(?:word|xl|ppt)/media/[^/]+\.(png|jpe?g|bmp)$", re.I)
DISABLED_LINK = "https://example.invalid/"


WORKSHEET = re.compile(r"xl/worksheets/[^/]+\.xml")


def xml_root(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser=parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("Dokument obsahuje nepodporované DTD.")
    return root


def column_index(reference):
    letters = re.match(r"[A-Z]*", reference or "")[0]
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index or None


def sheet_cells(root):
    """Yield (column, cell) in document order, honouring omitted references."""
    for row in root.iter(f"{{{SHEET}}}row"):
        column = 0
        for cell in row.iterchildren(f"{{{SHEET}}}c"):
            column = column_index(cell.get("r")) or column + 1
            yield column, cell


class StructuredColumns:
    """Label whole spreadsheet cells in columns whose header names personal data."""

    def __init__(self, original, rules):
        self.rules = rules
        self.shared = []
        self.shared_labels = {}
        names = set(original.namelist())
        if "xl/sharedStrings.xml" in names:
            root = xml_root(original.read("xl/sharedStrings.xml"))
            self.shared = ["".join(node.text or "" for node in item.iter(f"{{{SHEET}}}t")
                                   if not any(etree.QName(parent).localname == "rPh" for parent in node.iterancestors()))
                           for item in root.iterchildren(f"{{{SHEET}}}si")]
        workbook = xml_root(original.read("xl/workbook.xml"))
        properties = workbook.find(f"{{{SHEET}}}workbookPr")
        self.epoch = date(1904, 1, 1) if properties is not None and properties.get("date1904") in {"1", "true"} else date(1899, 12, 30)
        # Plan shared strings first: their part can precede or follow the sheets.
        for name in sorted(names):
            if WORKSHEET.fullmatch(name):
                self.apply(xml_root(original.read(name)), modify=False)

    def text(self, cell):
        kind = cell.get("t")
        value = cell.find(f"{{{SHEET}}}v")
        if kind == "s":
            try:
                return self.shared[int(value.text)]
            except (AttributeError, IndexError, TypeError, ValueError):
                return ""
        if kind == "inlineStr":
            return "".join(node.text or "" for node in cell.iter(f"{{{SHEET}}}t"))
        return value.text if value is not None and value.text else ""

    def value(self, cell, kind):
        text = self.text(cell)
        if kind == "NAROZENI" and cell.get("t") in {None, "n"}:
            try:
                serial = float(text)
            except ValueError:
                return text
            if 0 < serial < 2_958_466 and serial.is_integer():
                return (self.epoch + timedelta(days=int(serial))).isoformat()
        return text

    def apply(self, root, modify=True):
        columns = {}
        for column, cell in sheet_cells(root):
            header = column_kind(self.text(cell)) if cell.get("t") in {"s", "inlineStr", "str"} else None
            if header:
                columns[column] = header
                continue
            kind = columns.get(column)
            if not kind or cell.get("t") in {"b", "e"}:
                continue
            if cell.find(f"{{{SHEET}}}f") is not None:
                if self.text(cell).strip() or cell.get("t") == "str":
                    raise ValueError("Vzorec ve sloupci s osobními údaji vyžaduje ruční kontrolu.")
                continue
            label = self.rules.cell(kind, self.value(cell, kind))
            if not label:
                continue
            if cell.get("t") == "s":
                self.shared_labels.setdefault(int(cell.find(f"{{{SHEET}}}v").text), label)
            elif modify:
                for child in list(cell):
                    if etree.QName(child).localname in {"v", "is"}:
                        cell.remove(child)
                cell.set("t", "inlineStr")
                inline = etree.SubElement(cell, f"{{{SHEET}}}is")
                etree.SubElement(inline, f"{{{SHEET}}}t").text = label

    def redact_shared(self, root):
        for index, item in enumerate(root.iterchildren(f"{{{SHEET}}}si")):
            if index in self.shared_labels:
                for child in list(item):
                    item.remove(child)
                etree.SubElement(item, f"{{{SHEET}}}t").text = self.shared_labels[index]


def stripped_part(name):
    return name.startswith("customXml/") or name.startswith("docProps/thumbnail.")


def redact_nodes(nodes, rules):
    values = [node.text or "" for node in nodes]
    text = "".join(values)
    offsets = []
    end = 0
    for value in values:
        offsets.append(end)
        end += len(value)
    for match in reversed(rules.find(text)):
        label = rules.label(match)
        for index, offset in enumerate(offsets):
            left = max(match.start - offset, 0)
            right = min(match.end - offset, len(values[index]))
            if right <= left:
                continue
            value = nodes[index].text or ""
            nodes[index].text = value[:left] + label + value[right:]
            if nodes[index].text.strip() != nodes[index].text:
                nodes[index].set(XML_SPACE, "preserve")
            label = ""


def text_nodes(block, namespace, block_name):
    tag = f"{{{namespace}}}t"
    for node in block.iterdescendants(tag):
        if next(node.iterancestors(block_name), None) is block:
            yield node


def office_blocks(root, suffix):
    namespace, local = BLOCKS[suffix]
    name = f"{{{namespace}}}{local}"
    for block in root.iter(name):
        yield list(text_nodes(block, namespace, name))
    if suffix == ".xlsx":
        for block in root.iter(f"{{{SHEET}}}is"):
            yield list(text_nodes(block, SHEET, f"{{{SHEET}}}is"))
        for cell in root.iter(f"{{{SHEET}}}c"):
            if cell.get("t") == "str":
                yield list(cell.findall(f"{{{SHEET}}}v"))
        for tag in ("oddHeader", "evenHeader", "firstHeader", "oddFooter", "evenFooter", "firstFooter"):
            for node in root.iter(f"{{{SHEET}}}{tag}"):
                yield [node]
    # DrawingML text can also occur in Word and spreadsheet drawings.
    if suffix != ".pptx":
        name = f"{{{DRAW}}}p"
        for block in root.iter(name):
            yield list(text_nodes(block, DRAW, name))
    chart = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    for cache in root.iter(chart + "strCache", chart + "strLit", chart + "multiLvlStrCache"):
        for node in cache.iter(chart + "v"):
            yield [node]


def redact_image(data, extension, rules, tessdata):
    if extension == "bmp":
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        bitmap = QImage.fromData(data, "BMP")
        if bitmap.isNull() or bitmap.width() * bitmap.height() > MAX_PIXELS:
            raise ValueError("Vložený BMP je poškozený nebo příliš velký.")
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not bitmap.save(buffer, "PNG"):
            raise ValueError("Vložený BMP nelze převést pro OCR.")
        redacted = redact_image(bytes(buffer.data()), "png", rules, tessdata)
        buffer.close()
        buffer.setData(b"")
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not QImage.fromData(redacted, "PNG").save(buffer, "BMP"):
            raise ValueError("Anonymizovaný BMP nelze uložit.")
        return bytes(buffer.data())
    with pdf.open(stream=data, filetype=extension) as image:
        if image.page_count != 1:
            raise ValueError("Vložený obrázek má více snímků.")
        page = image[0]
        images = page.get_image_info()
        if len(images) != 1:
            raise ValueError("Vložený obrázek nelze vykreslit.")
        info = images[0]
        width, height = info["width"], info["height"]
        if width * height > MAX_PIXELS:
            raise ValueError("Vložený obrázek překračuje limit velikosti.")
        matrix = pdf.Matrix(width / page.rect.width, height / page.rect.height)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    content, geometry, transform = recognize_image(pixmap, tessdata)
    if not content.strip():
        # OCR cannot distinguish a decorative image from unreadable sensitive text.
        pixmap.clear_with(0)
    else:
        redact_geometry(pixmap, rules, content, geometry, transform)
    return pixmap.tobytes("png" if extension == "png" else "jpeg", jpg_quality=100)


def redact_office(source, destination, rules, tessdata=None):
    suffix = source.suffix.lower()
    main = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml"}[suffix]
    with ZipFile(source) as original, ZipFile(destination, "w") as result:
        names = original.namelist()
        if main not in names or len(names) != len(set(names)):
            raise ValueError("Neplatný dokument Office.")
        if any(UNSUPPORTED.search(name) for name in names):
            raise ValueError("Soubor obsahuje nepodporované obrázky, přílohy, grafy, podpisy nebo externí data.")
        if sum(entry.file_size for entry in original.infolist()) > 150_000_000:
            raise ValueError("Rozbalený dokument Office překračuje limit velikosti.")
        columns = StructuredColumns(original, rules) if suffix == ".xlsx" else None
        for entry in original.infolist():
            name = entry.filename
            if stripped_part(name):
                continue
            data = original.read(entry)
            media = MEDIA.fullmatch(name)
            if media:
                data = redact_image(data, media[1].lower(), rules, tessdata)
            elif "/media/" in name:
                raise ValueError("Vložený obrázek má nepodporovaný formát.")
            elif name.endswith((".xml", ".rels", ".vml")):
                root = xml_root(data)
                if name == "[Content_Types].xml" or name.endswith(".rels"):
                    for node in list(root):
                        part = (node.get("PartName") or node.get("Target") or "").removeprefix("../").lstrip("/")
                        if stripped_part(part):
                            root.remove(node)
                        elif node.get("TargetMode") == "External":
                            if node.get("Type", "").endswith("/hyperlink"):
                                node.set("Target", DISABLED_LINK)
                            else:
                                raise ValueError("Soubor načítá externí data nebo obrázek. Vložte je přímo do dokumentu.")
                elif name.startswith("docProps/"):
                    for node in root:
                        if name.endswith("custom.xml"):
                            root.remove(node)
                        elif etree.QName(node).localname in {
                            "creator", "lastModifiedBy", "title", "subject", "description",
                            "keywords", "category", "identifier", "Company", "Manager",
                        }:
                            root.remove(node)
                else:
                    if columns and name == "xl/sharedStrings.xml":
                        columns.redact_shared(root)
                    elif columns and WORKSHEET.fullmatch(name):
                        columns.apply(root)
                    for nodes in office_blocks(root, suffix):
                        redact_nodes(nodes, rules)
                    for node in root.iter():
                        if not isinstance(node.tag, str):
                            continue
                        local = etree.QName(node).localname
                        if local in {"delText", "delInstrText"}:
                            node.text = ""
                        if local in {"instrText", "fldSimple"}:
                            instruction = next((attr for attr in node.attrib if etree.QName(attr).localname == "instr"), None)
                            code = node.get(instruction, "") if instruction else node.text or ""
                            if re.search(r"\bHYPERLINK\b", code, re.I):
                                replacement = ' HYPERLINK "' + DISABLED_LINK + '" '
                                if instruction:
                                    node.set(instruction, replacement)
                                else:
                                    node.text = replacement
                        if local in {"f", "formula", "formula1", "formula2", "definedName", "instrText"}:
                            if rules.find(node.text or ""):
                                raise ValueError("Vzorec nebo pole obsahuje citlivý text. Zkontrolujte jej ručně.")
                        if local == "sheet" and rules.find(node.get("name", "")):
                            raise ValueError("Název listu obsahuje citlivý údaj. Přejmenujte jej před zpracováním.")
                        for attr in list(node.attrib):
                            attr_name = etree.QName(attr).localname
                            if attr_name in {"author", "initials", "userName", "lastModifiedBy", "descr", "tooltip"}:
                                node.set(attr, "")
                    for nodes in office_blocks(root, suffix):
                        if rules.find("".join(node.text or "" for node in nodes)):
                            raise ValueError("Ve výstupu zůstal detekovatelný text.")
                    for node in root.iter():
                        if rules.find(node.text or "") or rules.find(node.tail or ""):
                            raise ValueError("Citlivý údaj zůstal v části Office, kterou nelze bezpečně upravit.")
                        if not isinstance(node.tag, str):
                            continue
                        for attr, value in node.attrib.items():
                            if etree.QName(attr).localname in {"name", "displayName", "title", "prompt", "error"} and rules.find(value):
                                raise ValueError("Citlivý údaj v názvu nebo popisku vyžaduje ruční úpravu.")
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            result.writestr(entry, data)
