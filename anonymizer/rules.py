import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from .czech import LABEL, anchored, cls, colon_lines, contextual_findings, structured_identity


def key(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


RULES = {
    "EMAIL": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "IBAN": r"(?<!\w)CZ(?:[ \t]?\d){22}(?![ \t]?\d)",
    "RC": r"(?<!\d)\d{6}[ \t]*/[ \t]*\d{3,4}(?!\d)",
    "TELEFON": r"(?<![\w+])(?:\+420[ \t]*)?(?:[67]\d{2}(?:[ \t]?\d{3}){2}|[2-5]\d{2}(?:[ \t]\d{3}){2})(?!\d)",
    "UCET": r"(?:účet|účtu|bankovní spojení|č\.[ \t]*ú\.)[ \t]*:?[ \t]*((?:\d{1,6}-)?\d{2,10}/\d{4})(?!\d)",
    "DIC": r"\bCZ\d{8,10}\b",
    "DOKLAD": r"(?:(?:OP|pas|doklad)[ \t]*(?:č\.[ \t]*)?:[ \t]*)([A-Z0-9]{6,12})\b",
    "ICO": r"(?<!\w)IČO?[ \t]*:?[ \t]*((?:\d[ \t\u00a0]?){7}\d)(?![ \t\u00a0]?\d)",
}
COMPILED = {kind: re.compile(pattern, re.IGNORECASE) for kind, pattern in RULES.items()}
WHITESPACE = re.compile(r"\s")
LETTER_INITIAL = re.compile(r"\w\.")
INITIALS = re.compile(r"((?:\w\. ?)+) (\S+)")
EMAIL_RUN = re.compile(r"[\w.+-]+\Z")
STRIPPED = {"IBAN", "RC", "TELEFON", "UCET", "NAROZENI"}


def email_matches(text):
    """``COMPILED["EMAIL"].finditer(text)``, starting from each "@" instead of every word."""
    pattern, find, position = COMPILED["EMAIL"], text.find, 0
    at = find("@")
    while at != -1:
        # The lookbehind makes the start of the run before "@" the only possible start.
        low = max(at - 64, 0)
        run = EMAIL_RUN.search(text, low, at)
        while run and run.start() == low and low:
            low = max(at - 2 * (at - low), 0)
            run = EMAIL_RUN.search(text, low, at)
        found = pattern.match(text, run.start()) if run and run.start() >= position else None
        if found:
            yield found
            position = found.end()
            at = find("@", position)
        else:
            at = find("@", at + 1)


def rc_matches(text):
    """``COMPILED["RC"].finditer(text)``: six digits, blanks, then the slash."""
    pattern, find, position = COMPILED["RC"], text.find, 0
    slash = find("/")
    while slash != -1:
        start = slash
        while start and text[start - 1] in " \t":
            start -= 1
        # The six digits must directly precede the blanks, so the start is unique.
        if start - 6 >= position:
            found = pattern.match(text, start - 6)
            if found:
                yield found
                position = found.end()
                slash = find("/", position)
                continue
        slash = find("/", slash + 1)


CZ_START = re.compile(cls("c") + cls("z"))
PHONE_START = re.compile(r"[+2-7]\d")
# úč, ban, č.
ACCOUNT_START = re.compile(cls("účb") + "[" + cls("ča")[1:-1] + r".]")
ICO_START = re.compile(cls("i") + cls("č"))
SCANNERS = {
    "EMAIL": email_matches,
    "IBAN": lambda text: anchored(COMPILED["IBAN"], CZ_START, text),
    "RC": rc_matches,
    "TELEFON": lambda text: anchored(COMPILED["TELEFON"], PHONE_START, text),
    "UCET": lambda text: anchored(COMPILED["UCET"], ACCOUNT_START, text),
    "DIC": lambda text: anchored(COMPILED["DIC"], CZ_START, text),
    "DOKLAD": lambda text: colon_lines(COMPILED["DOKLAD"], text),
    "ICO": lambda text: anchored(COMPILED["ICO"], ICO_START, text),
}


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
        self.surnames = set()
        self.counts = Counter()
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

    def find(self, text):
        candidates = [(start, end, kind, key(identity))
                      for start, end, kind, identity in contextual_findings(text, self.surnames)]
        if self.custom:
            for match in self.custom.finditer(text):
                variant = self.variants[match.lastindex - 1]
                candidates.append((*match.span(), "VLASTNI", self.aliases[variant]))
        for kind, scan in SCANNERS.items():
            for match in scan(text):
                group = 1 if match.lastindex else 0
                identity = key(match[group])
                if kind in STRIPPED:
                    identity = WHITESPACE.sub("", identity)
                if kind == "TELEFON":
                    identity = identity.removeprefix("+420")
                candidates.append((*match.span(group), kind, identity))
        candidates.sort(key=lambda item: (item[0], item[0] - item[1]))
        merged = []
        for item in candidates:
            if merged and item[0] < merged[-1][1]:
                previous = merged[-1]
                if item[1] > previous[1]:
                    previous[3] = key(text[previous[0]:item[1]])
                    previous[1] = item[1]
            else:
                merged.append(list(item))
        return [Finding(*item) for item in merged]

    def label(self, item):
        identity = (item.kind, item.identity)
        if item.kind == "OSOBA" and identity not in self.labels:
            initials = INITIALS.fullmatch(item.identity)
            if initials:
                # "J. Novák" reuses the label of the only known "Jan Novák".
                first = initials[1][0]
                matches = {label for (kind, known), label in self.labels.items()
                           if kind == "OSOBA" and not LETTER_INITIAL.match(known)
                           and known.startswith(first) and known.split(" ", 1)[-1] == initials[2]}
                if len(matches) == 1:
                    self.labels[identity] = matches.pop()
            else:
                self.surnames.add(item.identity.split(" ", 1)[-1])
        if identity not in self.labels:
            self.counts[item.kind] += 1
            self.labels[identity] = f"[{item.kind}-{self.counts[item.kind]}]"
        return self.labels[identity]

    def replace(self, text):
        total = 0
        # A replacement can expose a new match next to a label; repeat until stable.
        for _ in range(3):
            findings = self.find(text)
            if not findings:
                break
            total += len(findings)
            # Labels are numbered from the end of the text, as in the Office formats.
            labels = [self.label(item) for item in reversed(findings)][::-1]
            pieces, position = [], 0
            for item, label in zip(findings, labels):
                pieces += [text[position:item.start], label]
                position = item.end
            text = "".join(pieces) + text[position:]
        return text, total

    def cell(self, kind, value):
        """Label a whole table cell whose column header marks it as personal data."""
        text = str(value).strip()
        if not text or LABEL.fullmatch(text):
            return None
        return self.label(Finding(0, len(text), kind, structured_identity(kind, text)))
