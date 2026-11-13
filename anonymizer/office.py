"""Redact supported Office XML parts while retaining their formatting."""
import re
from zipfile import ZipFile

from lxml import etree
import pymupdf as pdf

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
MEDIA = re.compile(r"^(?:word|xl|ppt)/media/[^/]+\.(png|jpe?g)$", re.I)
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
        for entry in original.infolist():
            name = entry.filename
            if stripped_part(name):
                continue
            data = original.read(entry)
            media = MEDIA.fullmatch(name)
            if media:
                data = redact_image(data, media[1].lower(), rules, tessdata)
            elif "/media/" in name:
                raise ValueError("Vložený obrázek má nepodporovaný formát. Použijte PNG nebo JPEG.")
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
                        }:
                            root.remove(node)
                else:
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
