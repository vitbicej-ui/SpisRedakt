"""Independent detector of direct personal data left in released outputs.

The detector knows the values that were put into a (synthetic) case file and
looks for them in every layer of the output files: visible text, metadata,
names of archive members, hidden XML parts, e-mail headers, attachments and,
with OCR, the pixels of scans. It shares no code with the redaction rules, so
it checks them instead of repeating their mistakes. It never reports values,
only counts per kind.
"""
from __future__ import annotations

import collections
import dataclasses
import email
import email.header
import email.policy
import functools
import html
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path

import pymupdf
from lxml import etree

KINDS = ("JMENO", "PRIJMENI", "RC", "NAROZENI", "ADRESA", "TELEFON", "EMAIL", "UCET", "DOKLAD", "ICO")
# When two findings overlap, the earlier kind wins, so one leak is counted once.
PRIORITY = ("EMAIL", "JMENO", "ADRESA", "RC", "UCET", "DOKLAD", "ICO", "TELEFON", "NAROZENI", "PRIJMENI")
MAX_DEPTH = 6
OCR_PIXELS = 40_000_000


@dataclasses.dataclass(frozen=True)
class Value:
    kind: str
    text: str
    common: bool = False

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError("Neznámý druh osobního údaje.")


# -- normalization -------------------------------------------------------------

_HYPHENATED = re.compile(r"(?<=\w)[-\u2010\u2011\u2012][ \t]*\r?\n[ \t]*(?=\w)")
_BLANKS = re.compile(r"\s{2,}|[^\S ]")
_INVISIBLE = dict.fromkeys(map(ord, "\xad\u200b\u200c\u200d\u2060\ufeff"))


def _strip(text):
    text = unicodedata.normalize("NFKD", text).translate(_INVISIBLE)
    return "".join(char for char in text if not unicodedata.combining(char))


def _plain(text):
    """Remove diacritics and join words hyphenated at the end of a line (case is kept)."""
    text = _HYPHENATED.sub("", _strip(text))
    return _BLANKS.sub(lambda found: "\n" if "\n" in found.group() else " ", text)


def _folded(text):
    return _strip(text).casefold()


# -- Czech forms ---------------------------------------------------------------

_MASCULINE = ("", "a", "ovi", "u", "e", "em", "ové", "ů", "ům", "y")
_FEMININE = ("ová", "ové", "ovou", "ovu", "ovy")
_ADJECTIVE = ("ý", "ého", "ému", "ém", "ým", "í", "ých", "ými", "á", "é", "ou", "ému")
_SOFT = {"k": "c", "h": "z", "g": "z", "r": "ř", "ch": "š"}


def _consonant_forms(base, feminine):
    forms = {base + ending for ending in _MASCULINE}
    if feminine:
        forms |= {base + ending for ending in _FEMININE}
    return forms


def _movable(base):
    """Bases with a movable e (Hájek - Hájka); the second one is the base without it."""
    for ending in ("ek", "ec", "el"):
        if base.endswith(ending):
            return base[:-2] + ending[1]
    return None


def _forms(name, first):
    """Declined forms of a surname (``first`` False) or a first name, in both genders."""
    low = name.strip().lower()
    forms = {low}
    if low.endswith("ová") and not first:
        base = low[:-3]
        forms |= _consonant_forms(base, True)
        forms |= {base[:-1] + "ek"} if base.endswith("k") else {base[:-1] + "ec"} if base.endswith("c") else set()
        forms.discard(base)
        if shortened := _movable(base):
            forms |= _consonant_forms(shortened, True)
    elif low.endswith(("ý", "á")) and not first:
        forms |= {low[:-1] + ending for ending in _ADJECTIVE}
    elif low.endswith("í") and not first:
        forms |= {low[:-1] + ending for ending in ("í", "ího", "ímu", "ím", "ích")}
    elif low.endswith("a"):
        stem = low[:-1]
        forms |= {stem + ending for ending in ("a", "y", "ě", "e", "u", "o", "ou")}
        for tail, soft in _SOFT.items():
            if stem.endswith(tail):
                forms |= {stem[:-len(tail)] + soft + "e", stem[:-len(tail)] + soft + "ě"}
        if not first:
            forms |= {stem + ending for ending in _FEMININE}
    elif low.endswith("ie") and first:
        forms |= {low[:-1] + ending for ending in ("e", "i", "í")}
    else:
        forms |= _consonant_forms(low, not first)
        if first:
            forms |= {low + "i"}
        if shortened := _movable(low):
            forms |= _consonant_forms(shortened, not first)
    return {_folded(form) for form in forms if form}


def _alternatives(forms):
    return "(?:" + "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True)) + ")"


_GAP = r"\s+"
_SEPARATORS = r"[\s./\-\u2010-\u2013]*"
_MONTHS = {1: "ledn", 2: "unor", 3: "brezn", 4: "dubn", 5: "kvetn", 6: "cervn", 7: "cervenc",
           8: "srpn", 9: "zari", 10: "rijn", 11: "listopad", 12: "prosinc"}
_ABBREVIATIONS = {"p", "pi", "pan", "pani", "ing", "mgr", "bc", "judr", "mudr", "phdr", "rndr", "doc", "prof", "dr",
                  "mjr", "plk", "kpt", "por", "sl", "sv", "nar", "c", "ul", "st"}


# -- compiling the values ------------------------------------------------------

def _digits(text):
    return re.sub(r"\D", "", text)


def _spaced(chars):
    return _SEPARATORS.join(re.escape(char) for char in chars)


def _number(text, letters=False):
    chars = _folded(re.sub(r"[\s./\-]", "", text)) if letters else _digits(text)
    boundary = r"[a-z0-9]" if letters and not chars.isdigit() else r"\d"
    return rf"(?<!{boundary}){_spaced(chars)}(?!{boundary})"


def _patterns(value):
    """Yield compiled expressions that find the value; ``kind`` is the value's own."""
    text = value.text.strip()
    if value.kind == "JMENO":
        parts = text.split()
        if len(parts) < 2:
            raise ValueError("Jméno musí mít křestní jméno i příjmení.")
        first, last = _alternatives(_forms(parts[0], True)), _alternatives(_forms(parts[-1], False))
        yield rf"(?<!\w)(?:{first}{_GAP}{last}|{last}{_GAP}{first})(?!\w)"
    elif value.kind == "PRIJMENI":
        yield rf"(?<!\w){_alternatives(_forms(text, False))}(?!\w)"
    elif value.kind in {"RC", "ICO"}:
        yield _number(text)
    elif value.kind == "TELEFON":
        yield rf"(?<!\d)(?:(?:(?:\+|00)\s*)?420{_SEPARATORS})?{_spaced(_digits(text)[-9:])}(?!\d)"
    elif value.kind == "UCET":
        yield _number(text)
        if "/" in text and len(_digits(text.split("/")[0])) >= 6:
            yield _number(text.split("/")[0])
    elif value.kind == "DOKLAD":
        yield _number(text, letters=True)
    elif value.kind == "NAROZENI":
        year, month, day = (int(part) for part in text.split("-"))
        d, m = f"0?{day}", f"0?{month}"
        yield rf"(?<!\d)(?:{d}(?:\s*[./\-]\s*|\s+){m}(?:\s*[./\-]\s*|\s+){year}|{year}\s*[./\-]\s*{m}\s*[./\-]\s*{d})(?!\d)"
        yield rf"(?<!\d){d}\s*\.?\s*{_MONTHS[month]}\w{{0,3}}\s+{year}(?!\d)"
    elif value.kind == "EMAIL":
        local = _folded(text.split("@")[0])
        yield rf"(?<![a-z0-9._%+\-]){re.escape(_folded(text))}(?![a-z0-9])"
        if len(local) > 3 and "@" in text:
            yield rf"(?<![a-z0-9]){re.escape(local)}(?![a-z0-9])"
    elif value.kind == "ADRESA":
        street = text.split(",")[0].strip()
        found = re.fullmatch(r"(.*?)\s*(\d+(?:\s*/\s*\d+)?\s*[A-Za-z]?)", street)
        if not found:
            raise ValueError("Adresa musí obsahovat ulici a číslo domu.")
        words = _folded(found[1]).split()
        last = words[-1]
        stem = last[:-1] if last[-1] in "aeiouy" else last
        tail = rf"{re.escape(stem)}\w{{0,3}}" if stem != last else re.escape(last)
        number = _SEPARATORS.join(re.escape(char) for char in _folded(re.sub(r"\s+", "", found[2])))
        words = _GAP.join(re.escape(word) for word in words[:-1] + [""])
        yield rf"(?<!\w){words}{tail}(?:\s+(?:ulic[ei]|ul\.?))?\s*,?\s*{number}(?!\d)"


@functools.lru_cache(maxsize=8)
def _compile(values):
    jobs = []
    for value in dict.fromkeys(values):
        for pattern in _patterns(value):
            check = (_common_ok if value.common and value.kind == "PRIJMENI" else
                     _grouped if value.kind in {"RC", "ICO", "UCET", "DOKLAD", "TELEFON"} else None)
            jobs.append((PRIORITY.index(value.kind), value.kind, re.compile(pattern, re.IGNORECASE), check))
    return jobs


def _grouped(text, match):
    """Digits split into single-digit pieces (a list of indexes or cells) are not a number."""
    return sum(len(group) == 1 for group in re.findall(r"\d+", match.group())) <= 2


def _common_ok(text, match):
    """A common word counts as a surname only when capitalized and not at the start of a sentence."""
    if not match.group()[0].isupper():
        return False
    before = text[:match.start()].rstrip(" \t")
    if not before or before[-1] in "\r\n!?":
        return False
    if before[-1] == ".":
        word = re.search(r"(\w*)\.$", before)[1].lower()
        return word in _ABBREVIATIONS
    return True


def scan_texts(texts, values):
    """Count findings per kind in the given texts (overlapping findings count once)."""
    jobs = _compile(tuple(values))
    found = collections.Counter()
    for text in texts:
        if not text:
            continue
        plain = _plain(text)
        spans = []
        for priority, kind, expression, check in jobs:
            for match in expression.finditer(plain):
                if match.end() > match.start() and (check is None or check(plain, match)):
                    spans.append((priority, match.start(), match.end(), kind))
        covered = bytearray(len(plain))
        for _, start, end, kind in sorted(spans):
            if not any(covered[start:end]):
                covered[start:end] = b"\1" * (end - start)
                found[kind] += 1
    return found


def report(counter):
    """Czech summary with kinds and counts only, never values."""
    items = [f"{kind} {counter[kind]}" for kind in KINDS if counter.get(kind)]
    return "Úniky podle druhu: " + ", ".join(items) if items else "Úniky: 0"


# -- layers of output files ----------------------------------------------------

def layers(path, tessdata=None):
    """Yield texts from every layer of an output file (format recognized by content)."""
    path = Path(path)
    yield from _layers(path.read_bytes(), path.name, tessdata, 0)


def tree_texts(root, tessdata=None):
    """Yield the relative names and all layers of every file under ``root``."""
    root = Path(root)
    for path in sorted(root.rglob("*")):
        yield str(path.relative_to(root))
        if path.is_file():
            yield from layers(path, tessdata)


def scan_tree(root, values, tessdata=None):
    return scan_texts(tree_texts(root, tessdata), values)


def _text(data, charset=None):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    for encoding in (charset, "utf-8-sig"):
        if encoding:
            try:
                return data.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                pass
    return data.decode("cp1250", "replace")


def _ascii_runs(data):
    return "\n".join(run.decode("ascii") for run in re.findall(rb"[\x20-\x7e]{6,}", data))


# Two header-like lines at the start: enough to treat a file without a suffix as an e-mail message.
_MAIL_START = re.compile(rb"\A[\w-]{2,40}: ?[^\n]*\n(?:[\w-]{2,40}:|[ \t])")


def _layers(data, name, tessdata, depth):
    if depth > MAX_DEPTH:
        return
    suffix = Path(name).suffix.lower()
    head = data[:16]
    if head.startswith(b"%PDF-"):
        yield from _pdf(data, tessdata, depth)
    elif head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        yield from _zip(data, tessdata, depth)
    elif head.startswith(b"{\\rtf"):
        raw = data.decode("latin-1")
        yield raw
        yield _rtf(raw)
    elif head.startswith((b"II*\x00", b"MM\x00*")):
        yield _ascii_runs(data)
        if tessdata:
            with pymupdf.open(stream=data, filetype="tiff") as scan:
                for page in scan:
                    yield _ocr(page.get_pixmap(dpi=300), tessdata)
    elif head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) or (head.startswith(b"BM") and suffix == ".bmp"):
        yield _ascii_runs(data)
        if head.startswith(b"\x89PNG"):
            yield from _png_text(data)
        if tessdata:
            yield _ocr(pymupdf.Pixmap(data), tessdata)
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        yield data.decode("utf-16-le", "ignore")
        yield data.decode("cp1250", "ignore")
    elif suffix == ".eml" or _MAIL_START.match(data[:2048]):
        yield from _mail(email.message_from_bytes(data), tessdata, depth)
    elif b"\0" in data[:4096] and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        yield _ascii_runs(data)
    else:
        text = _text(data)
        stripped = text.lstrip("\ufeff \t\r\n")
        if suffix in {".html", ".htm"}:
            yield from _html(text)
        elif suffix in {".xml", ".rels", ".svg"} or stripped.startswith("<?xml"):
            yield from _xml(data)
        elif suffix == ".json":
            yield re.sub(r"\\u([0-9a-fA-F]{4})", lambda found: chr(int(found[1], 16)), text)
        else:
            yield text


def _zip(data, tessdata, depth):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        yield _ascii_runs(data)
        return
    with archive:
        yield _text(archive.comment)
        for info in archive.infolist():
            yield info.filename
            if info.comment:
                yield _text(info.comment)
            if info.is_dir():
                continue
            try:
                member = archive.read(info)
            except RuntimeError:
                continue
            yield from _layers(member, info.filename, tessdata, depth + 1)


_INLINE = {"t", "r", "span", "a", "hyperlink", "ins", "smarttag", "sdt", "sdtcontent", "rpr", "prooferr",
           "bookmarkstart", "bookmarkend", "instrtext", "fldsimple", "nobreakhyphen", "softhyphen"}
_BREAKS = {"tab": " ", "br": "\n", "cr": "\n", "s": " ", "line-break": "\n"}


def _xml(data):
    """Text of an XML part (paragraphs separated, runs joined), then names and attribute values."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        root = None
    if root is None:
        yield _text(data)
        return
    out, extra = [], set()
    attributes = []
    for event, node in etree.iterwalk(root, events=("start", "end", "comment", "pi")):
        if event in {"comment", "pi"}:
            out.append("\n" + (node.text or "") + "\n" + (node.tail or ""))
            continue
        local = etree.QName(node).localname
        if event == "start":
            extra.add(local)
            extra.update(etree.QName(key).localname for key in node.attrib)
            attributes.extend(node.attrib.values())
            block = local.lower() not in _INLINE
            out.append(("\n" if block else "") + _BREAKS.get(local.lower(), "") + (node.text or ""))
        else:
            out.append(("\n" if local.lower() not in _INLINE else "") + (node.tail or ""))
    yield "".join(out)
    yield "\n".join(sorted(extra) + attributes)


_BLOCK_TAGS = re.compile(r"</?(?:p|div|br|tr|td|th|li|ul|ol|table|h[1-6]|body|html|head|title|blockquote)\b[^>]*>", re.I)


def _html(text):
    comments = re.findall(r"<!--(.*?)-->", text, re.S)
    text = re.sub(r"<!--.*?-->|<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    values = [html.unescape(a or b) for tag in re.findall(r"<[^>]*>", text)
              for a, b in re.findall(r"""=\s*(?:"([^"]*)"|'([^']*)')""", tag)]
    body = re.sub(r"<[^>]*>", "", _BLOCK_TAGS.sub("\n", text))
    yield html.unescape(body)
    yield "\n".join(values + comments)


_RTF_TOKEN = re.compile(r"\\u(-?\d+)(?:\\'[0-9a-fA-F]{2}|[^\\{}\s])?|\\'([0-9a-fA-F]{2})|\\([a-zA-Z]+)-?\d* ?|"
                        r"\\([{}\\])|\\[^a-zA-Z]|[{}]|([^\\{}]+)")


def _rtf(raw):
    """Decoded RTF text: \\'hh in Windows-1250, \\uN, paragraph breaks kept, other controls dropped."""
    def token(match):
        number, hexa, word, escaped, plain = match.groups()
        if number is not None:
            return chr(int(number) % 65536)
        if hexa is not None:
            return bytes([int(hexa, 16)]).decode("cp1250", "replace")
        if word is not None:
            return "\n" if word in {"par", "line", "cell", "row", "sect", "page"} else " " if word == "tab" else ""
        return escaped or plain or ""
    return _RTF_TOKEN.sub(token, raw)


def _png_text(data):
    position = 8
    while position + 8 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        if kind in {b"tEXt", b"iTXt"}:
            yield _text(body.replace(b"\0", b" "))
        elif kind == b"zTXt":
            import zlib

            try:
                yield _text(zlib.decompress(body.split(b"\0", 1)[1][1:]))
            except (IndexError, zlib.error):
                pass
        position += 12 + length


def _decode_header(value):
    value = str(value)
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except (LookupError, UnicodeError, ValueError, email.errors.HeaderParseError):
        return value


def _mail(message, tessdata, depth):
    for part in message.walk():
        for key, value in part.items():
            yield f"{key}: {_decode_header(value)}"
        if part.is_multipart():
            for extra in (part.preamble, part.epilogue):
                if isinstance(extra, str):
                    yield extra
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        filename = part.get_filename()
        filename = _decode_header(filename) if filename else ""
        if filename:
            yield filename
        kind = part.get_content_type()
        if kind.startswith("text/") and not filename:
            text = _text(payload, part.get_content_charset())
            yield from (_html(text) if kind == "text/html" else [text])
        else:
            yield from _layers(payload, filename, tessdata, depth + 1)


def _ocr(pixmap, tessdata):
    from .documents import ocr_path

    if pixmap.width * pixmap.height > OCR_PIXELS:
        return ""
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    if pixmap.colorspace is None or pixmap.colorspace.n != 3:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)  # the OCR engine reads RGB reliably
    recognized = pixmap.pdfocr_tobytes(language="ces", tessdata=ocr_path(tessdata))
    with pymupdf.open("pdf", recognized) as result:
        return "\n".join(page.get_text() for page in result)


def _pdf(data, tessdata, depth):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            return
        yield from (value for value in (document.metadata or {}).values() if value)
        xmp = document.get_xml_metadata()
        if xmp:
            yield from _xml(xmp.encode())
        for _, title, _ in document.get_toc():
            yield title
        for name in document.embfile_names():
            info = document.embfile_info(name)
            yield from (value for value in (name, *info.values()) if isinstance(value, str) and value)
            yield from _layers(document.embfile_get(name), info.get("filename") or name, tessdata, depth + 1)
        for page in document:
            yield page.get_text()
            for annotation in page.annots() or ():
                yield from (value for value in annotation.info.values() if isinstance(value, str) and value)
            for link in page.get_links():
                yield from (str(link[key]) for key in ("uri", "file") if link.get(key))
            for widget in page.widgets() or ():
                yield from (str(value) for value in (widget.field_name, widget.field_value) if value)
            if tessdata:
                zoom = min(300, int(72 * (OCR_PIXELS / max(1, page.rect.width * page.rect.height)) ** 0.5))
                yield _ocr(page.get_pixmap(dpi=zoom), tessdata)
