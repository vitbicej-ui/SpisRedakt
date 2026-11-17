import csv
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree

import pymupdf as pdf

from .documents import redact_pdf
from .reasons import reason_code
from .office import BLOCKS, redact_office, redact_image

TEXT = {".txt", ".csv", ".tsv", ".json", ".xml"}
IMAGES = {".png", ".jpg", ".jpeg", ".bmp"}
SCANS = {".tif", ".tiff"}
ODF = {".odt", ".ods", ".odp"}
MAIL = {".eml", ".msg"}
LEGACY = {".doc": ".docx", ".xls": ".xlsx"}
SUPPORTED = TEXT | IMAGES | SCANS | ODF | MAIL | LEGACY.keys() | BLOCKS.keys() | {".pdf", ".zip", ".rtf"}
LARGE_FORMATS = {".pdf", ".tif", ".tiff", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".eml", ".msg",
                 ".doc", ".xls", ".rtf"}
# Formats that cannot be written back safely are released in a related format.
OUTPUT = {".tif": ".pdf", ".tiff": ".pdf", ".msg": ".eml", **LEGACY}


def output_suffix(suffix):
    return OUTPUT.get(suffix, suffix)
MAX_BYTES = 40_000_000
# Scans and documents with images are processed page by page or part by part,
# so they may be much larger than text formats and archives.
LARGE_BYTES = 400_000_000
# Archives of a whole case file; members are processed one at a time.
ARCHIVE_BYTES = 500_000_000
ARCHIVE_UNPACKED = 2_000_000_000
ARCHIVE_MEMBERS = 5_000


def size_limit(suffix):
    return ARCHIVE_BYTES if suffix == ".zip" else LARGE_BYTES if suffix in LARGE_FORMATS else MAX_BYTES


class FormatError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


SIGNATURES = (b"%PDF-", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"{\\rtf", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff",
              b"BM", b"II*\x00", b"MM\x00*", b"PK\x03\x04", b"PK\x05\x06")
HEADER_BYTES = 16


def has_signature(head):
    return head.startswith(SIGNATURES)


def identify(data, suffix):
    """Detect the format of bytes or of a file given as a Path (never reads a whole file into memory)."""
    source = None
    if isinstance(data, Path):
        source = data
        with source.open("rb") as stream:
            data = stream.read(HEADER_BYTES)
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return identify_ole(source or data)
    if suffix == ".eml":
        return suffix
    if data.startswith(b"{\\rtf"):
        return ".rtf"
    for signature, detected in ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"BM", ".bmp"),
                                (b"II*\x00", ".tif"), (b"MM\x00*", ".tif")):
        if data.startswith(signature):
            return detected
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        with ZipFile(source or io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            for part, detected in (("word/document.xml", ".docx"), ("xl/workbook.xml", ".xlsx"),
                                   ("ppt/presentation.xml", ".pptx")):
                if "[Content_Types].xml" in names and part in names:
                    return detected
            from .odf import identify_odf

            detected = identify_odf(archive)
            if detected:
                return detected
        return ".zip"
    if suffix in TEXT:
        return suffix
    return None


def identify_ole(data):
    """Tell Word, Excel and Outlook files apart by their compound-file streams (bytes or Path)."""
    import olefile

    try:
        if isinstance(data, Path):
            with data.open("rb") as stream, olefile.OleFileIO(stream) as ole:
                names = {"/".join(entry) for entry in ole.listdir()}
        else:
            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                names = {"/".join(entry) for entry in ole.listdir()}
    except OSError:
        return None
    if "WordDocument" in names:
        return ".doc"
    if "Workbook" in names or "Book" in names:
        return ".xls"
    if any(name.startswith("__substg1.0_") for name in names):
        return ".msg"
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


def redact_table(rows, rules):
    """Redact a CSV-like table, labelling whole cells under a personal-data header."""
    from .czech import plan_table

    rows = list(rows)
    plan = plan_table([list(enumerate(row)) for row in rows])
    for index, row in enumerate(rows):
        yield [(index, i) in plan and rules.cell(plan[index, i], cell) or transform(cell, rules)
               for i, cell in enumerate(row)]


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
        csv.writer(result, delimiter=delimiter).writerows(redact_table(rows, rules))
        return result.getvalue().encode()
    return transform(text, rules).encode()


NOT_PROCESSED = "NEZPRACOVANO.txt"


def redact_archive(source, target, rules, tessdata=None, depth=0, budget=None):
    """Redact every supported member of a ZIP archive into a new archive.

    ``source`` and ``target`` are paths or binary file objects; members are read
    one at a time, so the archive is never held in memory as a whole.

    A member that is refused (redaction error, unsupported, encrypted or too
    large) is left out of the output in every form; the others carry on. The
    output then holds ``NEZPRACOVANO.txt`` with item numbers and codes only.
    Returns the list of ``(order, code)`` of the left out members (those of
    nested archives carry the order of the nested archive). The limits of the
    archive and an unreadable archive still stop the whole archive.
    """
    if budget is None:
        budget = [ARCHIVE_UNPACKED, ARCHIVE_MEMBERS]
    if depth >= 3:
        raise FormatError("archive_depth", "Archiv překročil limit vnoření.")
    skipped = []
    with ZipFile(source) as archive, ZipFile(target, "w", ZIP_DEFLATED) as output:
        order = 0
        for entry in archive.infolist():
            budget[0] -= entry.file_size
            budget[1] -= 1
            if min(budget) < 0:
                raise FormatError("archive_limit", "Archiv překročil limit rozbalení.")
            if entry.is_dir():
                continue
            order += 1
            try:
                if entry.flag_bits & 1:
                    raise FormatError("archive_encrypted", "Archiv obsahuje zašifrovaný soubor.")
                if entry.file_size > ARCHIVE_BYTES:
                    raise FormatError("size_limit", "Položka archivu překračuje limit velikosti.")
                payload = archive.read(entry)
                suffix = identify(payload, Path(entry.filename).suffix.lower())
                if suffix == ".zip":
                    cleaned = io.BytesIO()
                    skipped.extend((order, code) for _, code in
                                   redact_archive(io.BytesIO(payload), cleaned, rules, tessdata, depth + 1, budget))
                    cleaned, released = cleaned.getvalue(), ".zip"
                elif suffix not in SUPPORTED:
                    raise FormatError("archive_member_unsupported", "Archiv obsahuje nepodporovanou položku.")
                elif len(payload) > size_limit(suffix):
                    raise FormatError("size_limit", "Položka archivu překračuje limit velikosti.")
                else:
                    cleaned, released = redact_bytes(payload, suffix, rules, tessdata)
            except FormatError as error:
                if error.code == "archive_limit":
                    raise
                skipped.append((order, error.code))
                continue
            except Exception as error:
                code = reason_code(error)
                skipped.append((order, "processing_error" if code == "other" else code))
                continue
            # The member is complete and checked; only now does it enter the output.
            output.writestr(f"polozka-{order:04d}{released}", cleaned)
            del payload, cleaned
        if skipped:
            output.writestr(NOT_PROCESSED, "Tyto položky archivu nebyly zpracovány a ve výstupu nejsou "
                            "(uvedeno je jen pořadí a kód důvodu):\n"
                            + "".join(f"Položka {item}: {code}\n" for item, code in skipped))
    return skipped


def redact_scan(source, destination, rules, tessdata):
    """Redact a (multi-page) TIFF scan through the image-only PDF pipeline."""
    with pdf.open(source, filetype="tiff") as scan:
        if not scan.page_count or scan.page_count > 500:
            raise FormatError("scan_pages", "Sken TIFF je prázdný nebo má příliš mnoho stran.")
        converted = scan.convert_to_pdf()
    staged = destination.with_name(destination.stem + "-scan.pdf")
    try:
        staged.write_bytes(converted)
        # Scans always need OCR: there is no text layer to rely on.
        redact_pdf(staged, destination, rules, tessdata, force_ocr=True)
    finally:
        staged.unlink(missing_ok=True)


def redact_file(source, destination, suffix, rules, tessdata):
    if suffix == ".pdf":
        redact_pdf(source, destination, rules, tessdata)
    elif suffix in SCANS:
        redact_scan(source, destination, rules, tessdata)
    elif suffix in ODF:
        from .odf import redact_odf

        redact_odf(source, destination, rules, tessdata, redact_image)
        with ZipFile(destination) as archive:
            if archive.testzip() is not None or archive.namelist()[0] != "mimetype":
                raise FormatError("corrupt_output", "Výstup OpenDocument nelze přečíst.")
    elif suffix in LEGACY:
        from .legacy import doc_to_docx, xls_to_xlsx

        converted = (doc_to_docx if suffix == ".doc" else xls_to_xlsx)(source.read_bytes())
        staged = destination.with_name(destination.stem + "-converted" + LEGACY[suffix])
        try:
            staged.write_bytes(converted)
            redact_file(staged, destination, LEGACY[suffix], rules, tessdata)
        finally:
            staged.unlink(missing_ok=True)
    elif suffix in MAIL:
        from .mail import redact_eml, redact_msg

        redact = redact_eml if suffix == ".eml" else redact_msg
        destination.write_bytes(redact(source.read_bytes(), rules, tessdata,
                                       lambda data, name: redact_bytes(data, name, rules, tessdata),
                                       redact_image))
    elif suffix == ".rtf":
        from .rtf import redact_rtf

        destination.write_bytes(redact_rtf(source.read_bytes(), rules, tessdata, redact_image))
    elif suffix in BLOCKS:
        redact_office(source, destination, rules, tessdata)
        with ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise FormatError("corrupt_output", "Výstup Office nelze přečíst.")
    else:
        skipped = []
        if suffix == ".zip":
            skipped = redact_archive(source, destination, rules, tessdata)
        else:
            data = source.read_bytes()
            if suffix in TEXT:
                output = redact_text(data, suffix, rules)
            else:
                output = redact_image(data, suffix[1:], rules, tessdata)
            destination.write_bytes(output)
        if suffix in TEXT:
            reopened = destination.read_bytes()
            if redact_text(reopened, suffix, rules) != reopened:
                raise FormatError("residual_data", "Kontrola výstupu našla zbývající údaj.")
        if suffix == ".zip":
            with ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise FormatError("corrupt_output", "Výstupní archiv nelze přečíst.")
        return skipped


def redact_bytes(data, suffix, rules, tessdata, depth=0):
    """Redact an embedded file (e-mail attachment, archive member); return its bytes and suffix."""
    import tempfile

    detected = identify(data, suffix)
    if detected not in SUPPORTED:
        raise FormatError("attachment_unsupported", "E-mail nebo archiv obsahuje položku v nepodporovaném formátu.")
    with tempfile.TemporaryDirectory(prefix="SpisRedakt-attachment-") as folder:
        source = Path(folder) / ("input" + detected)
        target = Path(folder) / ("output" + output_suffix(detected))
        source.write_bytes(data)
        if redact_file(source, target, detected, rules, tessdata):
            # An e-mail attachment or nested member is never released with parts missing.
            raise FormatError("attachment_unsupported", "E-mail nebo archiv obsahuje položku v nepodporovaném formátu.")
        return target.read_bytes(), output_suffix(detected)
