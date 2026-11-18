import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from . import numbers
from .czech import (CASE, GIVEN, GIVEN_FORMS, INITIAL_WORD, LABEL, anchored, cls, contextual_findings,
                    identity_family, structured_identity, surface_surname)


def key(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


RULES = {
    "EMAIL": r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "IBAN": r"(?<!\w)CZ(?:[ \t]?\d){22}(?![ \t]?\d)",
    "RC": r"(?<!\d)\d{6}[ \t]*/[ \t]*\d{3,4}(?!\d)",
    # Birth number without a slash ("850314-1237", "850314 1237", "8503141237"). Only a valid number counts (see
    # ``valid_rc``); not a part of a longer number, an account ("…/0800", "19-2000123456"), a decimal or a group.
    "RC_BARE": (r"(?<![\w+/.-])(?:\d{6}(?:[ \t]*-[ \t]*|[ \t]+)\d{3,4}|\d{9,10})"
                r"(?!\d|[ \t]*/|[.,]\d|-\d|[ \t]\d{3})"),
    # A cue ("rodné číslo", "r. č.", "RČ") also takes a number that fails the checksum.
    "RC_CTX": (r"(?<!\w)(?:rodn[ée][ \t]+[cč][ií]slo|r\.[ \t]*č\.|rč)[ \t]*:?[ \t]*"
               r"(\d{6}[ \t]*[-/ ]?[ \t]*\d{3,4})(?![\d/]|-\d)"),
    # A number may be broken by a line end, or written with hyphens or dots ("777-123-456", "777.123.456").
    "TELEFON": (r"(?<![\w+])(?:\(?(?:\+|00)420\)?[ \t]*)?(?:"
                r"[67]\d{2}(?:[ \t]?\n?[ \t]?\d{3}){2}|[2-5]\d{2}(?:[ \t]\n?[ \t]?\d{3}){2}|"
                r"[2-7]\d{2}(?:[-.]\d{3}){2}|[2-7]\d{2}[ \t]?[-.][ \t]?\d{3}[ \t]?[-.][ \t]?\d{3})(?!\d)"),
    # The label may end one line and the number start the next (text layers of PDF).
    "UCET": r"(?:účet|účtu|bankovní\s+spojení|č\.\s*ú\.)\s*:?\s*((?:\d{1,6}-)?\d{2,10}/\d{4})(?!\d)",
    "DIC": r"\bCZ\d{8,10}\b",
    "DOKLAD": r"(?:(?:OP|pas|doklad)\s*(?:č\.\s*)?:\s*)([A-Z0-9]{6,12})\b",
    "ICO": r"(?<!\w)IČO?\s*:?\s*((?:\d[ \t\u00a0]?){7}\d)(?![ \t\u00a0]?\d)",
}
COMPILED = {kind: re.compile(pattern, re.IGNORECASE) for kind, pattern in RULES.items()}
WHITESPACE = re.compile(r"\s")
LETTER_INITIAL = re.compile(r"\w\.")
INITIALS = re.compile(r"((?:\w\. ?)+) (\S+)")
EMAIL_RUN = re.compile(r"[\w.+-]+\Z")
STRIPPED = {"IBAN", "RC", "RC_BARE", "RC_CTX", "TELEFON", "UCET", "NAROZENI"}
KIND_OF = {"RC_BARE": "RC", "RC_CTX": "RC"}
NON_DIGIT = re.compile(r"\D")
# OCR often reads "@" as "©" or "®" (and may add a stray letter after it); "(at)" is a common spelling.
AT_LOOKALIKE = re.compile(r"(?<=[\w.+-])(?:[©®]|\((?:at|AT)\)|\[(?:at|AT)\])[ĎďĚě ]?(?=[\w-])")


def lookalike_emails(text):
    """E-mail matches in ``text`` with "@" lookalikes read as "@". Yields (start, end) in ``text``."""
    if not ("©" in text or "®" in text or "(at)" in text.lower() or "[at]" in text.lower()):
        return
    view, starts, ends, position = [], [], [], 0
    for match in AT_LOOKALIKE.finditer(text):
        chunk = text[position:match.start()]
        view.append(chunk)
        starts += range(position, match.start())
        ends += range(position + 1, match.start() + 1)
        view.append("@")
        starts.append(match.start())
        ends.append(match.end())
        position = match.end()
    if not starts:
        return
    view.append(text[position:])
    starts += range(position, len(text))
    ends += range(position + 1, len(text) + 1)
    for found in email_matches("".join(view)):
        yield starts[found.start()], ends[found.end() - 1], found[0]


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


VALID_MONTHS = {*range(1, 13), *range(21, 33), *range(51, 63), *range(71, 83)}


def valid_rc(digits):
    """Whether nine or ten digits form a birth number (date part, year before 1954 for nine digits, checksum)."""
    if len(digits) not in (9, 10) or not digits.isdigit():
        return False
    month, day = int(digits[2:4]), int(digits[4:6])
    if month not in VALID_MONTHS or not 1 <= day <= 31:
        return False
    if len(digits) == 9:
        return int(digits[:2]) <= 53
    rest = int(digits[:9]) % 11
    return int(digits[9]) == (0 if rest == 10 else rest)


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


RC_CTX_START = re.compile(cls("r"))
CZ_START = re.compile(cls("c") + cls("z"))
PHONE_START = re.compile(r"\(?(?:\+|00)4|[2-7]\d")
# úč, ban, č.
ACCOUNT_START = re.compile(cls("účb") + "[" + cls("ča")[1:-1] + r".]")
ICO_START = re.compile(cls("i") + cls("č"))
# OP, pas, doklad
DOKLAD_START = re.compile(cls("odp") + cls("pao"))
SCANNERS = {
    "EMAIL": email_matches,
    "IBAN": lambda text: anchored(COMPILED["IBAN"], CZ_START, text),
    "RC": rc_matches,
    "RC_CTX": lambda text: anchored(COMPILED["RC_CTX"], RC_CTX_START, text),
    "TELEFON": lambda text: anchored(COMPILED["TELEFON"], PHONE_START, text),
    "UCET": lambda text: anchored(COMPILED["UCET"], ACCOUNT_START, text),
    "DIC": lambda text: anchored(COMPILED["DIC"], CZ_START, text),
    "DOKLAD": lambda text: anchored(COMPILED["DOKLAD"], DOKLAD_START, text),
    "ICO": lambda text: anchored(COMPILED["ICO"], ICO_START, text),
    "RC_BARE": lambda text: COMPILED["RC_BARE"].finditer(text),
}


# Spaces other than U+0020 (no-break, narrow, figure, thin ...) and invisible characters
# (soft hyphen, zero-width, BOM). Word and PDF text layers use them inside names and numbers.
SPACE_LIKE = {code: " " for code in (0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)}
INVISIBLE = {code: None for code in (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)}
NORMALIZE = {**SPACE_LIKE, **INVISIBLE}
UNUSUAL = re.compile("[" + "".join(map(chr, NORMALIZE)) + "]")


def normalize_for_rules(text):
    """Return ``(normalized, origin)``; ``origin[i]`` is the index in ``text`` of normalized char ``i``.

    ``origin`` is None when the text has nothing to normalize (the common case).
    """
    if not UNUSUAL.search(text):
        return text, None
    chars, origin = [], []
    for index, char in enumerate(text):
        replacement = NORMALIZE.get(ord(char), char)
        if replacement is not None:
            chars.append(replacement)
            origin.append(index)
    return "".join(chars), origin


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
        # family key -> [(gender, label)] of every person known by that surname
        self.families = {}
        # given name form (any case) -> canonical given names; canonical -> labels of the persons that have it
        self.given_forms = {}
        self.given_labels = {}
        # given_identities() is needed by every find(); it is rebuilt only after register_given() changed the forms
        self._given_cache = None
        # labels that so far stand for a bare surname only
        self.bare = set()
        # kind -> original strings found. Memory only: never written, logged or put into a message.
        self.surfaces = defaultdict(set)
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

    def find(self, text, contexts=True, ocr=False):
        """Findings in ``text``; unusual spaces and invisible characters do not hide a value.

        The rules run on a normalized copy (no-break and other spaces become a space, soft
        hyphens and zero-width characters are dropped) and the spans are mapped back, so
        "Jan\xa0Novák" or "850314\xad2010" are found and replaced in the original text.
        """
        normalized, origin = normalize_for_rules(text)
        if origin is None:
            return self._find(text, contexts, ocr)
        mapped = []
        for item in self._find(normalized, contexts, ocr):
            start, end = origin[item.start], origin[item.end - 1] + 1
            mapped.append(Finding(start, end, item.kind, item.identity))
        return mapped

    def _find(self, text, contexts=True, ocr=False):
        candidates = [(start, end, kind, key(identity))
                      for start, end, kind, identity in contextual_findings(
                          text, self.surnames, self.families, contexts, self.given_identities())]
        if self.custom:
            for match in self.custom.finditer(text):
                variant = self.variants[match.lastindex - 1]
                candidates.append((*match.span(), "VLASTNI", self.aliases[variant]))
        for kind, scan in SCANNERS.items():
            for match in scan(text):
                group = 1 if match.lastindex else 0
                identity = key(match[group])
                if kind == "RC_BARE" and not valid_rc(NON_DIGIT.sub("", identity)):
                    continue
                if kind in STRIPPED:
                    identity = WHITESPACE.sub("", identity)
                if kind in KIND_OF or kind == "RC":
                    identity = NON_DIGIT.sub("", identity)
                if kind == "TELEFON":
                    identity = NON_DIGIT.sub("", identity)
                    if len(identity) > 9:
                        identity = identity.removeprefix("00").removeprefix("420")
                candidates.append((*match.span(group), KIND_OF.get(kind, kind), identity))
        for start, end, shown in lookalike_emails(text):
            candidates.append((start, end, "EMAIL", key(shown)))
        candidates += self.numeric_candidates(text, candidates, ocr)
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
        for start, end, kind, _ in merged:
            self.remember(kind, text[start:end])
        return [Finding(*item) for item in merged]

    @staticmethod
    def numeric_candidates(text, candidates, ocr):
        """Numbers after a cue word and (in OCR text) long digit groups that the exact rules did not take."""
        found = []
        for start, end, kind in numbers.cue_numbers(text):
            if not any(item[0] <= start and end <= item[1] for item in candidates):
                found.append((start, end, kind, numbers.identity(text[start:end])))
        if ocr:
            # Case numbers and citations stay: they identify a public decision or the case, not a person.
            skip = [match.span(1) for match in CASE.finditer(text)]
            skip += [item[:2] for item in candidates if item[2] == "SPIS"]
            skip += [item[:2] for item in found]
            for start, end in numbers.long_numbers(text, skip):
                if not any(item[0] <= start and end <= item[1] for item in candidates):
                    found.append((start, end, "CISLO", numbers.identity(text[start:end])))
        return found

    def remember(self, kind, surface):
        if "[" in surface and LABEL.search(surface):
            return
        known = self.surfaces[kind]
        if surface in known:
            return
        known.add(surface)
        if kind == "OSOBA":
            surname = surface_surname(surface)
            if surname:
                self.surfaces["PRIJMENI"].add(surname)

    def given_identities(self):
        """Form -> identity ("!canonical|…") of the given names of the persons known by a full name."""
        if self._given_cache is None:
            self._given_cache = self.build_given_identities()
        return self._given_cache

    def build_given_identities(self):
        return {form: "!" + "|".join(sorted(canonicals)) for form, canonicals in self.given_forms.items()}

    def register_given(self, identity, label):
        """Remember the given name of a person known by a full name ("jan novák" -> every form of "jan")."""
        tokens = [token for token in identity.split() if not INITIAL_WORD.fullmatch(token)]
        if len(tokens) < 2:
            return
        first = tokens[0]
        if tokens[-1] in GIVEN and first not in GIVEN:
            first = tokens[-1]
        for canonical in GIVEN.get(first, [first]):
            if label not in self.given_labels.setdefault(canonical, []):
                self.given_labels[canonical].append(label)
            for form in set(GIVEN_FORMS.get(canonical, ())) | {first, canonical}:
                self.given_forms.setdefault(form, set()).add(canonical)
        self._given_cache = None

    def given_label(self, identity):
        """Label of a lone given name: that of the one person known with it, else one label for all of them."""
        labels = []
        for canonical in identity[1:].split("|"):
            labels += [known for known in self.given_labels.get(canonical, ()) if known not in labels]
        if len(labels) == 1:
            return labels[0]
        if ("OSOBA", identity) not in self.labels:
            self.labels["OSOBA", identity] = self.label_for("OSOBA")
        return self.labels["OSOBA", identity]

    def label_for(self, kind):
        self.counts[kind] += 1
        return f"[{kind}-{self.counts[kind]}]"

    @staticmethod
    def compatible(first, second):
        return first is None or second is None or first == second

    def register(self, keys, gender, label):
        for key_, _ in keys:
            entries = self.families.setdefault(key_, [])
            if (gender, label) not in entries:
                entries.append((gender, label))

    def surname_label(self, identity):
        """Label of a bare surname ("~key|gender"): that of the one person with the surname."""
        key_, gender = identity[1:].split("|")
        gender = gender or None
        labels = []
        for known_gender, known in self.families.get(key_, ()):
            if self.compatible(gender, known_gender) and known not in labels:
                labels.append(known)
        if len(labels) == 1:
            return labels[0]
        if labels:
            # Several persons share the surname: one label for all bare uses, over-marking is fine.
            if ("OSOBA", identity) not in self.labels:
                self.labels["OSOBA", identity] = self.label_for("OSOBA")
            return self.labels["OSOBA", identity]
        label = self.label_for("OSOBA")
        self.register(((key_, gender),), gender, label)
        self.bare.add(label)
        return label

    def shared_surname(self, keys, gender):
        """A full name takes the label of the single earlier bare use of its surname."""
        labels = {known for known_gender, known in self.families.get(keys[0][0], ())
                  if self.compatible(gender, known_gender)}
        if len(labels) == 1 and labels <= self.bare:
            return labels.pop()
        return None

    def label(self, item):
        identity = (item.kind, item.identity)
        if item.kind == "OSOBA" and item.identity.startswith("!"):
            return self.given_label(item.identity)
        if item.kind == "OSOBA" and item.identity.startswith("~") and "|" in item.identity:
            return self.surname_label(item.identity)
        person = item.kind == "OSOBA" and identity not in self.labels
        keys = ()
        if person:
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
        if person:
            keys, gender = identity_family(item.identity)
            if keys and identity not in self.labels:
                shared = self.shared_surname(keys, gender)
                if shared:
                    self.labels[identity] = shared
                    self.bare.discard(shared)
        if identity not in self.labels:
            self.labels[identity] = self.label_for(item.kind)
        if keys:
            self.register(keys, gender, self.labels[identity])
        if person and not item.identity.startswith(("~", "!")):
            self.register_given(item.identity, self.labels[identity])
        return self.labels[identity]

    def collect(self, text, ocr=False):
        """First phase of a two-phase run over a whole case (all its texts).

        Registers the persons and other labels found in ``text`` and returns nothing.
        Call it on every text of the case, then ``replace`` each text: a surname that
        is introduced only in a later document is then replaced in an earlier one too.
        """
        self.replace(text, ocr)

    def replace(self, text, ocr=False):
        total = 0
        # A replacement can expose a new match next to a label; repeat until stable.
        for turn in range(3):
            # Persons named by a context ("pan X") are found in the first pass; later passes only repeat the scan.
            findings = self.find(text, turn == 0, ocr)
            if not findings:
                break
            total += len(findings)
            # Labels are numbered from the end of the text, as in the Office formats.
            # A bare surname is labelled last, once every full name of the text is known.
            labels = [None] * len(findings)
            for bare in (False, True):
                for index in reversed(range(len(findings))):
                    item = findings[index]
                    if (item.kind == "OSOBA" and item.identity.startswith(("~", "!"))) == bare:
                        labels[index] = self.label(item)
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
        self.remember(kind, text)
        return self.label(Finding(0, len(text), kind, structured_identity(kind, text)))
