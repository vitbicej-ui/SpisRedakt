import csv
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree

from .documents import redact_pdf
from .office import BLOCKS, redact_office, redact_image

TEXT = {".txt", ".csv", ".tsv", ".json", ".xml"}
IMAGES = {".png", ".jpg", ".jpeg", ".bmp"}
SUPPORTED = TEXT | IMAGES | BLOCKS.keys() | {".pdf", ".zip"}
MAX_BYTES = 40_000_000


class FormatError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def identify(data, suffix):
    if data.startswith(b"%PDF-"):
        return ".pdf"
    for signature, detected in ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"BM", ".bmp")):
        if data.startswith(signature):
            return detected
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        with ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            for part, detected in (("word/document.xml", ".docx"), ("xl/workbook.xml", ".xlsx"),
                                   ("ppt/presentation.xml", ".pptx")):
                if "[Content_Types].xml" in names and part in names:
                    return detected
        return ".zip"
    if suffix in TEXT:
        return suffix
    return None


def decode(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def transform(value, rules):
    if isinstance(value, str):
        return rules.replace(value)[0]
    if isinstance(value, list):
        return [transform(item, rules) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = transform(key, rules)
            if new_key in result:
                raise FormatError("key_collision", "Po anonymizaci vznikl duplicitní klíč JSON.")
            result[new_key] = transform(item, rules)
        return result
    if isinstance(value, int) and not isinstance(value, bool):
        replaced = transform(str(value), rules)
        return value if replaced == str(value) else replaced
    return value


def redact_text(data, suffix, rules):
    if suffix == ".xml":
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True,
                                                       remove_comments=True, remove_pis=True))
        if root.getroottree().docinfo.doctype:
            raise FormatError("xml_dtd", "XML obsahuje nepodporované DTD.")
        for node in root.iter():
            if rules.find(node.tag) or any(rules.find(key) for key in node.attrib):
                raise FormatError("xml_sensitive_name", "Citlivý údaj je součástí názvu prvku XML.")
            if node.text:
                node.text = transform(node.text, rules)
            if node.tail:
                node.tail = transform(node.tail, rules)
            for key, value in node.attrib.items():
                node.set(key, transform(value, rules))
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)
    text = decode(data)
    if "\0" in text:
        raise FormatError("binary_as_text", "Soubor obsahuje binární data.")
    if suffix == ".json":
        return json.dumps(transform(json.loads(text), rules), ensure_ascii=False, allow_nan=False, indent=2).encode()
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        if suffix == ".csv":
            try:
                delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;").delimiter
            except csv.Error:
                pass
        rows = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        result = io.StringIO(newline="")
        csv.writer(result, delimiter=delimiter).writerows(
            [transform(cell, rules) for cell in row] for row in rows
        )
        return result.getvalue().encode()
    return transform(text, rules).encode()


def redact_archive(data, rules, depth=0, budget=None):
    if budget is None:
        budget = [MAX_BYTES, 500]
    if depth >= 3:
        raise FormatError("archive_depth", "Archiv překročil limit vnoření.")
    output = io.BytesIO()
    with ZipFile(io.BytesIO(data)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            budget[0] -= entry.file_size
            budget[1] -= 1
            if min(budget) < 0:
                raise FormatError("archive_limit", "Archiv překročil limit rozbalení.")
            if entry.is_dir():
                continue
            if entry.flag_bits & 1:
                raise FormatError("archive_encrypted", "Archiv obsahuje zašifrovaný soubor.")
            payload = source.read(entry)
            suffix = identify(payload, Path(entry.filename).suffix.lower())
            if suffix in TEXT:
                cleaned = redact_text(payload, suffix, rules)
                if redact_text(cleaned, suffix, rules) != cleaned:
                    raise FormatError("residual_data", "Kontrola přílohy našla zbývající údaj.")
            elif suffix == ".zip":
                cleaned = redact_archive(payload, rules, depth + 1, budget)
            else:
                raise FormatError("archive_member_unsupported", "Archiv obsahuje nepodporovanou položku.")
            target.writestr(f"polozka-{len(target.filelist) + 1:04d}{suffix}", cleaned)
    return output.getvalue()


def redact_file(source, destination, suffix, rules, tessdata):
    if suffix == ".pdf":
        redact_pdf(source, destination, rules, tessdata)
    elif suffix in BLOCKS:
        redact_office(source, destination, rules, tessdata)
        with ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise FormatError("corrupt_output", "Výstup Office nelze přečíst.")
    else:
        data = source.read_bytes()
        if suffix in TEXT:
            output = redact_text(data, suffix, rules)
        elif suffix in IMAGES:
            output = redact_image(data, suffix[1:], rules, tessdata)
        else:
            output = redact_archive(data, rules)
        destination.write_bytes(output)
        reopened = destination.read_bytes()
        if suffix in TEXT and redact_text(reopened, suffix, rules) != reopened:
            raise FormatError("residual_data", "Kontrola výstupu našla zbývající údaj.")
        if suffix == ".zip":
            with ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise FormatError("corrupt_output", "Výstupní archiv nelze přečíst.")
