import re
import unicodedata
from collections import Counter
from dataclasses import dataclass


def key(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


RULES = {
    "EMAIL": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "IBAN": r"(?<!\w)CZ(?:[ \t]?\d){22}(?![ \t]?\d)",
    "RC": r"(?<!\d)\d{6}[ \t]*/[ \t]*\d{3,4}(?!\d)",
    "TELEFON": r"(?<![\w+])(?:\+420[ \t]*)?(?:[67]\d{2}(?:[ \t]?\d{3}){2}|[2-5]\d{2}(?:[ \t]\d{3}){2})(?!\d)",
    "UCET": r"(?<![\d/])(?:\d{1,6}-)?\d{2,10}/\d{4}(?!\d)",
    "NAROZENI": r"(?<=[: ])\d{1,2}\.[ \t]*\d{1,2}\.[ \t]*\d{4}\b",
    "DIC": r"\bCZ\d{8,10}\b",
    "DOKLAD": r"(?:(?:OP|pas|doklad)[ \t]*(?:č\.[ \t]*)?:[ \t]*)([A-Z0-9]{6,12})\b",
    "ICO": r"(?:IČO?[ \t]*:[ \t]*)(\d{8})\b",
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
        candidates = []
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
        if identity not in self.labels:
            self.counts[item.kind] += 1
            self.labels[identity] = f"[{item.kind}-{self.counts[item.kind]}]"
        return self.labels[identity]

    def replace(self, text):
        findings = self.find(text)
        for item in reversed(findings):
            text = text[:item.start] + self.label(item) + text[item.end:]
        return text, len(findings)
