"""Convert binary Word 97-2003 (DOC) and Excel 97-2003 (XLS) files to DOCX and XLSX.

The converted files then go through the regular Office redaction. Only text,
table structure and cell values are carried over: formatting, images,
formulas, comments and macros are not, so nothing hidden can slip through.
"""
import io
import re
import struct
from bisect import bisect_right
from datetime import datetime
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

PICTURE = "[OBRÁZEK VYNECHÁN]"


# -- DOC -----------------------------------------------------------------------

def doc_text(data):
    """Return the main text and the text of footnotes, headers, comments and text boxes."""
    return _doc_parts(data)[:2]


def _doc_parts(data):
    """Return main text, other text and per-character table properties (or None).

    The properties are a list with one ``(itap, ttp)`` tuple per character of
    the main text; None means the paragraph properties could not be read
    reliably and the table structure has to be guessed from the characters.
    """
    import olefile

    if not olefile.isOleFile(io.BytesIO(data)):
        raise ValueError("Soubor není platný dokument Word 97-2003.")
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if not ole.exists("WordDocument"):
            raise ValueError("Soubor není platný dokument Word 97-2003.")
        word = ole.openstream("WordDocument").read()
        if len(word) < 0x1AA or struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
            raise ValueError("Dokument Word má nepodporovanou verzi. Uložte jej jako DOCX.")
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        if flags & 0x0100:
            raise ValueError("Dokument Word je zašifrovaný.")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise ValueError("Dokument Word je poškozený.")
        table = ole.openstream(table_name).read()
    csw = struct.unpack_from("<H", word, 32)[0]
    lw = 32 + 2 + csw * 2 + 2
    cslw = struct.unpack_from("<H", word, lw - 2)[0]
    counts = struct.unpack_from(f"<{min(cslw, 11)}i", word, lw)
    fc_lcb = lw + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", word, fc_lcb + 33 * 8)
    clx = table[fc_clx:fc_clx + lcb_clx]
    position = 0
    while position < len(clx) and clx[position] == 0x01:
        position += 3 + struct.unpack_from("<H", clx, position + 1)[0]
    if position >= len(clx) or clx[position] != 0x02:
        raise ValueError("Dokument Word nemá čitelnou tabulku textu.")
    size = struct.unpack_from("<I", clx, position + 1)[0]
    plc = clx[position + 5:position + 5 + size]
    pieces = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    text, located = [], []
    for index in range(pieces):
        fc, prm = struct.unpack_from("<IH", plc, (pieces + 1) * 4 + index * 8 + 2)
        length = cps[index + 1] - cps[index]
        if fc & 0x40000000:
            offset = (fc & ~0x40000000) // 2
            text.append(word[offset:offset + length].decode("cp1252", "replace"))
            located.append((offset, 1, cps[index], length, prm))
        else:
            text.append(word[fc:fc + 2 * length].decode("utf-16-le", "replace"))
            located.append((fc, 2, cps[index], length, prm))
    full = "".join(text)
    main = counts[3]
    others = sum(max(0, value) for value in counts[4:11])
    main_text = full[:main]
    try:
        flags = _paragraph_flags(word, table, fc_lcb, located, len(main_text))
    except (struct.error, ValueError, IndexError, OverflowError):
        flags = None
    if flags is not None and "\x07" in main_text and not any(item[0] for item in flags):
        flags = None
    return main_text, full[main:main + others], flags


_SPRM_SIZES = {0: 1, 1: 1, 2: 2, 3: 4, 4: 2, 5: 2, 7: 3}
FRAME = 512


def _parse_sprms(grpprl):
    """Return the table related sprms of a paragraph property list as a dict."""
    found, position = {}, 0
    while position + 2 <= len(grpprl):
        opcode = struct.unpack_from("<H", grpprl, position)[0]
        spra = opcode >> 13
        position += 2
        if spra == 6:
            if opcode == 0xD608:
                size = struct.unpack_from("<H", grpprl, position)[0] + 1
            else:
                if position >= len(grpprl) or grpprl[position] == 255:
                    raise ValueError
                size = grpprl[position] + 1
        else:
            size = _SPRM_SIZES[spra]
        if position + size > len(grpprl):
            raise ValueError
        if opcode in (0x2416, 0x2417, 0x244B, 0x244C):
            found[opcode] = grpprl[position] & 1
        elif opcode == 0x6649:
            found[opcode] = struct.unpack_from("<i", grpprl, position)[0]
        position += size
    return found


def _paragraph_flags(word, table, fc_lcb, located, length):
    """Read table properties of the paragraphs (PlcBtePapx); None if inconsistent."""
    fc_plc, lcb_plc = struct.unpack_from("<II", word, fc_lcb + 13 * 8)
    if lcb_plc < 12 or (lcb_plc - 4) % 8 or fc_plc + lcb_plc > len(table):
        return None
    count = (lcb_plc - 4) // 8
    plc = table[fc_plc:fc_plc + lcb_plc]
    fcs = struct.unpack_from(f"<{count + 1}I", plc, 0)
    pages = struct.unpack_from(f"<{count}I", plc, (count + 1) * 4)
    spans = sorted((start, start + size * length_, cp, size)
                   for start, size, cp, length_, _ in located)
    starts = [item[0] for item in spans]
    state = [None] * length

    def assign(first, last, props):
        position = bisect_right(starts, first) - 1
        while position >= 0 and position < len(spans) and spans[position][0] < last:
            begin, end, cp, size = spans[position]
            low, high = max(first, begin), min(last, end)
            if low < high:
                a = cp + (low - begin) // size
                b = cp + (high - begin) // size
                for index in range(a, min(b, length)):
                    state[index] = props
            position += 1

    for page in pages:
        base = page * FRAME
        if base + FRAME > len(word):
            return None
        runs = word[base + FRAME - 1]
        if runs == 0 or 4 * (runs + 1) + 13 * runs > FRAME - 1:
            return None
        edges = struct.unpack_from(f"<{runs + 1}I", word, base)
        for run in range(runs):
            offset = word[base + 4 * (runs + 1) + 13 * run] * 2
            if offset == 0:
                found = {}
            else:
                if offset >= FRAME - 1:
                    return None
                size = word[base + offset]
                position = base + offset + 1
                if size == 0:
                    size = word[position]
                    position += 1
                    size *= 2
                else:
                    size = size * 2 - 1
                if position + size > base + FRAME - 1 or size < 2:
                    return None
                found = _parse_sprms(word[position + 2:position + size])
            if edges[run] > edges[run + 1]:
                return None
            assign(edges[run], edges[run + 1], found)
    # Piece level modifications (Prm) can override table properties; their
    # mapping is not verified, so such documents keep the character heuristic.
    if any(prm for *_, prm in located):
        return None
    if any(item is None for item in state):
        return None
    result = []
    for found in state:
        itap = found.get(0x6649, 0)
        if itap <= 0:
            itap = 1 if found.get(0x2416) else 0
        result.append((itap, bool(found.get(0x2417) or found.get(0x244C))))
    return result


def clean(text):
    """Hide field codes and replace object placeholders."""
    return clean_indexed(text)[0]


def clean_indexed(text):
    """Like ``clean``, also returning the source index of every output character."""
    output, sources, depth, hidden = [], [], 0, []
    for position, char in enumerate(text):
        if char == "\x13":
            depth += 1
            hidden.append(True)
        elif char == "\x14":
            if hidden:
                hidden[-1] = False
        elif char == "\x15":
            if hidden:
                hidden.pop()
            depth = max(0, depth - 1)
        elif any(hidden):
            continue
        elif char in "\x01\x08":
            output.append(PICTURE)
            sources.extend([position] * len(PICTURE))
        elif char in "\x02\x05":
            continue
        elif char in "\x0b\x0c\x0e":
            output.append("\n" if char == "\x0b" else "\r")
            sources.append(position)
        else:
            output.append(char)
            sources.append(position)
    return "".join(output), sources


def _flush_rows(document, rows):
    width = max((len(row) for row in rows), default=0)
    if width:
        table = document.add_table(rows=len(rows), cols=width)
        for row, values in zip(table.rows, rows):
            for cell, value in zip(row.cells, values):
                cell.text = value


def _add_by_properties(document, text, flags):
    """Build paragraphs and tables from per-character paragraph properties."""
    rows, cells, cell, paragraph = [], [], [], []
    started = False

    def close_cell():
        cells.append("".join(cell).strip("\n"))
        cell.clear()

    def close_table():
        nonlocal rows, started
        if cell:
            close_cell()
        if cells:
            rows.append(list(cells))
            cells.clear()
        _flush_rows(document, [row for row in rows if row])
        rows = []
        started = False

    for char, (itap, ttp) in zip(text, flags):
        if itap == 0:
            if started:
                close_table()
            if char in "\r\x07":
                document.add_paragraph("".join(paragraph))
                paragraph = []
            else:
                paragraph.append(char)
            continue
        if paragraph:
            document.add_paragraph("".join(paragraph))
            paragraph = []
        started = True
        if itap > 1:
            # A nested table is flattened into the text of the outer cell.
            if char in "\r\x07":
                if cell and cell[-1] != "\n":
                    cell.append("\n")
            else:
                cell.append(char)
        elif char == "\x07":
            if ttp:
                if cell:
                    close_cell()
                rows.append(list(cells))
                cells.clear()
            else:
                close_cell()
        elif char == "\r":
            cell.append("\n")
        else:
            cell.append(char)
    if started:
        close_table()
    if paragraph:
        document.add_paragraph("".join(paragraph))


def _add_by_characters(document, text):
    """Guess the table structure from the cell marks (no paragraph properties)."""
    rows, cells, paragraph = [], [], []
    last_was_cell = False

    def flush_table():
        nonlocal rows
        if rows:
            _flush_rows(document, rows)
            rows = []

    # Word ends the last paragraph of a cell with a cell mark (\x07) and a row
    # with one more mark. Without paragraph properties, a paragraph break inside
    # a started row is kept as a line break of the cell, and a paragraph break
    # outside a row ends the table.
    for char in text:
        if char == "\x07":
            if last_was_cell and not paragraph:
                rows.append(cells)
                cells = []
                last_was_cell = False
            else:
                cells.append("".join(paragraph))
                paragraph = []
                last_was_cell = True
            continue
        last_was_cell = False
        if char == "\r" and cells:
            paragraph.append("\n")
        elif char == "\r":
            flush_table()
            document.add_paragraph("".join(paragraph))
            paragraph = []
        else:
            paragraph.append(char)
    if cells:
        rows.append(cells)
    flush_table()
    if paragraph:
        document.add_paragraph("".join(paragraph))


def doc_to_docx(data):
    from docx import Document

    main, extra, flags = _doc_parts(data)
    document = Document()
    text, sources = clean_indexed(main)
    if flags is None:
        _add_by_characters(document, text)
    else:
        _add_by_properties(document, text, [flags[index] for index in sources])
    extra = clean(extra).replace("\x07", "\r").strip("\r")
    if extra.strip():
        document.add_paragraph("Poznámky, záhlaví, zápatí, komentáře a textová pole:")
        for line in extra.split("\r"):
            if line.strip():
                document.add_paragraph(line)
    output = io.BytesIO()
    document.core_properties.author = ""
    document.save(output)
    return output.getvalue()


# -- XLS -----------------------------------------------------------------------

def column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xls_to_xlsx(data):
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=False)
    except xlrd.XLRDError as error:
        raise ValueError("Sešit Excel 97-2003 nelze přečíst nebo je zašifrovaný.") from error
    sheets = []
    for sheet in book.sheets():
        rows = []
        for row_index in range(sheet.nrows):
            cells = []
            for column, cell in enumerate(sheet.row(row_index)):
                reference = f"{column_name(column)}{row_index + 1}"
                if cell.ctype == xlrd.XL_CELL_TEXT and cell.value:
                    cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
                                 f"{escape(cell.value)}</t></is></c>")
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    cells.append(f'<c r="{reference}"><v>{repr(float(cell.value)).removesuffix(".0")}</v></c>')
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    moment = xlrd.xldate_as_datetime(cell.value, book.datemode)
                    serial = (moment - datetime(1899, 12, 30)).total_seconds() / 86400
                    cells.append(f'<c r="{reference}" s="1"><v>{repr(serial).removesuffix(".0")}</v></c>')
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    cells.append(f'<c r="{reference}" t="b"><v>{int(cell.value)}</v></c>')
            if cells:
                rows.append(f'<row r="{row_index + 1}">{"".join(cells)}</row>')
        sheets.append((sheet.name, "".join(rows)))
    if not sheets:
        raise ValueError("Sešit Excel neobsahuje žádný list.")
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relations = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, len(sheets) + 1)) + "</Types>"))
        archive.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{relations}/officeDocument" Target="xl/workbook.xml"/></Relationships>'))
        archive.writestr("xl/workbook.xml", (
            f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main}" xmlns:r="{relations}"><sheets>'
            + "".join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                      for i, (name, _) in enumerate(sheets, 1)) + "</sheets></workbook>"))
        archive.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="{relations}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(sheets) + 1))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="{relations}/styles" Target="styles.xml"/></Relationships>'))
        archive.writestr("xl/styles.xml", (
            f'<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{main}">'
            '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="2"><xf/><xf numFmtId="14" applyNumberFormat="1"/></cellXfs></styleSheet>'))
        for i, (_, rows) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{i}.xml", (
                f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{main}"><sheetData>{rows}</sheetData></worksheet>'))
    return output.getvalue()
