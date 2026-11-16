"""Redact OpenDocument text, spreadsheet and presentation files (ODT, ODS, ODP)."""
import re
from zipfile import ZIP_STORED, ZipFile

from lxml import etree

from .czech import plan_table

NS = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0",
}
TEXT, TABLE, OFFICE = (f"{{{NS[name]}}}" for name in ("text", "table", "office"))
MIMETYPES = {
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}
BLOCKS = {TEXT + "p", TEXT + "h"}
# Inline elements that stand for whitespace inside a paragraph.
SPACES = {TEXT + "s": " ", TEXT + "tab": " ", TEXT + "line-break": " "}
# Elements whose content is not part of the surrounding paragraph text.
NESTED = {TEXT + "note", OFFICE + "annotation"}
UNSUPPORTED = re.compile(r"^(?:Object|Basic|Scripts)|^META-INF/.*signature", re.I)
# User-interface settings and previews that are not needed in the output.
SKIPPED = ("Thumbnails/", "Configurations2/")
IMAGE = re.compile(r"^Pictures/[^/]+\.(png|jpe?g)$", re.I)
META_PRIVATE = {"creator", "initial-creator", "title", "subject", "description", "keyword",
                "user-defined", "printed-by", "template"}
DISABLED_LINK = "https://example.invalid/"
VALUE_ATTRIBUTES = [f"{{{NS[prefix]}}}{name}" for prefix in ("office", "calcext") for name in
                    ("value", "date-value", "time-value", "boolean-value", "string-value", "value-type")]


def identify_odf(archive):
    names = archive.namelist()
    if names and names[0] == "mimetype":
        return MIMETYPES.get(archive.read("mimetype").decode("ascii", "replace").strip())
    return None


class Segment:
    """A piece of paragraph text stored in an element's text or tail."""

    def __init__(self, node, attribute, fixed=None):
        self.node, self.attribute, self.fixed = node, attribute, fixed

    @property
    def value(self):
        if self.fixed is not None:
            return self.fixed
        return getattr(self.node, self.attribute) or ""

    @value.setter
    def value(self, text):
        if self.fixed is None:
            setattr(self.node, self.attribute, text)
            return
        # A replaced space element is removed; its new text and tail move to the
        # previous sibling's tail (or the parent's text).
        node, parent = self.node, self.node.getparent()
        moved = text + (node.tail or "")
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + moved
        else:
            parent.text = (parent.text or "") + moved
        parent.remove(node)


def segments(block):
    found = [Segment(block, "text")]

    def visit(node):
        for child in node:
            if not isinstance(child.tag, str):
                found.append(Segment(child, "tail"))
                continue
            if child.tag in NESTED or child.tag in BLOCKS:
                found.append(Segment(child, "tail"))
                continue
            if child.tag in SPACES:
                count = int(child.get(TEXT + "c", "1")) if child.tag == TEXT + "s" else 1
                found.append(Segment(child, None, SPACES[child.tag] * max(1, min(count, 1000))))
            else:
                found.append(Segment(child, "text"))
                visit(child)
            found.append(Segment(child, "tail"))

    visit(block)
    return found


def redact_block(block, rules):
    parts = segments(block)
    values = [part.value for part in parts]
    text = "".join(values)
    offsets, end = [], 0
    for value in values:
        offsets.append(end)
        end += len(value)
    changes = {}
    for match in reversed(rules.find(text)):
        label = rules.label(match)
        for index, offset in enumerate(offsets):
            left, right = max(match.start - offset, 0), min(match.end - offset, len(values[index]))
            if right <= left:
                continue
            values[index] = values[index][:left] + label + values[index][right:]
            changes[index] = values[index]
            label = ""
    # Plain text first; space elements are removed afterwards, last first, so
    # the text they move is not overwritten.
    for index in sorted(changes):
        if parts[index].fixed is None:
            parts[index].value = changes[index]
    for index in sorted(changes, reverse=True):
        if parts[index].fixed is not None:
            parts[index].value = changes[index]


def block_text(block):
    return "".join(part.value for part in segments(block))


def table_rows(table):
    for row in table.iter(TABLE + "table-row"):
        if next(row.iterancestors(TABLE + "table"), None) is not table:
            continue
        cells, column = [], 0
        for cell in row:
            if cell.tag not in {TABLE + "table-cell", TABLE + "covered-table-cell"}:
                continue
            repeat = max(1, min(int(cell.get(TABLE + "number-columns-repeated", "1")), 16384))
            if cell.tag == TABLE + "table-cell":
                paragraphs = [node for node in cell.iter(*BLOCKS)
                              if next(node.iterancestors(TABLE + "table-cell"), None) is cell]
                cells.append((column, cell, " ".join(block_text(node) for node in paragraphs), paragraphs))
            column += repeat
        yield cells


def label_cell(cell, paragraphs, kind, text, rules):
    value = text
    date = cell.get(OFFICE + "date-value")
    if kind == "NAROZENI" and date:
        value = date[:10]
    label = rules.cell(kind, value)
    if not label:
        return
    if cell.get(TABLE + "formula"):
        raise ValueError("Vzorec ve sloupci s osobními údaji vyžaduje ruční kontrolu.")
    for attribute in VALUE_ATTRIBUTES:
        cell.attrib.pop(attribute, None)
    cell.set(OFFICE + "value-type", "string")
    first = paragraphs[0]
    for child in list(first):
        first.remove(child)
    first.text = label
    for paragraph in paragraphs[1:]:
        paragraph.getparent().remove(paragraph)


def redact_tables(root, rules):
    for table in root.iter(TABLE + "table"):
        rows = list(table_rows(table))
        plan = plan_table([[(column, text) for column, _, text, _ in cells] for cells in rows])
        for (index, position), kind in plan.items():
            _, cell, text, paragraphs = rows[index][position]
            if paragraphs:
                label_cell(cell, paragraphs, kind, text, rules)


def check(root, rules):
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if rules.find(node.text or "") or rules.find(node.tail or ""):
            raise ValueError("Citlivý údaj zůstal v části dokumentu, kterou nelze bezpečně upravit.")
        for attribute, value in node.attrib.items():
            if rules.find(value):
                raise ValueError("Citlivý údaj zůstal v atributu dokumentu. Zkontrolujte jej ručně.")
    for block in root.iter(*BLOCKS):
        if rules.find(block_text(block)):
            raise ValueError("Ve výstupu zůstal detekovatelný text.")


def redact_xml(root, rules):
    href = f"{{{NS['xlink']}}}href"
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        local = etree.QName(node).localname
        target = node.get(href)
        if target and not target.startswith("#"):
            if local == "a":
                node.set(href, DISABLED_LINK)
            elif re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                raise ValueError("Dokument načítá externí obrázek nebo data. Vložte je přímo do dokumentu.")
        if local == "annotation":
            for child in list(node):
                if etree.QName(child).localname in {"creator", "date", "initials"}:
                    child.text = ""
        if local == "changed-region":
            for child in node.iter():
                if etree.QName(child).localname in {"creator", "date"}:
                    child.text = ""
    redact_tables(root, rules)
    for block in list(root.iter(*BLOCKS)):
        redact_block(block, rules)
    check(root, rules)


def redact_odf(source, destination, rules, tessdata=None, redact_image=None):
    with ZipFile(source) as original, ZipFile(destination, "w") as result:
        if not identify_odf(original):
            raise ValueError("Neplatný dokument OpenDocument.")
        names = original.namelist()
        if len(names) != len(set(names)) or sum(entry.file_size for entry in original.infolist()) > 150_000_000:
            raise ValueError("Dokument OpenDocument je poškozený nebo příliš velký.")
        if any(UNSUPPORTED.match(name) for name in names):
            raise ValueError("Dokument obsahuje vložené objekty nebo makra. Odstraňte je před zpracováním.")
        for entry in original.infolist():
            name = entry.filename
            if name.startswith(SKIPPED) or entry.is_dir():
                continue
            data = original.read(entry)
            if name == "mimetype":
                result.writestr(entry, data, compress_type=ZIP_STORED)
                continue
            image = IMAGE.match(name)
            if image:
                data = redact_image(data, "png" if image[1].lower() == "png" else "jpg", rules, tessdata)
            elif name.startswith("Pictures/"):
                raise ValueError("Dokument obsahuje obrázek v nepodporovaném formátu. Použijte PNG nebo JPEG.")
            elif name.endswith(".xml") or name.endswith(".rdf"):
                root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
                if root.getroottree().docinfo.doctype:
                    raise ValueError("Dokument obsahuje nepodporované DTD.")
                if name == "META-INF/manifest.xml":
                    entry_tag = f"{{{NS['manifest']}}}file-entry"
                    for node in list(root.iter(entry_tag)):
                        path = node.get(f"{{{NS['manifest']}}}full-path", "")
                        if path.startswith(SKIPPED):
                            node.getparent().remove(node)
                        if any(etree.QName(child).localname == "encryption-data" for child in node):
                            raise ValueError("Dokument je zašifrovaný.")
                elif name == "meta.xml":
                    for node in list(root.iter()):
                        if isinstance(node.tag, str) and etree.QName(node).localname in META_PRIVATE:
                            node.getparent().remove(node)
                    check(root, rules)
                else:
                    redact_xml(root, rules)
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            elif name.startswith("META-INF/") or name.endswith(".rdf"):
                pass
            else:
                raise ValueError("Dokument obsahuje nepodporovanou část.")
            result.writestr(entry, data)
