"""Render a whole case file as one anonymized, token-frugal Markdown document.

Two phases over the extracted documents (``extract.walk``): ``collect`` only
registers persons and other labels in ``Rules``; ``render`` replaces and writes
the text. Every saving is deterministic (no summarising, no guessing). The
module is pure: it returns strings and never writes to disk or logs content.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import date

from . import czech

# Rough estimate for Czech text; real tokenizers differ by model.
TOKEN_CHARS = 3.0
DUPLICATE_PARAGRAPH_MIN = 200
CITATION_MIN = 40
WARNING = ("Automatická anonymizace nezaručuje odstranění všech osobních údajů; "
           "nepřímou identifikaci z kontextu musí posoudit člověk.")

SHORT = {"OSOBA": "O", "ADRESA": "A", "RC": "RC", "NAROZENI": "N", "TELEFON": "T", "EMAIL": "E", "UCET": "U",
         "IBAN": "IB", "DOKLAD": "DK", "ICO": "IC", "DIC": "DI", "SPIS": "SZ", "ORGANIZACE": "F",
         "OSOBNI_UDAJ": "X", "VLASTNI": "V", "PRIJMENI": "O"}
LEGEND = (("O", "osoba"), ("A", "adresa"), ("RC", "rodné číslo"), ("N", "datum narození"), ("T", "telefon"),
          ("E", "e-mail"), ("U", "účet"), ("IB", "IBAN"), ("DK", "doklad"), ("IC", "IČO"), ("DI", "DIČ"),
          ("SZ", "spisová značka"), ("F", "organizace"), ("X", "jiný osobní údaj"), ("V", "vlastní údaj"))
LONG_LABEL = re.compile(r"\[([A-Z_]+)-(\d+)\]")

QUOTE_MARKER = re.compile(
    r"\s*(?:-{3,}\s*(?:původní zpráva|original message)\s*-{3,}|dne\b.*\bnapsal\(?a?\)?\s*:?|on\b.*\bwrote\s*:)\s*",
    re.I)
QUOTED_HEADER = re.compile(r"\s*(?:od|odesláno|komu|kopie|předmět|datum|from|sent|to|cc|subject|date)\s*:", re.I)
DATE_IN_TEXT = re.compile(r"\b(?:ze\s+dne|dne)\s+(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})", re.I)
HYPHEN_END = re.compile(r"[^\W\d_][-­]$")


@dataclass
class Output:
    text: str
    files: dict = field(default_factory=dict)
    documents: int = 0
    skipped: list = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0


def estimate_tokens(text):
    return math.ceil(len(text) / TOKEN_CHARS)


def _short_code(kind):
    return SHORT.get(kind) or kind[:2]


def short_labels(text):
    """``[OSOBA-12]`` -> ``[O12]``."""
    return LONG_LABEL.sub(lambda m: f"[{_short_code(m[1])}{m[2]}]", text)


def _norm(text):
    return " ".join(text.split())


INLINE_PAGE = re.compile(r"\b(?:strana|str\.|s\.)\s*\d+(?:\s*(?:z|ze|/|of)\s*\d+)?|(?<!\w)-\s*\d+\s*-(?!\w)", re.I)
WHOLE_PAGE = re.compile(r"\d+(?:\s*(?:/|z|ze|of)\s*\d+)?")
HEADER_MAX = 120


def _strip_page(line):
    """The line without page-number patterns, blanks collapsed ("" for a bare page number)."""
    text = _norm(line)
    if WHOLE_PAGE.fullmatch(text):
        return ""
    return _norm(INLINE_PAGE.sub(" ", text))


def _line_key(line):
    return _strip_page(line).casefold()


# -- preparation (before anonymization) -------------------------------------------------

def _join_lines(text):
    """Join the lines of a text-layer paragraph with spaces; hyphenated words are rejoined."""
    out = ""
    for line in (item.strip() for item in text.split("\n")):
        if not line:
            continue
        if not out:
            out = line
        elif HYPHEN_END.search(out) and line[:1].islower():
            out = out[:-1] + line
        else:
            out += " " + line
    return out


def _detect_repeats(pages):
    """Return {(page, block, line)} to drop plus the first text of repeated headers and footers.

    Conservative: page-number patterns are removed from a line first (nothing left = a page number);
    the rest must match exactly (blanks aside) on more than half of the pages, be short, and stand
    among the first/last 3 lines (2 on pages of at most 6 lines).
    """
    positions = []     # per page: [(block, line, raw)]
    for page in pages:
        found = []
        for b, block in enumerate(page.blocks):
            if block.kind == "p":
                found += [(b, n, raw) for n, raw in enumerate(block.text.split("\n")) if raw.strip()]
        positions.append(found)

    def edge(lines):
        width = 2 if len(lines) <= 6 else 3
        return range(min(width, len(lines))), range(max(len(lines) - width, 0), len(lines))

    drop, headers, footers = set(), [], []
    total = len(pages)
    top, bottom = {}, {}
    if total >= 3:
        for lines in positions:
            first, last = edge(lines)
            for target, part in ((top, first), (bottom, last)):
                for key in {_line_key(lines[i][2]) for i in part}:
                    if key and len(key) <= HEADER_MAX and any(c.isalpha() for c in key):
                        target[key] = target.get(key, 0) + 1
    for p, lines in enumerate(positions):
        first, last = edge(lines)
        for i, (b, n, raw) in enumerate(lines):
            if i not in first and i not in last:
                continue
            key = _line_key(raw)
            if not key:
                drop.add((p, b, n))
            elif i in first and top.get(key, 0) * 2 > total:
                drop.add((p, b, n))
                if key not in {k for k, _ in headers}:
                    headers.append((key, _strip_page(raw)))
            elif i in last and bottom.get(key, 0) * 2 > total:
                drop.add((p, b, n))
                if key not in {k for k, _ in footers}:
                    footers.append((key, _strip_page(raw)))
    return drop, [raw for _, raw in headers], [raw for _, raw in footers]


def _prepare(doc):
    """Return (header texts, footer texts, pages) where pages = [(number, [raw item]), ...].

    A raw item is ``(kind, text, ocr, rows)``. Page numbers and repeated headers/footers are
    removed; removed lines are kept per page for the naive rendering only.
    """
    drop, headers, footers = _detect_repeats(doc.pages)
    pages = []
    for p, page in enumerate(doc.pages):
        items, removed = [], []
        for b, block in enumerate(page.blocks):
            if block.kind != "p":
                items.append((block.kind, block.text or "", block.ocr, block.rows))
                continue
            lines = block.text.split("\n")
            kept = [raw for n, raw in enumerate(lines) if (p, b, n) not in drop]
            removed += [raw for n, raw in enumerate(lines) if (p, b, n) in drop]
            if len(kept) != len(lines) and not any(line.strip() for line in kept):
                continue
            text = "\n".join(kept)
            if doc.format == "PDF" and not block.ocr:
                text = _join_lines(text)
            elif block.ocr:
                text = _norm(text)
            if text.strip():
                items.append(("p", text.strip(), block.ocr, None))
        pages.append((page.number, items, removed))
    extra_h = [t for t in (getattr(doc, "headers", None) or []) if t and t.strip()]
    extra_f = [t for t in (getattr(doc, "footers", None) or []) if t and t.strip()]

    def merged(first, second):
        seen, out = set(), []
        for text in first + second:
            if _norm(text) not in seen:
                seen.add(_norm(text))
                out.append(text)
        return out

    return merged(extra_h, headers), merged(extra_f, footers), pages


# -- anonymization -----------------------------------------------------------------------

@dataclass
class _Entry:
    kind: str           # p, table, image, imgocr, note, mail, quote
    text: str           # compact rendering
    naive: str          # naive rendering (nothing saved)
    page: int = 0
    quote: "str | None" = None      # normalized quoted text (kind quote)


@dataclass
class _Doc:
    doc: object
    headers: list
    footers: list
    pages: list         # [(number, [_Entry], [removed anonymized lines])]
    removed: list = field(default_factory=list)
    text: str = ""      # rendered body, filled by render


class _Anonymizer:
    def __init__(self, rules, collecting):
        self.rules = rules
        self.collecting = collecting
        self.collect = getattr(rules, "collect", None)

    def text(self, value):
        if not value:
            return value
        if self.collecting:
            if self.collect is not None:
                self.collect(value)
            else:
                self.rules.replace(value)
            return value
        return self.rules.replace(value)[0]

    def table(self, rows):
        rows = [["" if cell is None else str(cell) for cell in row] for row in rows or ()]
        plan = czech.plan_table([list(enumerate(row)) for row in rows])
        result = []
        for r, row in enumerate(rows):
            out = []
            for c, cell in enumerate(row):
                value = self.rules.cell(plan[r, c], cell) if (r, c) in plan else None
                out.append(value if value else self.text(cell))
            result.append(out)
        return rows, plan, result


def _cell(value):
    return _norm(value.replace("\n", " / ")).replace("|", "\\|") if value else ""


def _is_label(cell):
    return bool(cell.strip()) and (czech.column_kind(cell) is not None or cell.strip().endswith(":"))


def _table_entries(raw_rows, plan, rows):
    """Return (compact, naive) text of a table."""
    naive_lines = []
    for n, row in enumerate(rows):
        naive_lines.append("| " + " | ".join(row) + " |")
        if n == 0:
            naive_lines.append("| " + " | ".join("---" for _ in row) + " |")
    naive = "\n".join(naive_lines)
    filled = [row for row in rows if any(cell.strip() for cell in row)]
    if not filled:
        return "", naive
    form = bool(plan) and all(
        any(czech.column_kind(raw_rows[r][i]) is not None for i in range(c)) for r, c in plan)
    colon = all(len([c for c in row if c.strip()]) == 2 and row[0].strip().endswith(":")
                for row in raw_rows if any(c.strip() for c in row))
    lines = []
    for r, row in enumerate(rows):
        raw = raw_rows[r]
        if not any(cell.strip() for cell in row):
            continue
        if form or colon:
            parts, i = [], 0
            cells = [(k, c) for k, c in enumerate(row) if c.strip()]
            while i < len(cells):
                k, cell = cells[i]
                nxt = cells[i + 1] if i + 1 < len(cells) else None
                if _is_label(raw[k]) and nxt and not _is_label(raw[nxt[0]]):
                    parts.append(f"{_cell(cell).rstrip(':').rstrip()}: {_cell(nxt[1])}")
                    i += 2
                else:
                    parts.append(_cell(cell))
                    i += 1
            lines.append("; ".join(parts))
        else:
            cells = [_cell(c) for c in row]
            while cells and not cells[-1]:
                cells.pop()
            lines.append("|" + "|".join(cells) + "|")
    return "\n".join(lines), naive


def _quoted_plain(lines):
    """Normalized text of quoted lines: no ``>``, no marker, no leading quoted headers."""
    texts = [re.sub(r"^[>\s]+", "", line) for line in lines]
    while texts and (not texts[0].strip() or QUOTE_MARKER.fullmatch(texts[0])
                     or QUOTED_HEADER.match(texts[0])):
        texts.pop(0)
    return _norm(" ".join(texts))


def _quote_entries(lines, page):
    """Split the lines of a quoted region into ``quote`` entries (reply text and ``>`` copies apart)."""
    runs = []
    for line in lines:
        quoted = line.lstrip().startswith(">")
        if runs and runs[-1][0] == quoted:
            runs[-1][1].append(line)
        else:
            runs.append((quoted, [line]))
    merged = []
    for quoted, run in runs:
        # a marker with its headers but no body belongs to the run that follows
        if merged and not _quoted_plain(merged[-1][1]):
            merged[-1] = (quoted, merged[-1][1] + run)
        else:
            merged.append((quoted, run))
    result = []
    for _, run in merged:
        text = "\n".join(line for line in run if line.strip())
        if text:
            result.append(_Entry("quote", text, text, page, _quoted_plain(run)))
    return result


def _quotes(entries, doc_format):
    """Turn quoted-reply regions of an e-mail body into ``quote`` entries.

    A region starts at a marker line (``-----Původní zpráva-----``, ``Dne ... napsal(a):``, ``On ... wrote:``)
    and lasts to the end of the body, or it is a run of ``>`` lines. The blocks of the body are judged
    together: the extraction splits a body at blank lines.
    """
    result, run, region, text, page = [], [], None, [], 0

    def flush():
        if run:
            result.extend(_quote_entries(run, page))
            run.clear()
        if text:
            value = "\n".join(text)
            result.append(_Entry("p", value, value, page))
            text.clear()

    for entry in entries:
        if region is not None:
            region.extend(entry.text.split("\n") + [""])
            continue
        if entry.kind != "p":
            flush()
            result.append(entry)
            continue
        page = entry.page
        for line in entry.text.split("\n"):
            if QUOTE_MARKER.fullmatch(line):
                if text:
                    value = "\n".join(text)
                    result.append(_Entry("p", value, value, page))
                    text.clear()
                if run:
                    result.extend(_quote_entries(run, page))
                    run.clear()
                region = [line]
            elif region is not None:
                region.append(line)
            elif line.lstrip().startswith(">"):
                if text:
                    value = "\n".join(text)
                    result.append(_Entry("p", value, value, page))
                    text.clear()
                run.append(line)
            else:
                if run:
                    result.extend(_quote_entries(run, page))
                    run.clear()
                text.append(line)
        if region is None and text:
            value = "\n".join(text)
            result.append(_Entry("p", value, value, page))
            text.clear()
        if region is not None:
            region.append("")
    if region is not None:
        result.extend(_quote_entries(region, page))
    flush()
    return result


def _process(doc, rules, collecting):
    anon = _Anonymizer(rules, collecting)
    headers, footers, pages = _prepare(doc)
    headers = [anon.text(t) for t in headers]
    footers = [anon.text(t) for t in footers]
    cache = {}
    out = []
    for number, items, removed in pages:
        entries = []
        for kind, text, ocr, rows in items:
            if kind == "table":
                raw_rows, plan, done = anon.table(rows)
                compact, naive = _table_entries(raw_rows, plan, done)
                if compact or naive.strip("| -\n"):
                    entries.append(_Entry("table", compact, naive))
            elif kind == "image":
                if text.strip():
                    value = anon.text(text.strip())
                    entries.append(_Entry("imgocr", f"[OBRÁZEK, text OCR:] {value}", f"[OBRÁZEK, text OCR:] {value}"))
                else:
                    entries.append(_Entry("image", "[OBRÁZEK VYNECHÁN]", "[OBRÁZEK VYNECHÁN]"))
            elif text.strip():
                value = anon.text(text.strip())
                if kind == "note":
                    value = "[pozn.] " + value
                entries.append(_Entry({"mailheader": "mail", "note": "note"}.get(kind, "p"), value, value))
        if doc.format in ("EML", "MSG") and not collecting:
            entries = _quotes(entries, doc.format)
        lines = []
        for raw in removed:
            key = _line_key(raw)
            if not _strip_page(raw) or not any(c.isalpha() for c in raw):
                lines.append(raw.strip())
            else:
                if key not in cache:
                    cache[key] = anon.text(raw.strip())
                lines.append(cache[key])
        out.append((number, entries, lines))
    return _Doc(doc, headers, footers, out)


def _documents(documents):
    return [d for d in documents if hasattr(d, "pages")]


def collect(documents, rules):
    """Phase 1: register persons and other labels over every text of the case file."""
    for doc in _documents(documents):
        _process(doc, rules, True)


# -- rendering ---------------------------------------------------------------------------

def _heading(doc):
    pages = sum(1 for page in doc.pages if page.number is not None)
    parts = [doc.format] + ([f"{pages} s."] if pages else [])
    return f"## D{doc.index} ({', '.join(parts)})"


def _number(value):
    return f"{value:,}".replace(",", " ")


def _date(item, entries):
    if item.doc.date:
        return item.doc.date
    for entry in entries:
        found = DATE_IN_TEXT.search(entry.text)
        if found:
            try:
                return date(int(found[3]), int(found[2]), int(found[1])).isoformat()
            except ValueError:
                continue
    return None


def _naive(item):
    parts = [_heading(item.doc)]
    for number, entries, removed in item.pages:
        if number is not None:
            parts.append(f"[s. {number}]")
        parts += removed
        parts += [e.naive for e in entries if e.naive.strip()]
    return "\n\n".join(parts)


def _merge_images(lines):
    out, count = [], 0

    def flush():
        nonlocal count
        if count:
            out.append("[OBRÁZEK VYNECHÁN]" if count == 1 else f"[OBRÁZEK VYNECHÁN ×{count}]")
            count = 0

    for line in lines:
        if line == "[OBRÁZEK VYNECHÁN]":
            count += 1
        else:
            flush()
            out.append(line)
    flush()
    return out


def render(documents, rules, split=False, version=""):
    """Phase 2: replace, apply the savings and write the Markdown (one text or one file per document)."""
    documents = list(documents)
    skipped = [(d.index, d.code) for d in documents if not hasattr(d, "pages")]
    items = [_process(doc, rules, False) for doc in _documents(documents)]

    # 3. duplicate documents
    seen, duplicate = {}, {}
    for item in items:
        signature = _norm(" ".join(
            [*item.headers, *item.footers] + [e.text for _, entries, _ in item.pages for e in entries])).casefold()
        if not signature:
            continue
        if signature in seen:
            duplicate[item.doc.index] = seen[signature]
        else:
            seen[signature] = item.doc.index
    live = [item for item in items if item.doc.index not in duplicate]

    # 5. quoted e-mails whose original is part of another e-mail of the case file
    bodies = {}
    for item in live:
        if item.doc.format in ("EML", "MSG"):
            bodies[item.doc.index] = _norm(" ".join(
                e.text for _, entries, _ in item.pages for e in entries if e.kind == "p")).casefold()
    for item in live:
        for _, entries, _ in item.pages:
            for entry in entries:
                if entry.kind != "quote":
                    continue
                needle = (entry.quote or "").casefold()
                if len(needle) < CITATION_MIN:
                    continue
                source = next((index for index, body in bodies.items()
                               if index != item.doc.index and needle in body), None)
                if source is not None:
                    entry.text = f"[citace: D{source}]"

    # 4. repeated long paragraphs
    first, referenced, replace = {}, set(), {}
    for item in live:
        position = 0
        for _, entries, _ in item.pages:
            for entry in entries:
                position += 1
                if entry.kind != "p":
                    continue
                key = _norm(entry.text).casefold()
                if len(key) <= DUPLICATE_PARAGRAPH_MIN:
                    continue
                if key in first:
                    referenced.add(first[key])
                    replace[id(entry)] = first[key]
                else:
                    first[key] = (item.doc.index, position)
    # render bodies
    bodies_text = {}
    for item in live:
        lines, position = [], 0
        if item.headers:
            lines.append("Záhlaví: " + " | ".join(item.headers))
        if item.footers:
            lines.append("Zápatí: " + " | ".join(item.footers))
        for number, entries, _ in item.pages:
            if number is not None:
                lines.append(f"[s. {number}]")
            for entry in entries:
                position += 1
                if id(entry) in replace:
                    d, n = replace[id(entry)]
                    lines.append(f"[shodné s D{d} §{n}]")
                elif entry.kind == "p" and (item.doc.index, position) in referenced:
                    lines.append(f"§{position} {entry.text}")
                elif entry.text and not (entry.text.startswith("[citace: D") and lines and lines[-1] == entry.text):
                    lines.append(entry.text)
        bodies_text[item.doc.index] = short_labels("\n".join(_merge_images(lines)))
    for index, source in duplicate.items():
        bodies_text[index] = f"D{index} = shodný s D{source}"

    # table of contents
    ordered = sorted(items, key=lambda i: i.doc.index)
    contents, all_text = [], "\n".join(bodies_text.values())
    for item in ordered:
        doc = item.doc
        pages = sum(1 for page in doc.pages if page.number is not None)
        parts = [doc.format] + ([f"{pages} s."] if pages else [])
        if doc.parent is not None:
            parts.append(f"příloha D{doc.parent}")
        if doc.index not in duplicate:
            day = _date(item, [e for _, entries, _ in item.pages for e in entries])
            if day:
                parts.append(day)
        parts.append(f"~{_number(estimate_tokens(bodies_text[doc.index]))} tok.")
        contents.append(f"D{doc.index} " + ", ".join(parts))

    codes = {_short_code(m[1]) for m in LONG_LABEL.finditer(
        "\n".join(_all_long(live)))}
    legend = " · ".join(f"[{code}…] {name}" for code, name in LEGEND if code in codes)
    extra = sorted(codes - {code for code, _ in LEGEND})
    if extra:
        legend += (" · " if legend else "") + " · ".join(f"[{code}…] jiný údaj" for code in extra)
    head = ["# Anonymizovaný spis", f"SpisRedakt {version}. {WARNING}".replace("SpisRedakt .", "SpisRedakt.")]
    if legend:
        head += ["## Legenda", legend]
    head += ["## Obsah", *contents]
    tail = []
    if skipped:
        tail = ["## Nezpracované položky", *(f"položka {i}: {code}" for i, code in skipped)]

    def assemble(total):
        top = head + [f"Celkem ~{_number(total)} tokenů (odhad: znaky / {TOKEN_CHARS:g})"]
        index_text = "\n".join(top + tail)
        if split:
            files = {"00_obsah.md": index_text}
            for item in ordered:
                files[f"D{item.doc.index:04d}.md"] = _heading(item.doc) + "\n" + bodies_text[item.doc.index]
            return index_text, files
        sections = [_heading(item.doc) + "\n" + bodies_text[item.doc.index] for item in ordered]
        return "\n".join(top + sections + tail), {}

    text, files = assemble(0)
    size = sum(estimate_tokens(t) for t in (files.values() if split else [text]))
    text, files = assemble(size)
    after = sum(estimate_tokens(t) for t in (files.values() if split else [text]))

    naive = ["# Anonymizovaný spis", f"SpisRedakt {version}. {WARNING}"] + [_naive(item) for item in ordered]
    return Output(text=text, files=files, documents=len(items), skipped=skipped,
                  tokens_before=estimate_tokens("\n\n".join(naive)), tokens_after=after)


def _all_long(live):
    for item in live:
        for _, entries, _ in item.pages:
            for entry in entries:
                yield entry.text
        yield from item.headers
        yield from item.footers
