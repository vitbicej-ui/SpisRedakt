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
# Numeric identifiers the application generates; they carry no text of the author and may look like a phone number.
TECHNICAL_ATTRIBUTES = {"id", "spid", "paraId", "textId", "idx", "creationId", "rsid", "rsidR", "rsidRPr", "rsidRDefault",
                        "rsidP", "rsidTr", "rsidSect", "rsidDel", "rsidRoot"}
TECHNICAL_ELEMENTS = {"creationId", "rsid", "rsidRoot"}
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


def spaced_text(cell, table, namespace):
    """Text of a Word or PowerPoint cell with paragraph and line breaks kept as newlines.

    Used only to recognise headings split over several paragraphs; the cell
    value itself is read from the text nodes.
    """
    parts, table_tag = [], f"{{{namespace}}}tbl"
    for node in cell.iter():
        if not isinstance(node.tag, str) or next(node.iterancestors(table_tag), None) is not table:
            continue
        name = etree.QName(node).localname
        if name in {"p", "br", "cr"}:
            parts.append("\n")
        elif name == "tab":
            parts.append(" ")
        elif name == "t":
            parts.append(node.text or "")
    return "".join(parts)


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
                    cells.append((column, nodes, "".join(node.text or "" for node in nodes),
                                  spaced_text(cell, table, namespace)))
                    column += int(width) if width and width.isdecimal() else 1
                rows.append(cells)
            plan = plan_table([[(column, plan_text) for column, _, _, plan_text in cells] for cells in rows])
            for (index, i), kind in plan.items():
                _, nodes, text, _ = rows[index][i]
                label = rules.cell(kind, text)
                if label:
                    # The first run keeps its formatting; the rest of the cell is emptied.
                    for position, node in enumerate(nodes):
                        node.text = label if position == 0 else ""


COMMENT_PARTS = re.compile(r"^(?:word/comments\w*\.xml|xl/comments(?:\d*\.xml|/.*)|xl/persons/.*|ppt/comments/.*"
                           r"|ppt/commentAuthors\.xml|ppt/authors\.xml)$", re.I)
# Package properties name authors, companies, files and sheets; the output keeps none of them.
EMPTY_PROPERTIES = {"docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"}


def stripped_part(name):
    """Parts left out of the output: custom data, previews, signatures, co-author list, comments, printer settings."""
    return (name.startswith(("customXml/", "docProps/thumbnail.", "_xmlsignatures/"))
            or re.search(r"(?:^|/)people\.xml$", name) is not None
            or COMMENT_PARTS.match(name) is not None
            or re.search(r"(?:^|/)(?:printerSettings/|customProperty\d*\.bin$)", name) is not None)


def tag_name(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def truthy(node):
    """Value of an on/off element such as ``w:vanish`` (a missing value means on)."""
    value = node.get(f"{{{WORD}}}val")
    return value is None or value.lower() not in {"0", "false", "off"}


def hidden_styles(original):
    """Ids of Word styles that hide text (directly or through the style they are based on)."""
    if "word/styles.xml" not in original.namelist():
        return set()
    root = xml_root(original.read("word/styles.xml"))
    based, hidden = {}, set()
    for style in root.iter(f"{{{WORD}}}style"):
        identifier = style.get(f"{{{WORD}}}styleId")
        link = style.find(f"{{{WORD}}}basedOn")
        based[identifier] = link.get(f"{{{WORD}}}val") if link is not None else None
        properties = [node for part in ("rPr", "pPr/{%s}rPr" % WORD) for node in style.findall(f"{{{WORD}}}{part}")]
        if any(truthy(node) for item in properties for node in item.iterchildren(f"{{{WORD}}}vanish")):
            hidden.add(identifier)
    changed = True
    while changed:
        changed = False
        for identifier, parent in based.items():
            if parent in hidden and identifier not in hidden:
                hidden.add(identifier)
                changed = True
    return hidden


TRACKED_PROPERTIES = {"rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblGridChange", "trPrChange",
                      "tcPrChange", "tblPrExChange", "numberingChange", "cellIns", "cellDel", "cellMerge"}
REMOVED = {"del", "moveFrom", "delText", "delInstrText", "moveFromRangeStart", "moveFromRangeEnd",
           "moveToRangeStart", "moveToRangeEnd", "commentRangeStart", "commentRangeEnd", "commentReference",
           "annotationRef", "docVars"} | TRACKED_PROPERTIES


def remove_node(node):
    """Remove an element; its tail text stays with the surrounding text."""
    parent = node.getparent()
    previous = node.getprevious()
    if node.tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def unwrap_node(node):
    """Replace an element by its children."""
    parent = node.getparent()
    index = parent.index(node)
    children = list(node)
    previous = node.getprevious()
    lead = node.text or ""
    if lead:
        if previous is not None:
            previous.tail = (previous.tail or "") + lead
        else:
            parent.text = (parent.text or "") + lead
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    if node.tail:
        last = children[-1] if children else previous
        if last is not None:
            last.tail = (last.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def strip_word_layers(root, hidden):
    """Accept tracked insertions, drop deletions, change records and comment marks, remove hidden text.

    Reviewers' names, dates, old wording and hidden text are not part of what a reader sees; the output
    keeps only the accepted visible text.
    """
    for node in list(root.iter()):
        name = tag_name(node)
        if not name or node.getparent() is None:
            continue
        in_properties = tag_name(node.getparent()) in {"rPr", "pPr"}
        if name in REMOVED or (name in {"ins", "moveTo"} and in_properties):
            remove_node(node)
        elif name in {"ins", "moveTo"}:
            unwrap_node(node)
    for run in list(root.iter(f"{{{WORD}}}r")):
        properties = run.find(f"{{{WORD}}}rPr")
        if properties is None or run.getparent() is None:
            continue
        style = properties.find(f"{{{WORD}}}rStyle")
        if (any(truthy(node) for node in properties.iterchildren(f"{{{WORD}}}vanish", f"{{{WORD}}}specVanish"))
                or style is not None and style.get(f"{{{WORD}}}val") in hidden):
            remove_node(run)
    for paragraph in list(root.iter(f"{{{WORD}}}p")):
        properties = paragraph.find(f"{{{WORD}}}pPr")
        style = properties.find(f"{{{WORD}}}pStyle") if properties is not None else None
        if style is not None and style.get(f"{{{WORD}}}val") in hidden:
            for run in list(paragraph.iterchildren(f"{{{WORD}}}r")):
                remove_node(run)
    # What still names hiding would only hide empty paragraph marks.
    for node in list(root.iter(f"{{{WORD}}}vanish", f"{{{WORD}}}specVanish")):
        if node.getparent() is not None:
            remove_node(node)


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
    dropped, extra = {}, set()
    for name in names:
        if not name.endswith(".rels"):
            continue
        folder, _, rels = name.rpartition("_rels/")
        source = folder + rels.removesuffix(".rels")
        nodes = list(xml_root(original.read(name)))
        for node in nodes:
            if node.get("TargetMode") == "External" and node.get("Type", "").endswith(DROPPED_EXTERNAL):
                dropped.setdefault(source, set()).add(node.get("Id"))
        targets = [(node, (node.get("Target") or "").lstrip("/") if (node.get("Target") or "").startswith("/")
                    else resolve(folder, node.get("Target") or "")) for node in nodes]
        if any(stripped_part(full) for _, full in targets):
            # The drawing that holds the note shapes of removed comments goes with them.
            for node, full in targets:
                if stripped_part(full) or node.get("Type", "").endswith("/vmlDrawing"):
                    dropped.setdefault(source, set()).add(node.get("Id"))
                    if node.get("Type", "").endswith("/vmlDrawing"):
                        extra.add(full)
    return renamed, dropped, extra


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
        redact_geometry(pixmap, rules, content, geometry, transform, True)
    return pixmap.tobytes("png" if extension == "png" else "jpeg", jpg_quality=100)


def redact_relationships(name, root, renamed, extra=frozenset()):
    """Fix relationship targets and content types for dropped and renamed parts."""
    if name == "[Content_Types].xml":
        namespace = etree.QName(root).namespace
        for node in list(root):
            part = (node.get("PartName") or "").lstrip("/")
            if part and (stripped_part(part) or part in extra):
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
        if stripped_part(full) or full in extra:
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


def redact_part(name, root, suffix, rules, columns, dropped, hidden=frozenset()):
    if suffix == ".docx" and name.startswith("word/"):
        strip_word_layers(root, hidden)
    for node in list(root.iter()):
        if not isinstance(node.tag, str) or node.getparent() is None:
            continue
        # Template paths and mail-merge sources name the author's computer.
        if tag_name(node) == "pageSetup" and node.get(RELATIONSHIP_ID):
            # The printer settings go (they name the printer and the user); the page setup stays.
            del node.attrib[RELATIONSHIP_ID]
        elif etree.QName(node).localname in {"attachedTemplate", "mailMerge"} or node.get(RELATIONSHIP_ID) in dropped:
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
        local = etree.QName(node).localname
        for attr, value in node.attrib.items():
            name = etree.QName(attr).localname
            if name in TECHNICAL_ATTRIBUTES or (name == "val" and local in TECHNICAL_ELEMENTS):
                continue
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
        if sum(entry.file_size for entry in original.infolist()) > 1_000_000_000:
            raise ValueError("Rozbalený dokument Office překračuje limit velikosti.")
        renamed, dropped, extra = plan_package(original)
        hidden = hidden_styles(original) if suffix == ".docx" else set()
        columns = StructuredColumns(original, rules) if suffix == ".xlsx" else None
        for entry in original.infolist():
            name = entry.filename
            if stripped_part(name) or name in extra or entry.is_dir():
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
                    redact_relationships(name, root, renamed, extra)
                elif name in EMPTY_PROPERTIES:
                    for node in list(root):
                        root.remove(node)
                    root.text = None
                else:
                    redact_part(name, root, suffix, rules, columns, dropped.get(name, set()), hidden)
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            result.writestr(entry, data)
