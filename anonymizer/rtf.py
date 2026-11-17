"""Redact RTF documents while keeping their control words and formatting.

The parser keeps every token of the original file. Visible characters are
collected per paragraph or table cell, redacted with the shared rules and
written back; only the replaced characters change. Document properties,
fallback pictures and comment authors are removed, embedded PNG and JPEG
pictures are redacted like other images and unknown objects stop processing.
"""
import re

from .czech import plan_table

TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|(\r\n|\r|\n)|([^\\{}\r\n]+)")
DISABLED_LINK = "https://example.invalid/"
# Destinations removed from the output entirely.
DROP = {"info", "userprops", "nonshppict", "atnid", "atnauthor", "docvar", "passwordhash", "password"}
# Destinations whose text is visible to readers.
VISIBLE = {"annotation", "footnote", "header", "headerl", "headerr", "headerf", "footer", "footerl",
           "footerr", "footerf", "fldrslt", "shptxt", "pntext", "pntxta", "pntxtb"}
# Hidden destinations that hold free text and must not keep personal data.
CHECKED = {"bkmkstart", "bkmkend", "xe", "tc", "sv", "fldinst", "txe"}
NONTEXT = {"fonttbl", "colortbl", "stylesheet", "listtable", "listoverridetable", "revtbl", "rsidtbl",
           "pict", "object", "sp", "sn", "filetbl", "generator", "themedata", "colorschememapping",
           "datastore", "latentstyles", "xmlnstbl", "pgdptbl", "mmathPr", "listtext", "blipuid", "fldinst",
           "bkmkstart", "bkmkend", "xe", "tc", "sv", "txe", "objdata", "shpinst", "falt", "panose"}
BREAKS = {"par", "cell", "row", "sect", "page", "nestcell", "nestrow"}
SPACES = {"line": " ", "tab": " ", "emdash": "—", "endash": "–", "lquote": "‘", "rquote": "’",
          "ldblquote": "“", "rdblquote": "”", "bullet": "•", "~": "\u00a0"}


class Tables:
    """Table structure seen while walking: cells are lists of blocks.

    The state is shared by all groups, because ``\\cell`` is often written
    inside braces. Content that cannot be assigned reliably to a cell is left
    to the text rules.
    """

    def __init__(self):
        self.tables, self.rows, self.row, self.cell = [], [], [], []
        self.edges, self.keys = [], []
        self.intbl = self.in_row = False
        self.itap, self.tainted, self.pending = 1, False, None

    def active(self):
        return self.intbl or self.in_row

    def close(self):
        if self.rows and not self.tainted:
            self.tables.append((self.rows, self.keys))
        self.rows, self.row, self.cell, self.tainted = [], [], [], False
        self.keys = []

    def note(self, block):
        if not self.active():
            self.close()
        elif not self.cell or self.cell[-1] is not block:
            self.cell.append(block)

    def word(self, name):
        if name == "pard":
            self.intbl, self.itap = False, 1
        elif name == "intbl":
            self.intbl = True
        elif name == "trowd":
            self.in_row = True
            self.edges = []
        elif name == "cellx":
            self.edges.append(self.pending)
        elif name == "par":
            if not self.active():
                self.close()
        elif name in ("cell", "row"):
            if self.itap > 1:
                self.tainted = True
            self.itap = 1
            if name == "cell":
                self.row.append(self.cell)
                self.cell = []
            else:
                self.rows.append(self.row)
                self.keys.append((len(self.row), tuple(self.edges)))
                self.row, self.cell = [], []
                self.in_row = self.intbl = False

    def param(self, name, value):
        self.pending = value
        if name == "itap" and value is not None:
            self.itap = value


class Token:
    __slots__ = ("raw", "kind", "name", "param", "fallback", "edits")

    def __init__(self, raw, kind, name=None, param=None):
        self.raw, self.kind, self.name, self.param = raw, kind, name, param
        self.fallback = []
        self.edits = {}

    def output(self):
        if not self.edits:
            return self.raw
        return b"".join(self.edits.get(index, self.raw[index:index + 1]) for index in range(len(self.raw)))


class Group:
    def __init__(self):
        self.children = []
        self.dropped = False

    def destination(self):
        """Return (name, starred) of the group's first control word."""
        starred = False
        for child in self.children:
            if isinstance(child, Group):
                return None, starred
            if child.kind == "symbol" and child.name == "*":
                starred = True
            elif child.kind == "word":
                return child.name, starred
            elif child.kind != "newline":
                return None, starred
        return None, starred

    def raw(self):
        if self.dropped:
            return b""
        return b"{" + b"".join(child.raw() if isinstance(child, Group) else child.output()
                               for child in self.children) + b"}"


def parse(data):
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("Soubor není platný RTF.")
    root, stack, position = Group(), [], 0
    current = root
    while position < len(data):
        match = TOKEN.match(data, position)
        if not match:
            raise ValueError("RTF obsahuje neplatný zápis.")
        position = match.end()
        word, param, hexa, symbol, brace, newline, text = match.groups()
        if brace == b"{":
            group = Group()
            current.children.append(group)
            stack.append(current)
            current = group
        elif brace == b"}":
            if not stack:
                raise ValueError("RTF má nevyvážené závorky.")
            current = stack.pop()
        elif word is not None:
            name = word.decode("ascii")
            token = Token(match[0], "word", name, int(param) if param else None)
            if name == "bin" and token.param:
                # Binary data follows verbatim and may contain braces.
                if token.param < 0 or position + token.param > len(data):
                    raise ValueError("RTF obsahuje poškozená binární data.")
                token.raw += data[position:position + token.param]
                position += token.param
            current.children.append(token)
        elif hexa is not None:
            current.children.append(Token(match[0], "hex", param=int(hexa, 16)))
        elif symbol is not None:
            current.children.append(Token(match[0], "symbol", symbol.decode("latin-1")))
        elif newline is not None:
            current.children.append(Token(match[0], "newline"))
        else:
            current.children.append(Token(text, "text"))
    if stack or len(root.children) != 1 or not isinstance(root.children[0], Group):
        raise ValueError("RTF má nevyvážené závorky nebo obsah mimo dokument.")
    return root.children[0]


def codepage(document):
    for child in document.children:
        if isinstance(child, Token) and child.name == "ansicpg" and child.param:
            return f"cp{child.param}"
    return "cp1252"


def walk(group, state, blocks, checked, pictures):
    """Collect visible characters into blocks of [(char, token, offset)]."""
    name, starred = group.destination()
    if name in DROP:
        group.dropped = True
        return
    if name == "object":
        raise ValueError("RTF obsahuje vložený objekt. Vložte jeho obsah přímo do dokumentu.")
    if name == "pict":
        pictures.append(group)
        return
    state = dict(state)
    if name == "nesttableprops":
        state["nest"] = True
    if name in VISIBLE:
        state["visible"] = True
        blocks.append([])
    elif starred or name in NONTEXT:
        state["visible"] = False
    if name in CHECKED:
        state["target"] = []
        checked.append((name, state["target"]))

    def emit(char, token, offset=None):
        if state["target"] is not None:
            state["target"].append((char, token, offset))
        elif state["visible"]:
            state["tables"].note(blocks[-1])
            blocks[-1].append((char, token, offset))

    pending, owner = 0, None
    for child in group.children:
        if isinstance(child, Group):
            walk(child, state, blocks, checked, pictures)
            continue
        if child.kind == "word" and not state.get("nest"):
            state["tables"].param(child.name, child.param)
            state["tables"].word(child.name)
        if child.kind == "text":
            for offset, byte in enumerate(child.raw):
                if pending:
                    owner.fallback.append((child, offset))
                    pending -= 1
                    continue
                emit(bytes([byte]).decode(state["codepage"], "replace"), child, offset)
            continue
        if pending and child.kind == "hex":
            owner.fallback.append((child, None))
            pending -= 1
            continue
        pending = 0
        char = None
        if child.kind == "word":
            if child.name == "uc" and child.param is not None:
                state["uc"] = child.param
            elif child.name == "u" and child.param is not None:
                char = chr(child.param % 65536)
                owner, pending = child, state["uc"]
            elif child.name in BREAKS:
                if state["visible"] and state["target"] is None:
                    blocks.append([])
            elif child.name in SPACES:
                char = SPACES[child.name]
        elif child.kind == "symbol":
            char = SPACES.get(child.name, child.name if child.name in "\\{}" else None)
        elif child.kind == "hex":
            char = bytes([child.param]).decode(state["codepage"], "replace")
        if char is not None:
            emit(char, child)
    if name in VISIBLE:
        blocks.append([])


def redact_pictures(pictures, rules, tessdata, redact_image):
    for group in pictures:
        words = {child.name for child in group.children if isinstance(child, Token) and child.kind == "word"}
        if "bin" in words:
            raise ValueError("RTF obsahuje binárně vložený obrázek, který nelze zkontrolovat.")
        kind = "png" if "pngblip" in words else "jpeg" if "jpegblip" in words else None
        if kind is None:
            raise ValueError("RTF obsahuje obrázek v nepodporovaném formátu. Použijte PNG nebo JPEG.")
        hex_tokens = [child for child in group.children if isinstance(child, Token) and child.kind == "text"]
        data = bytes.fromhex(b"".join(token.raw for token in hex_tokens).decode("ascii", "replace"))
        redacted = redact_image(data, "png" if kind == "png" else "jpg", rules, tessdata).hex().encode()
        for index, token in enumerate(hex_tokens):
            token.raw = b"\n".join(redacted[i:i + 128] for i in range(0, len(redacted), 128)) if index == 0 else b""
        group.children = [child for child in group.children if not (isinstance(child, Token) and child.kind == "newline")]


def set_text(token, offset, value):
    value = value.encode("ascii")
    if offset is None:
        token.raw = value
    else:
        token.edits[offset] = value
    for fallback, position in token.fallback:
        set_text(fallback, position, "")


def collect(document):
    blocks, checked, pictures, tables = [[]], [], [], Tables()
    walk(document, {"visible": True, "uc": 1, "codepage": codepage(document), "target": None,
                    "tables": tables}, blocks, checked, pictures)
    tables.close()
    return blocks, checked, pictures, tables.tables


def redact_rtf(data, rules, tessdata=None, redact_image=None):
    document = parse(data)
    blocks, checked, pictures, tables = collect(document)
    if pictures:
        redact_pictures(pictures, rules, tessdata, redact_image)
    labelled = set()
    for rows, keys in tables:
        texts = [[" ".join(t for t in ("".join(char for char, _, _ in block).strip() for block in cell) if t)
                  for cell in row] for row in rows]
        plan = plan_table([list(enumerate(row)) for row in texts])
        # Rows of different shape may be separate tables written back to back:
        # a cell is labelled when the whole table or any such section marks it.
        start = 0
        for end in range(1, len(rows) + 1):
            if end == len(rows) or keys[end] != keys[start]:
                if (start, end) != (0, len(rows)):
                    part = plan_table([list(enumerate(row)) for row in texts[start:end]])
                    for (index, position), kind in part.items():
                        plan.setdefault((start + index, position), kind)
                start = end
        for (index, position), kind in plan.items():
            cell = rows[index][position]
            label = rules.cell(kind, texts[index][position])
            if not label:
                continue
            chars = [item for block in cell for item in block]
            for number, (_, token, offset) in enumerate(chars):
                set_text(token, offset, label if number == 0 else "")
            labelled.update(id(block) for block in cell)
    for block in blocks:
        if id(block) in labelled:
            continue
        text = "".join(char for char, _, _ in block)
        for match in reversed(rules.find(text)):
            label = rules.label(match)
            for index in range(match.start, match.end):
                _, token, offset = block[index]
                set_text(token, offset, label if index == match.start else "")
    for name, items in checked:
        text = "".join(char for char, _, _ in items)
        if name == "fldinst" and re.search(r"\bHYPERLINK\b", text, re.I):
            for index, (_, token, offset) in enumerate(items):
                set_text(token, offset, f' HYPERLINK "{DISABLED_LINK}" ' if index == 0 else "")
        elif rules.find(text):
            raise ValueError("Citlivý údaj je ve skryté části RTF (pole, záložka nebo rejstřík).")
    output = document.raw()
    # Re-read the result: nothing the rules recognise may remain visible.
    blocks, checked, _, _ = collect(parse(output))
    for block in blocks + [items for _, items in checked]:
        if rules.find("".join(char for char, _, _ in block)):
            raise ValueError("Kontrola výstupu RTF našla zbývající údaj.")
    return output
