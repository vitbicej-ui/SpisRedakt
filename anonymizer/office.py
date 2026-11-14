"""Redact supported Office XML parts while retaining their formatting."""
import re
from zipfile import ZipFile

from lxml import etree
import pymupdf as pdf

from . import forms

from .czech import FORM_LABELS, fold
from .documents import MAX_PIXELS, blackout, recheck, recognize_image, redact_geometry
from .forms import FormError
from .rules import Finding, key

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


LABELS = {" ".join(phrase) for phrase in FORM_LABELS}


class Block(list):
    """Text nodes of one paragraph-like element, in document order."""
    element = None


def bare_label(text):
    return " ".join(re.sub(r"[\s:.]+$", "", fold(text)).split()) in LABELS


def redact_labelled_values(blocks, rules, suffix):
    """A block that is only a field label makes the next block its value (table cell or line)."""
    pending = 0
    for nodes in blocks:
        text = "".join(node.text or "" for node in nodes)
        if bare_label(text):
            if suffix != ".docx":
                raise FormError("form_layout_unsupported",
                                "Tabulkový formulář s osobními poli nebyl vydán: hodnoty nelze spolehlivě přiřadit.")
            pending += 1
        elif pending and text.strip():
            rules.remember_literal(text)
            label = rules.label(Finding(0, len(text), "OSOBNI_UDAJ", key(text)))
            for node in nodes:
                node.text, label = label, ""
            pending -= 1
        elif pending and nodes.element is not None and next(nodes.element.iterancestors(f"{{{WORD}}}tc"), None) is not None:
            pending -= 1        # an empty cell is an empty value


def text_nodes(block, namespace, block_name):
    tag = f"{{{namespace}}}t"
    for node in block.iterdescendants(tag):
        if next(node.iterancestors(block_name), None) is block:
            yield node


def office_blocks(root, suffix):
    namespace, local = BLOCKS[suffix]
    name = f"{{{namespace}}}{local}"
    for block in root.iter(name):
        nodes = Block(text_nodes(block, namespace, name))
        nodes.element = block
        yield nodes
    if suffix == ".xlsx":
        for block in root.iter(f"{{{SHEET}}}is"):
            yield Block(text_nodes(block, SHEET, f"{{{SHEET}}}is"))
        for cell in root.iter(f"{{{SHEET}}}c"):
            if cell.get("t") == "str":
                yield Block(cell.findall(f"{{{SHEET}}}v"))
        for tag in ("oddHeader", "evenHeader", "firstHeader", "oddFooter", "evenFooter", "firstFooter"):
            for node in root.iter(f"{{{SHEET}}}{tag}"):
                yield Block([node])
    # DrawingML text can also occur in Word and spreadsheet drawings.
    if suffix != ".pptx":
        name = f"{{{DRAW}}}p"
        for block in root.iter(name):
            yield Block(text_nodes(block, DRAW, name))
    chart = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    for cache in root.iter(chart + "strCache", chart + "strLit", chart + "multiLvlStrCache"):
        for node in cache.iter(chart + "v"):
            yield Block([node])


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
        rules.learn(content)
        words = forms.page_words(content, geometry, transform)
        labels = forms.find_labels(words)
        found = forms.fields(labels)
        wider = forms.check_document([(fold(content), found)])[0]
        for value in forms.values(labels, words, wider):
            rules.remember_literal(value.text)
        blocks = forms.graphic_blocks(pixmap) if found else []
        clean = pdf.Pixmap(pixmap)
        for area in forms.band_areas(labels, pixmap.width, wider) + blocks:
            blackout(pixmap, area, pdf.Matrix(1, 1))
        redact_geometry(pixmap, rules, content, geometry, transform, clean)
        recheck(pixmap, rules, tessdata, [], bool(found))
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
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        for entry in original.infolist():       # learn names from the whole document first
            name = entry.filename
            if name.endswith((".xml", ".vml")) and not stripped_part(name) and not name.startswith("docProps/") \
                    and name != "[Content_Types].xml":
                for nodes in office_blocks(etree.fromstring(original.read(entry), parser=parser), suffix):
                    rules.learn("".join(node.text or "" for node in nodes))
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
                parser = etree.XMLParser(resolve_entities=False, no_network=True)
                root = etree.fromstring(data, parser=parser)
                if root.getroottree().docinfo.doctype:
                    raise ValueError("Dokument obsahuje nepodporované DTD.")
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
                            "TitlesOfParts", "HeadingPairs", "HyperlinkBase", "contentStatus",
                        }:
                            root.remove(node)
                else:
                    redact_labelled_values(office_blocks(root, suffix), rules, suffix)
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
                            raise FormError("residual_data", "Ve výstupu zůstal detekovatelný text.")
                    for node in root.iter():
                        if rules.find(node.text or "") or rules.find(node.tail or ""):
                            raise FormError("residual_data", "Citlivý údaj zůstal v části Office, kterou nelze bezpečně upravit.")
                        if not isinstance(node.tag, str):
                            continue
                        for attr, value in node.attrib.items():
                            if etree.QName(attr).localname in {"name", "displayName", "title", "prompt", "error"} and rules.find(value, generic=False):
                                raise ValueError("Citlivý údaj v názvu nebo popisku vyžaduje ruční úpravu.")
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True)
            result.writestr(entry, data)
