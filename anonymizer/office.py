"""Redact supported Office XML parts while retaining their formatting."""
import re
from datetime import date, timedelta
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from lxml import etree
import pymupdf as pdf

from .czech import plan_table
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
UNSUPPORTED = re.compile(r"(?:^|/)(?:activeX|externalLinks|pivotCache|queryTables|threadedComments)(?:/|$)|vbaProject", re.I)
# Raster formats that OCR can read after conversion to PNG.
CONVERTED_MEDIA = re.compile(r"^(?:word|xl|ppt)/media/[^/]+\.(gif|tiff?)$", re.I)
EMBEDDED = re.compile(r"^(?:word|xl|ppt)/embeddings/[^/]+\.(docx|xlsx|pptx)$", re.I)
# External relationships that only point to the author's computer and can be dropped.
DROPPED_EXTERNAL = ("/attachedTemplate", "/mailMergeSource", "/mailMergeHeaderSource", "/recipientData")
RELATIONSHIP_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
CLEARED_ATTRIBUTES = {"author", "initials", "userName", "lastModifiedBy", "descr", "tooltip", "userId",
                      "providerId", "title"}
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


def sheet_rows(root):
    """Yield rows as [(column, cell)], honouring omitted cell references."""
    for row in root.iter(f"{{{SHEET}}}row"):
        cells, column = [], 0
        for cell in row.iterchildren(f"{{{SHEET}}}c"):
            column = column_index(cell.get("r")) or column + 1
            cells.append((column, cell))
        yield cells


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
        rows = list(sheet_rows(root))
        plan = plan_table([[(column, self.text(cell)) for column, cell in cells] for cells in rows])
        for (index, i), kind in plan.items():
            self.label_cell(rows[index][i][1], kind, modify)

    def label_cell(self, cell, kind, modify):
        if not kind or cell.get("t") in {"b", "e"}:
            return
        if cell.find(f"{{{SHEET}}}f") is not None:
            if self.text(cell).strip() or cell.get("t") == "str":
                raise ValueError("Vzorec ve sloupci s osobními údaji vyžaduje ruční kontrolu.")
            return
        label = self.rules.cell(kind, self.value(cell, kind))
        if not label:
            return
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


def redact_tables(root, rules):
    """Label whole Word and PowerPoint table cells under a personal-data header."""
    for namespace in (WORD, DRAW):
        table_tag = f"{{{namespace}}}tbl"
        for table in root.iter(table_tag):
            rows = []
            for row in table.iterchildren(f"{{{namespace}}}tr"):
                cells, column = [], 0
                for cell in row.iterchildren(f"{{{namespace}}}tc"):
                    if namespace == WORD:
                        span = cell.find(f"{{{WORD}}}tcPr/{{{WORD}}}gridSpan")
                        width = span.get(f"{{{WORD}}}val") if span is not None else None
                    else:
                        # DrawingML keeps an hMerge placeholder for every spanned column.
                        width = None
                        if cell.get("hMerge") in {"1", "true"}:
                            column += 1
                            continue
                    nodes = [node for node in cell.iter(f"{{{namespace}}}t")
                             if next(node.iterancestors(table_tag), None) is table]
                    cells.append((column, nodes, "".join(node.text or "" for node in nodes)))
                    column += int(width) if width and width.isdecimal() else 1
                rows.append(cells)
            plan = plan_table([[(column, text) for column, _, text in cells] for cells in rows])
            for (index, i), kind in plan.items():
                _, nodes, text = rows[index][i]
                label = rules.cell(kind, text)
                if label:
                    # The first run keeps its formatting; the rest of the cell is emptied.
                    for position, node in enumerate(nodes):
                        node.text = label if position == 0 else ""


def stripped_part(name):
    """Parts left out of the output: custom data, previews, signatures, co-author list."""
    return (name.startswith(("customXml/", "docProps/thumbnail.", "_xmlsignatures/"))
            or re.search(r"(?:^|/)people\.xml$", name) is not None)


def black_png():
    pixmap = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 16, 16), False)
    pixmap.clear_with(0)
    return pixmap.tobytes("png")


def to_png(data):
    """Render the first frame of a GIF or TIFF image as PNG for OCR."""
    with pdf.open(stream=data) as image:
        if image.page_count < 1:
            raise ValueError("Vložený obrázek nelze vykreslit.")
        page = image[0]
        if page.rect.width * page.rect.height > MAX_PIXELS:
            raise ValueError("Vložený obrázek překračuje limit velikosti.")
        return page.get_pixmap(alpha=False).tobytes("png")


def resolve(base, target):
    """Resolve a relationship target relative to the folder of its source part."""
    parts = [part for part in base.split("/") if part]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def relative(base, name):
    """Express a package path relative to the folder of a source part."""
    base_parts = [part for part in base.split("/") if part]
    name_parts = name.split("/")
    common = 0
    while common < min(len(base_parts), len(name_parts) - 1) and base_parts[common] == name_parts[common]:
        common += 1
    return "/".join([".."] * (len(base_parts) - common) + name_parts[common:])


def plan_package(original):
    """Find media to rename to PNG and external relationships to drop."""
    names = original.namelist()
    renamed = {}
    for name in names:
        if "/media/" in name and not MEDIA.fullmatch(name):
            stem, _, extension = name.rpartition(".")
            candidate = f"{stem}_{extension.lower()}.png"
            while candidate in names or candidate in renamed.values():
                candidate = candidate[:-4] + "_.png"
            renamed[name] = candidate
    dropped = {}
    for name in names:
        if not name.endswith(".rels"):
            continue
        folder, _, rels = name.rpartition("_rels/")
        source = folder + rels.removesuffix(".rels")
        for node in xml_root(original.read(name)):
            if node.get("TargetMode") == "External" and node.get("Type", "").endswith(DROPPED_EXTERNAL):
                dropped.setdefault(source, set()).add(node.get("Id"))
    return renamed, dropped


def redact_fields(root, rules):
    """Redact field instructions; Word often splits one instruction into many runs."""
    disabled = ' HYPERLINK "' + DISABLED_LINK + '" '
    groups, stack = [], []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        local = etree.QName(node).localname
        if local == "fldChar":
            kind = next((value for attr, value in node.attrib.items()
                         if etree.QName(attr).localname == "fldCharType"), "")
            if kind == "begin":
                stack.append([])
                groups.append(stack[-1])
            elif kind in {"separate", "end"} and stack:
                stack.pop()
        elif local == "instrText" and stack:
            stack[-1].append(node)
        elif local == "fldSimple":
            instruction = next((attr for attr in node.attrib if etree.QName(attr).localname == "instr"), None)
            if instruction:
                code = node.get(instruction)
                if re.search(r"\bHYPERLINK\b", code, re.I):
                    node.set(instruction, disabled)
                else:
                    node.set(instruction, rules.replace(code)[0])
    for nodes in groups:
        code = "".join(node.text or "" for node in nodes)
        if re.search(r"\bHYPERLINK\b", code, re.I):
            for index, node in enumerate(nodes):
                node.text = disabled if index == 0 else ""
        else:
            redact_nodes(nodes, rules)
    # Instruction text outside a recognised field still goes through the rules.
    for node in root.iter(f"{{{WORD}}}instrText"):
        node.text = rules.replace(node.text or "")[0]


def redact_nodes(nodes, rules):
    # A replacement can expose a new match (e.g. a name next to a label), so
    # repeat until the text is stable.
    for _ in range(3):
        if not redact_nodes_once(nodes, rules):
            break


def redact_nodes_once(nodes, rules):
    values = [node.text or "" for node in nodes]
    text = "".join(values)
    offsets = []
    end = 0
    for value in values:
        offsets.append(end)
        end += len(value)
    findings = rules.find(text)
    for match in reversed(findings):
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
    return bool(findings)


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


def redact_relationships(name, root, renamed):
    """Fix relationship targets and content types for dropped and renamed parts."""
    if name == "[Content_Types].xml":
        namespace = etree.QName(root).namespace
        for node in list(root):
            part = (node.get("PartName") or "").lstrip("/")
            if part and stripped_part(part):
                root.remove(node)
            elif part in renamed:
                node.set("PartName", "/" + renamed[part])
                node.set("ContentType", "image/png")
        defaults = {(node.get("Extension") or "").lower() for node in root if node.get("Extension")}
        if renamed and "png" not in defaults:
            etree.SubElement(root, f"{{{namespace}}}Default", Extension="png", ContentType="image/png")
        return
    folder, _, rels = name.rpartition("_rels/")
    for node in list(root):
        target = node.get("Target") or ""
        if node.get("TargetMode") == "External":
            if node.get("Type", "").endswith("/hyperlink"):
                node.set("Target", DISABLED_LINK)
            elif node.get("Type", "").endswith(DROPPED_EXTERNAL):
                root.remove(node)
            else:
                raise ValueError("Soubor načítá externí data nebo obrázek. Vložte je přímo do dokumentu.")
            continue
        full = target.lstrip("/") if target.startswith("/") else resolve(folder, target)
        if stripped_part(full):
            root.remove(node)
        elif full in renamed:
            node.set("Target", "/" + renamed[full] if target.startswith("/") else relative(folder, renamed[full]))


def redact_embedded(data, extension, rules, tessdata):
    """Redact an embedded Office document (for example chart data or a pasted table)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="SpisRedakt-embedded-") as folder:
        source = Path(folder) / ("input." + extension)
        target = Path(folder) / ("output." + extension)
        source.write_bytes(data)
        redact_office(source, target, rules, tessdata)
        return target.read_bytes()


def redact_part(name, root, suffix, rules, columns, dropped):
    for node in list(root.iter()):
        if not isinstance(node.tag, str) or node.getparent() is None:
            continue
        # Template paths and mail-merge sources name the author's computer.
        if etree.QName(node).localname in {"attachedTemplate", "mailMerge"} or node.get(RELATIONSHIP_ID) in dropped:
            node.getparent().remove(node)
    if columns and name == "xl/sharedStrings.xml":
        columns.redact_shared(root)
    elif columns and WORKSHEET.fullmatch(name):
        columns.apply(root)
    elif suffix != ".xlsx":
        redact_tables(root, rules)
    for nodes in office_blocks(root, suffix):
        redact_nodes(nodes, rules)
    redact_fields(root, rules)
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        local = etree.QName(node).localname
        if local in {"delText", "delInstrText"}:
            node.text = ""
        if local in {"f", "formula", "formula1", "formula2", "definedName"}:
            if rules.find(node.text or ""):
                raise ValueError("Vzorec nebo pole obsahuje citlivý text. Zkontrolujte jej ručně.")
        if local == "sheet" and rules.find(node.get("name", "")):
            raise ValueError("Název listu obsahuje citlivý údaj. Přejmenujte jej před zpracováním.")
        for attr in list(node.attrib):
            if etree.QName(attr).localname in CLEARED_ATTRIBUTES:
                node.set(attr, "")
    for nodes in office_blocks(root, suffix):
        if rules.find("".join(node.text or "" for node in nodes)):
            raise ValueError("Ve výstupu zůstal detekovatelný text.")
    for node in root.iter():
        if rules.find(node.text or "") or rules.find(node.tail or ""):
            raise ValueError("Citlivý údaj zůstal v části Office, kterou nelze bezpečně upravit.")
        if not isinstance(node.tag, str):
            continue
        for value in node.attrib.values():
            if rules.find(value):
                raise ValueError("Citlivý údaj v názvu nebo popisku vyžaduje ruční úpravu.")


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
        renamed, dropped = plan_package(original)
        columns = StructuredColumns(original, rules) if suffix == ".xlsx" else None
        for entry in original.infolist():
            name = entry.filename
            if stripped_part(name) or entry.is_dir():
                continue
            data = original.read(entry)
            media = MEDIA.fullmatch(name)
            embedded = EMBEDDED.fullmatch(name)
            if media:
                data = redact_image(data, media[1].lower(), rules, tessdata)
            elif name in renamed:
                # GIF and TIFF go through OCR; vector and other formats (EMF, WMF,
                # SVG) cannot be checked and are replaced by a black image.
                if CONVERTED_MEDIA.fullmatch(name):
                    data = redact_image(to_png(data), "png", rules, tessdata)
                else:
                    data = black_png()
                entry = ZipInfo(renamed[name], entry.date_time)
                entry.compress_type = ZIP_DEFLATED
            elif embedded:
                data = redact_embedded(data, embedded[1].lower(), rules, tessdata)
            elif "/embeddings/" in name:
                raise ValueError("Soubor obsahuje nepodporované obrázky, přílohy, grafy, podpisy nebo externí data.")
            elif name.endswith((".xml", ".rels", ".vml")):
                root = xml_root(data)
                if name == "[Content_Types].xml" or name.endswith(".rels"):
                    redact_relationships(name, root, renamed)
                elif name.startswith("docProps/"):
                    for node in list(root):
                        if name.endswith("custom.xml"):
                            root.remove(node)
                        elif etree.QName(node).localname in {
                            "creator", "lastModifiedBy", "title", "subject", "description",
                            "keywords", "category", "identifier", "Company", "Manager", "Template",
                        }:
                            root.remove(node)
                else:
                    redact_part(name, root, suffix, rules, columns, dropped.get(name, set()))
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            result.writestr(entry, data)
