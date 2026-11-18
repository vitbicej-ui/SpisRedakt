"""Numeric data that the exact rules miss: numbers misread by OCR or written in an unusual way.

Two conservative rules run after the exact ones (``rules.RULES``, ``czech``):

1. A number after a cue word ("tel.", "účet", "RČ", "OP", "IČO", "nar." ...) is a personal value even when
   it has a wrong number of digits, a letter instead of a digit ("l" for 1, "O" for 0, "S" for 5, "B" for 8)
   or an odd separator. It applies to every text.
2. In text read by OCR, a long group of digits (at least nine) is replaced by the label ``CISLO`` unless it
   is an amount, a date, a case number or a number in a citation of a law.

Both only return spans; ``Rules.find`` leaves out a span that an exact rule already covers.
"""
import re

LOOKALIKE = "OolISB"
DIGITISH = "0-9" + LOOKALIKE
# One group of digits (at least one real digit; OCR letters may stand in for the others).
GROUP = rf"(?=[{DIGITISH}]*\d)[{DIGITISH}]+"
SEPARATOR = r"[ \t ]{1,2}|[ \t ]?[/\-+().][ \t ]?"
RUN = re.compile(rf"[+(]{{0,2}}{GROUP}(?:(?:{SEPARATOR}){GROUP})*")
START = re.compile(rf"(?<![A-Za-z0-9])[+(]{{0,2}}(?=[{DIGITISH}]*\d)[{DIGITISH}]")
# Long groups of OCR text: only single blanks, slashes and hyphens join the groups (dots would join dates).
LONG_SEPARATOR = r"[ \t ]|[ \t ]?[/\-][ \t ]?"
LONG_RUN = re.compile(rf"(?<![A-Za-z0-9])\+?{GROUP}(?:(?:{LONG_SEPARATOR}){GROUP})*(?![A-Za-z0-9])")
LONG_MIN_DIGITS = 9
CUE_MIN_DIGITS = 6
GAP_MAX = 25
DIGITS = re.compile(r"\d")
DIGITISH_CHARS = re.compile(rf"[{DIGITISH}]")

CUES = {
    "TELEFON": r"tel(?:efon\w*)?|mob(?:il\w*)?|gsm|fax",
    "UCET": r"[uú][cč](?:et|t\w*)|č\.[ \t]*ú|bankovní[ \t\n]+spojení|iban",
    "RC": r"r\.[ \t]*č|rč|rodn\w+[ \t\n]+číslo",
    "DOKLAD": r"op|pas\w*|doklad\w*",
    "ICO": r"ičo?",
    "DIC": r"dič",
    "NAROZENI": r"nar|datum[ \t\n]+narození",
}
CUE = re.compile(
    r"(?<!\w)(?:" + "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in CUES.items()) + r")(?!\w)\.?",
    re.IGNORECASE)
# A document number may mix letters and digits ("AB12345B").
TOKEN = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,12}(?![A-Za-z0-9])")
# A word of four letters and a dot, "!" or "?" or a blank line between the cue and the number ends the sentence.
SENTENCE_END = re.compile(r"[!?]|\n[ \t]*\n|[^\W\d_]{4,}\.")
CURRENCY = re.compile(r"(?:[,.]\d{1,2})?[ \t ]*(?:kč|kc|czk|eur|usd|€|\$|korun\w*|,[-–—])", re.IGNORECASE)
LAW_BEFORE = re.compile(
    r"(?:§+|čl\.|odst\.|zák(?:on\w*|\.)[ \t]*č\.|vyhl(?:ášk\w*|\.)(?:[ \t]*č\.)?|sb\.|nařízení[ \t]+(?:vlády[ \t]+)?č\.)"
    r"[ \t ]*\Z", re.IGNORECASE)
CASE_NUMBER = re.compile(r"\d{1,6}[ \t]*/[ \t]*(?:19|20)\d{2}(?:-\d+)*")
DATE_IN_RUN = re.compile(r"(?<!\d)(?:(?:19|20)\d{2}-\d{2}-\d{2}|\d{1,2}[/\-]\d{1,2}[/\-](?:19|20)\d{2})(?!\d)")


def real_digits(text):
    return len(DIGITS.findall(text))


def identity(text):
    """Value shared by all spellings of one number: digits and letters without separators."""
    return re.sub(r"[^0-9A-Za-z]", "", text).casefold()


def cue_numbers(text):
    """Yield ``(start, end, kind)`` of numbers that follow a cue word."""
    for cue in CUE.finditer(text):
        kind = cue.lastgroup
        position = cue.end()
        limit = min(len(text), position + GAP_MAX)
        positions = {found.start() for found in START.finditer(text, position, limit)}
        if kind == "DOKLAD":
            positions |= {found.start() for found in TOKEN.finditer(text, position, limit)}
        for start in sorted(positions):
            if SENTENCE_END.search(text, position, start):
                break
            run = RUN.match(text, start)
            if run and real_digits(run[0]) >= CUE_MIN_DIGITS:
                if not CURRENCY.match(text, run.end()):
                    yield start, run.end(), kind
                break
            token = TOKEN.match(text, start) if kind == "DOKLAD" else None
            if token:
                # "AB12345B": letters and digits mixed
                yield start, token.end(), kind
                break
            # Only the first group after the cue counts: a later one belongs to other text.
            break


def long_numbers(text, skip):
    """Yield ``(start, end)`` of long digit groups in OCR text.

    ``skip`` is a list of ``(start, end)`` spans of case numbers that stay.
    """
    for run in LONG_RUN.finditer(text):
        start, end = run.span()
        value = run[0]
        # OCR letters inside a group (it always holds a real digit, so a word never counts) are digits here.
        if len(DIGITISH_CHARS.findall(value)) < LONG_MIN_DIGITS:
            continue
        if CURRENCY.match(text, end):
            continue
        if LAW_BEFORE.search(text[max(0, start - 30):start]):
            continue
        if CASE_NUMBER.fullmatch(value) or DATE_IN_RUN.search(value):
            continue
        if any(start < high and low < end for low, high in skip):
            continue
        yield start, end
