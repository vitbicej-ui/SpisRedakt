import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from .czech import LABEL, contextual_findings, structured_identity


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
        candidates = [Finding(start, end, kind, key(identity))
                      for start, end, kind, identity in contextual_findings(text, self.surnames)]
        if self.custom:
            for match in self.custom.finditer(text):
                variant = self.variants[match.lastindex - 1]
                candidates.append(Finding(*match.span(), "VLASTNI", self.aliases[variant]))
        for kind, pattern in COMPILED.items():
            for match in pattern.finditer(text):
                group = 1 if match.lastindex else 0
                identity = key(match[group])
                if kind in {"IBAN", "RC", "TELEFON", "UCET", "NAROZENI"}:
                    identity = re.sub(r"\s", "", identity)
                if kind == "TELEFON":
                    identity = identity.removeprefix("+420")
                candidates.append(Finding(*match.span(group), kind, identity))
        candidates.sort(key=lambda item: (item.start, -(item.end - item.start)))
        merged = []
        for item in candidates:
            if merged and item.start < merged[-1].end:
                previous = merged[-1]
                merged[-1] = Finding(previous.start, max(previous.end, item.end), previous.kind,
                                     previous.identity if item.end <= previous.end else key(text[previous.start:item.end]))
            else:
                merged.append(item)
        return merged

    def label(self, item):
        identity = (item.kind, item.identity)
        if item.kind == "OSOBA" and identity not in self.labels:
            initials = re.fullmatch(r"((?:\w\. ?)+) (\S+)", item.identity)
            if initials:
                # "J. Novák" reuses the label of the only known "Jan Novák".
                first = initials[1][0]
                matches = {label for (kind, known), label in self.labels.items()
                           if kind == "OSOBA" and not re.match(r"\w\.", known)
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
            for item in reversed(findings):
                text = text[:item.start] + self.label(item) + text[item.end:]
        return text, total

    def cell(self, kind, value):
        """Label a whole table cell whose column header marks it as personal data."""
        text = str(value).strip()
        if not text or LABEL.fullmatch(text):
            return None
        return self.label(Finding(0, len(text), kind, structured_identity(kind, text)))
