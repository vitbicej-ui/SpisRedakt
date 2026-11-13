import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .czech import LEGAL_FORM, contextual_findings, fold, surname_key
from .names import surname_pattern


def key(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


MONEY = r"(?![ \t ]*(?:Kč|,-|,\d\d(?!\d)|CZK|EUR|€|korun|Sk\b|tis\.|mil\.))"
GAP = r"[ \t\n ]*:?[ \t\n ]*"
RULES = {
    "EMAIL": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "IBAN": r"(?<!\w)(?:CZ|SK)(?:[ \t]?\d){22}(?![ \t]?\d)",
    "RC": r"(?<!\d)\d{6}[ \t]*/[ \t]*\d{3,4}(?!\d)",
    "RC2": r"(?:r\.[ \t]*č\.|rodn[ée][ \t]+čísl[oa]|RČ)" + GAP + r"(\d{9,10})(?!\d)",
    "TELEFON": r"(?<![\w+])(?<!\d[ \t ])(?:(?:\+42[01]|0042[01])[ \t]*)?(?:[67]\d{2}(?:[ \t-]?\d{3}){2}|[2-59]\d{2}(?:[ \t-]\d{3}){2})(?!\d)" + MONEY,
    "UCET": r"(?:účet|účtu|účtem|bankovní spojení|č\.[ \t]*ú\.)[ \t\n ]*(?:č\.[ \t]*)?" + GAP + r"((?:\d{1,6}-)?\d{2,10}/\d{4})(?!\d)",
    "UCET2": r"(?<![\w/-])(?:\d{1,6}-)?\d{7,10}/\d{4}(?!\d)",
    "SPZ": r"(?:\bRZ|\bSPZ|\bEČV?|registrační[ \t]+značk\w+)" + GAP + r"(\d[A-Z0-9]{1,2}[ \t]?[A-Z0-9]{4}|[A-Z]{2}[ \t-]?\d{3}[ \t-]?[A-Z]{2})\b",
    "VIN": r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-Z])[A-HJ-NPR-Z0-9]{17}\b",
    "DIC": r"\b(?:CZ|SK)\d{8,10}\b",
    "DOKLAD": r"(?:(?:OP|pas|pasu|doklad|dokladu|průkaz|průkazu|preukaz|preukazu)[ \t]*(?:č\.[ \t]*)?" + GAP + r")((?=[A-Z0-9]*\d)[A-Z0-9]{6,12})\b",
    "ICO": r"(?<!\w)(?:IČO?|identifikační[ \t]+číslo(?:[ \t]+osoby)?)" + GAP + r"((?:\d[ \t ]?){7}\d)(?![ \t ]?\d)",
    # also catches an e-mail whose "@" OCR misread as a letter
    "WEB": r"(?<![\w@.-])(?:[\w-]{2,}\.)+(?:cz|sk|com|net|org|eu|info|test)(?![\w-])",
    "DS": r"(?:ID[ \t]+DS|datov\w+[ \t]+schránk\w+)" + GAP + r"([a-z0-9]{7})\b",
}
# Typographic spaces and hyphens that fonts and Word put in place of the plain ones.
PLAIN = {ord(char): " " for char in "      "} | {
    ord(char): "-" for char in "­‐‑"}
COMPILED = {kind: re.compile(pattern, re.IGNORECASE) for kind, pattern in RULES.items()}
for _kind in ("DOKLAD", "SPZ", "VIN"):
    COMPILED[_kind] = re.compile(RULES[_kind])
NEUTRAL = set("slovenska republika ceska sr cr slovensko cesko muz zena muzske zenske muzsky zensky "
              "ano nie ne neuvedene neuvedeno bez zaznamu".split())
PRIORITY = {"OSOBNI_UDAJ": 2, "OSOBA": 1}


@dataclass(frozen=True)
class Finding:
    start: int
    end: int
    kind: str
    identity: str


class Rules:
    def __init__(self, alias_lines=()):
        self.aliases = {}
        self.labels = {}
        self.counts = Counter()
        self.surnames = {}      # regex source -> surname key
        self.people = {}        # surname key -> set of full identities
        self.learned = {}       # regex source -> (kind, identity), matched on folded text
        self.brands = {}        # folded all-capitals company brand -> identity
        self._surname = self._learned = None
        patterns = []
        for line in alias_lines:
            variants = [key(part) for part in line.split("|") if part.strip()]
            for variant in variants:
                if variant in self.aliases and self.aliases[variant] != variants[0]:
                    raise ValueError("Jeden tvar je přiřazen více osobám nebo údajům.")
                self.aliases[variant] = variants[0]
        self.variants = sorted(self.aliases, key=len, reverse=True)
        for variant in self.variants:
            patterns.append("(" + r"[ \t\n]+".join(re.escape(word) for word in variant.split()) + ")")
        self.custom = re.compile(r"(?<!\w)(?:" + "|".join(patterns) + r")(?!\w)", re.I) if patterns else None

    # -- knowledge carried across paragraphs, pages and files of one batch ----------------
    def known_surname(self, word):
        if self._surname is None and self.surnames:
            self._surname = re.compile("(?:" + "|".join(self.surnames) + ")", re.I)
        return bool(self._surname and self._surname.fullmatch(word))

    def remember_literal(self, value, kind="OSOBNI_UDAJ", identity=None):
        """Redact this exact value wherever it appears again, whatever its context."""
        words = re.findall(r"[^\W_]+", fold(value))
        if (sum(map(len, words)) < 4 or all(word in NEUTRAL for word in words)
                or not any(len(word) > 2 for word in words)):
            return
        source = r"[\W_]{0,3}".join(re.escape(word) for word in words)
        if source not in self.learned:
            self.learned[source] = (kind, key(identity or value))
            self._learned = None

    def _remember(self, item, text):
        value = text[item.start:item.end]
        if item.kind == "OSOBA" and not item.identity.startswith("příjmení "):
            surname = item.identity.split(" ", 1)[1].split("-")[0]
            self.people.setdefault(surname, set()).add(item.identity)
            words = value.split()
            for word in words[1:] if len(words) > 1 else words:
                for part in word.split("-"):
                    source = surname_pattern(part)
                    if source and source not in self.surnames:
                        self.surnames[source] = surname
                        self._surname = None
        elif item.kind == "ORGANIZACE":
            form = None
            for form in LEGAL_FORM.finditer(value):
                pass
            core = value[:form.start()] if form else value
            words = [word for word in re.findall(r"[^\W_]+", fold(core)) if len(word) > 1]
            if words and max(map(len, words)) >= 3 and sum(map(len, words)) >= 4:
                source = r"[\W_]{1,3}".join(
                    re.escape(re.sub(r"[aeiouy]+$", "", word) if len(word) > 4 else word) + r"[a-z]{0,3}"
                    for word in words)
                if source not in self.learned:
                    self.learned[source] = ("ORGANIZACE", item.identity)
                    self._learned = None
            # a distinctive all-capitals brand also stands for the company on its own
            brand = re.match(r"[\W_]*([^\W\d_]{3,}(?:-[^\W\d_]{2,})*)", core)
            if brand and brand[1].isupper():
                source = re.escape(fold(brand[1]))
                self.brands.setdefault(source, item.identity)
        elif item.kind == "OSOBNI_UDAJ":
            self.remember_literal(value)

    def learn(self, text):
        self.find(text)

    def find(self, text, generic=True):
        """Findings in text. generic=False skips the bare "two capitalised words" guess,
        for technical strings such as style names."""
        if not text or not text.strip():
            return []
        text = text.translate(PLAIN)        # same length, so offsets still fit the caller's text
        known = len(self.surnames)
        candidates = [Finding(start, end, kind, key(identity))
                      for start, end, kind, identity in contextual_findings(text, self.known_surname, generic)]
        if self.custom:
            for match in self.custom.finditer(text):
                variant = self.variants[match.lastindex - 1]
                candidates.append(Finding(*match.span(), "VLASTNI", self.aliases[variant]))
        for kind, pattern in COMPILED.items():
            if kind == "WEB" and not generic:
                continue                    # technical strings are full of vendor domains
            for match in pattern.finditer(text):
                group = 1 if match.lastindex else 0
                identity = key(match[group])
                kind = kind.rstrip("2")
                if kind in {"IBAN", "RC", "TELEFON", "UCET", "ICO"}:
                    identity = re.sub(r"[\s/]", "", identity)
                if kind == "TELEFON":
                    identity = re.sub(r"^(?:\+|00)42[01]", "", identity)
                candidates.append(Finding(*match.span(group), kind, identity))
        for item in candidates:
            self._remember(item, text)
        if len(self.surnames) != known:
            return self.find(text, generic)       # surnames met here also stand alone elsewhere
        if self.learned:
            if self._learned is None:
                self._learned = [(re.compile(r"(?<![^\W_])" + source + r"(?![^\W_])"), value)
                                 for source, value in self.learned.items()]
            folded = fold(text)
            for pattern, (kind, identity) in self._learned:
                for match in pattern.finditer(folded):
                    if kind != "ORGANIZACE" or text[match.start()].isupper():
                        candidates.append(Finding(*match.span(), kind, identity))
        for source, identity in self.brands.items():
            for match in re.finditer(r"(?<![^\W_])" + source + r"(?![^\W_])", fold(text)):
                if text[match.start():match.end()].isupper():
                    candidates.append(Finding(*match.span(), "ORGANIZACE", identity))
        resolved = []
        for item in candidates:
            if item.kind == "OSOBA" and item.identity.startswith("příjmení "):
                surname = item.identity.split(" ", 1)[1]
                for source, known in self.surnames.items():
                    if re.fullmatch(source, text[item.start:item.end].split()[-1], re.I):
                        surname = known
                        break
                owners = self.people.get(surname, ())
                if len(owners) == 1:
                    item = Finding(item.start, item.end, item.kind, next(iter(owners)))
            resolved.append(item)
        resolved.sort(key=lambda item: (item.start, -(item.end - item.start), PRIORITY.get(item.kind, 0)))
        merged = []
        for item in resolved:
            if merged and item.start < merged[-1].end:
                previous = merged[-1]
                merged[-1] = Finding(previous.start, max(previous.end, item.end), previous.kind,
                                     previous.identity if item.end <= previous.end else key(text[previous.start:item.end]))
            else:
                merged.append(item)
        return merged

    def label(self, item):
        identity = (item.kind, item.identity)
        if identity not in self.labels:
            self.counts[item.kind] += 1
            self.labels[identity] = f"[{item.kind}-{self.counts[item.kind]}]"
        return self.labels[identity]

    def replace(self, text):
        findings = self.find(text)
        for item in reversed(findings):
            text = text[:item.start] + self.label(item) + text[item.end:]
        return text, len(findings)
