"""Contextual rules for Czech documents, with no network or model dependency."""
import re
from datetime import date

SPACE = r"[ \t\n]+"
UPPER = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
CAP = rf"[{UPPER}][a-záčďéěíňóřšťúůýž]+"
NAME_WORD = rf"(?:{CAP}|[{UPPER}]{{2,}})"
NAME = re.compile(rf"\b(?=(({NAME_WORD}){SPACE}({NAME_WORD}(?:-{NAME_WORD})*)(?![\w-])))")
TITLE = re.compile(r"(?:JUDr|MUDr|PhDr|Mgr|Ing|Bc|Dr)\.[ \t\n]*$", re.I)
PERSON_CONTEXT = re.compile(r"\b(?:dědic\w*|účastník\w*|klient\w*|notář\w*|pan(?:a|em|u|í|ovi)?|svědk\w*|svědek|pozůstalost\w* po|zastoupen\w*|podepsal[ay]?)\s*:?\s+$", re.I)
PERSON_AFTER = re.compile(r"\s*,?\s*(?:nar\.|narozen\w*|bytem\b|zemřel\w*\b|podepsal\w*\b)", re.I)

# Explicit paradigms cover ambiguous given names whose cases cannot be recovered
# reliably by cutting suffixes (e.g. Petr / Petra / Petrem).
GIVEN = {}
for row in (
    "Jan Jana Janovi Janu Jane Janem", "Petr Petra Petrovi Petru Petře Petrem",
    "Pavel Pavla Pavlovi Pavlu Pavle Pavlem", "Josef Josefa Josefovi Josefu Josefe Josefem",
    "Jiří Jiřího Jiřímu Jiřím Jiřímovi", "František Františka Františkovi Františku Františkem",
    "Martin Martina Martinovi Martinu Martine Martinem", "Tomáš Tomáše Tomášovi Tomáši Tomášem",
    "Václav Václava Václavovi Václavu Václave Václavem", "Karel Karla Karlovi Karlu Karle Karlem",
    "Michal Michala Michalovi Michalu Michale Michalem", "Jakub Jakuba Jakubovi Jakubu Jakube Jakubem",
    "David Davida Davidovi Davidu Davide Davidem", "Ondřej Ondřeje Ondřejovi Ondřeji Ondřejem",
    "Lukáš Lukáše Lukášovi Lukáši Lukášem", "Svatomír Svatomíra Svatomírovi Svatomíru Svatomírem",
    "Anna Anny Anně Annu Anno Annou", "Jana Jany Janě Janu Jano Janou",
    "Eva Evy Evě Evu Evo Evou", "Petra Petry Petře Petru Petro Petrou",
    "Marie Marii Marie Marií", "Lucie Lucii Lucie Lucií", "Hana Hany Haně Hanu Hano Hanou",
    "Alena Aleny Aleně Alenu Aleno Alenou", "Kateřina Kateřiny Kateřině Kateřinu Kateřino Kateřinou",
    "Tereza Terezy Tereze Terezu Terezo Terezou", "Veronika Veroniky Veronice Veroniku Veroniko Veronikou",
    "Lenka Lenky Lence Lenku Lenko Lenkou", "Barbora Barbory Barboře Barboru Barboro Barborou",
    "Marek Marka Markovi Marku Markem", "Adam Adama Adamovi Adamu Adame Adamem",
    "Milan Milana Milanovi Milanu Milane Milanem", "Miroslav Miroslava Miroslavovi Miroslavu Miroslavem",
    "Zdeněk Zdeňka Zdeňkovi Zdeňku Zdeňkem", "Vojtěch Vojtěcha Vojtěchovi Vojtěchu Vojtěchem",
    "Aleš Aleše Alešovi Aleši Alešem", "Jaroslav Jaroslava Jaroslavovi Jaroslavu Jaroslavem",
    "Daniel Daniela Danielovi Danielu Danielem", "Filip Filipa Filipovi Filipu Filipem",
    "Roman Romana Romanovi Romanu Romanem", "Radek Radka Radkovi Radku Radkem",
    "Ivana Ivany Ivaně Ivanu Ivano Ivanou", "Jitka Jitky Jitce Jitku Jitko Jitkou",
    "Martina Martiny Martině Martinu Martino Martinou", "Markéta Markéty Markétě Markétu Markétou",
    "Eliška Elišky Elišce Elišku Eliško Eliškou", "Kristýna Kristýny Kristýně Kristýnu Kristýno Kristýnou",
    "Matěj Matěje Matějovi Matěji Matějem", "Štěpán Štěpána Štěpánovi Štěpánu Štěpánem",
    "Václava Václavy Václavě Václavu Václavo Václavou", "Jindřich Jindřicha Jindřichovi Jindřichu Jindřichem",
    "Šárka Šárky Šárce Šárku Šárko Šárkou", "Zuzana Zuzany Zuzaně Zuzanu Zuzano Zuzanou",
    "Monika Moniky Monice Moniku Moniko Monikou", "Simona Simony Simoně Simonu Simono Simonou",
    "Michaela Michaely Michaele Michaelu Michaelo Michaelou",
    "Dominik Dominika Dominikovi Dominiku Dominikem", "Dominika Dominiky Dominice Dominiku Dominikou",
    "Mikuláš Mikuláše Mikulášovi Mikuláši Mikulášem", "Zbyněk Zbyňka Zbyňkovi Zbyňku Zbyňkem",
    "Tobiáš Tobiáše Tobiášovi Tobiáši Tobiášem", "Patrik Patrika Patrikovi Patriku Patrikem",
):
    forms = row.casefold().split()
    for form in forms:
        GIVEN.setdefault(form, []).append(forms[0])

FEMININE = set("anna jana eva petra marie lucie hana alena kateřina tereza veronika lenka barbora ivana jitka martina markéta eliška kristýna".split())
FEMININE.update("václava šárka zuzana monika simona michaela dominika".split())


def name_identity(first, last):
    first, last = first.casefold(), last.casefold()
    forms = GIVEN.get(first, [first])
    feminine = last.endswith(("ová", "ové", "ovou", "ovými", "á", "ou")) or all(
        form in FEMININE for form in forms
    )
    canonical = next((form for form in forms if (form in FEMININE) == feminine), forms[0])
    surname = "-".join(surname_stem(part, feminine) for part in last.split("-"))
    return f"{canonical} {surname}"


def surname_stem(last, feminine):
    adjective = re.fullmatch(r"(.+?)(?:ý|á|ého|ému|ém|ým|é|ou)", last)
    female = re.fullmatch(r"(.+ov)(?:á|é|ou|ými)", last)
    if female:
        last = female[1] + "á"
    elif adjective:
        last = adjective[1] + ("á" if feminine else "ý")
    elif not feminine:
        # Keep stems stable across the common mobile-e and mobile-ě patterns.
        last = re.sub(r"(?<=[bcčdďfghjklmnňprřsštťvzž])(?:ovi|em|a|u|e|i)$", "", last)
        last = re.sub(r"(?<=[bcčdďfghjklmnňprřsštťvzž])[eě](?=[kl]$)", "", last)
    return last


CITY = rf"(?:{CAP})(?:{SPACE}(?:(?:nad|pod|u|na|ve|v|a){SPACE})?{CAP}){{0,3}}"
STREET = rf"(?:{CAP}|[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]\.|[a-záčďéěíňóřšťúůýž]{{2,}})(?:{SPACE}(?:{CAP}|[a-záčďéěíňóřšťúůýž]{{1,}})){{0,3}}"
NUMBER = r"\d{1,5}(?:/\d{1,4})?[a-z]?(?![\w/.]\d|\w)"
POSTCODE = r"\d{3}[ \t]?\d{2}"
ADDRESS_CUE = re.compile(r"\b(?:bytem|bydlí(?:cí)?|sídlem|trval(?:ý pobyt|e bytem)|(?:doručovací )?adresa)\b[ \t\n]*:?[ \t\n]*", re.I)
ADDRESS = re.compile(rf"(?:{STREET}{SPACE}{NUMBER}(?:[ \t\n]*,[ \t\n]*(?:{POSTCODE}{SPACE})?{CITY})?|{CITY}[ \t\n]*,[ \t\n]*{STREET}{SPACE}{NUMBER}|{CITY}{SPACE}{NUMBER})(?=$|[\s,.;)])")
FREE_ADDRESS = re.compile(rf"(?<!\w)(?:{CITY}[ \t\n]*,[ \t\n]*(?:{CAP}|[A-Z]\.){SPACE}(?:{CAP}{SPACE})?{NUMBER}|{CITY}{SPACE}{NUMBER}[ \t\n]*,[ \t\n]*(?:{POSTCODE}{SPACE})?{CITY})(?=$|[\s,.;)])")
BIRTH = re.compile(r"\b(?:nar(?:ozen(?:ý|á|ého|é|ému|ou)?)?\.?|datum[ \t\n]+narození|dat\.[ \t\n]*nar\.)[ \t\n]*:?[ \t\n]*(?:dne[ \t\n]+)?(\d{1,2})\.[ \t\n]*(\d{1,2})\.[ \t\n]*(\d{4})(?!\d)", re.I)
CASE = re.compile(r"(?:sp\.[ \t\n]*zn\.|č\.[ \t\n]*j\.|spisová značka|číslo jednací)[ \t\n]*:?[ \t\n]*((?:\d{1,4}[ \t\n]+)?[A-ZČŘŠŽ][A-Za-zČŘŠŽčřšž]{0,7}[ \t\n]*\d{1,6}/\d{2,4}(?:-\d+)?)")
MONTHS = "ledna února března dubna května června července srpna září října listopadu prosince".split()
BIRTH_TEXT = re.compile(BIRTH.pattern.replace(r"(\d{1,2})\.[ \t\n]*(\d{4})", "(" + "|".join(MONTHS) + rf"){SPACE}(\d{{4}})"), re.I)


def address_identity(value):
    parts = re.split(r"\s*,\s*", value.casefold())
    parts = [re.sub(r"^\d{3}[ \t]?\d{2}\s+", "", part) for part in parts]
    return ", ".join(sorted(" ".join(part.split()) for part in parts))


def contextual_findings(text):
    for match in COMPANY.finditer(text):
        yield *match.span(1), "ORGANIZACE", match[1]
    for match in SLOVAK_FIELD.finditer(text):
        yield *match.span(1), "OSOBNI_UDAJ", match[1]
    for match in CASE.finditer(text):
        yield *match.span(1), "SPIS", match[1]
    for pattern in (BIRTH, BIRTH_TEXT):
        for match in pattern.finditer(text):
            day, month, year = match.groups()
            month = int(month) if month.isdecimal() else MONTHS.index(month.casefold()) + 1
            try:
                birth = date(int(year), month, int(day))
            except ValueError:
                continue
            yield match.start(1), match.end(3), "NAROZENI", birth.isoformat()
    for cue in ADDRESS_CUE.finditer(text):
        match = ADDRESS.match(text, cue.end())
        if match:
            yield *match.span(), "ADRESA", address_identity(match[0])
    for match in FREE_ADDRESS.finditer(text):
        yield *match.span(), "ADRESA", address_identity(match[0])
    end = 0
    for match in NAME.finditer(text):
        if match.start() < end:
            continue
        before = text[max(0, match.start() - 100):match.start()]
        if (match[2].casefold() in GIVEN or TITLE.search(before)
                or PERSON_CONTEXT.search(before) or PERSON_AFTER.match(text, match.end(1))):
            end = match.end(1)
            yield *match.span(1), "OSOBA", name_identity(match[2], match[3])


COMPANY = re.compile(
    r"(?<!\w)([^\W\d_][\w&.'’ -]{0,100}?[ \t]+(?:a\.[ \t]*s\.|s\.[ \t]*r\.[ \t]*o\.|spol\.[ \t]*s[ \t]*r\.[ \t]*o\.))(?=[ \t]*[,;)]|[ \t]*$)",
    re.I | re.M,
)
SLOVAK_FIELD = re.compile(
    r"(?:^|[ \t])(?:meno|(?:rodné[ \t]+)?priezvisko|dátum narodenia|miesto narodenia|"
    r"štát narodenia|štátne občianstvo|číslo dokladu|rodné číslo|pohlavie|číslo žiadosti|kód RT)"
    r"[ \t]*:[ \t]*([^\n;]+?)(?=[ \t]{2,}\S|[ \t]*$|;)", re.I | re.M,
)


# Column headers whose cells hold one structured value each. Headers must match
# completely, so "Datum podpisu" or "Poznámka k telefonu" stay untouched.
COLUMN_HEADERS = [(kind, re.compile(pattern, re.I)) for kind, pattern in (
    ("NAROZENI", r"(?:datum|dat\.)[ ]*(?:narození|nar\.?)|narozen[aáý]?|nar\.|datum a místo narození"),
    ("RC", r"rodné[ ]+číslo|r\.?[ ]*č\.?"),
    ("ICO", r"ičo?|identifikační[ ]+číslo(?:[ ]+osoby)?"),
    ("DIC", r"dič|daňové[ ]+identifikační[ ]+číslo"),
    ("DOKLAD", r"(?:číslo|č\.)[ ]*(?:op|pasu|dokladu(?:[ ]+totožnosti)?|občanského[ ]+průkazu)|op|doklad(?:[ ]+totožnosti)?"),
    ("TELEFON", r"(?:mobilní[ ]+)?telefon(?:ní[ ]+číslo)?|tel\.?(?:[ ]*č\.)?|mobil"),
    ("EMAIL", r"e-?mail(?:ová[ ]+adresa)?"),
    ("UCET", r"(?:číslo[ ]+)?(?:bankovního[ ]+)?(?:účet|účtu)|bankovní[ ]+spojení|iban"),
    ("ADRESA", r"(?:adresa(?:[ ]+(?:trvalého[ ]+)?(?:pobytu|bydliště))?|bydliště|trvalý[ ]+pobyt|bytem|doručovací[ ]+adresa)"),
    ("OSOBA", r"jméno(?:[ ]+a[ ]+příjmení)?|příjmení(?:[ ]+a[ ]+jméno)?|celé[ ]+jméno|(?:jméno[ ]+)?(?:účastník|dědic|svědek|zákonný[ ]+zástupce)"),
)]
LABEL = re.compile(r"\[[A-Z_]+-\d+\]")


def column_kind(header):
    """Return the finding kind for a table column header, or None."""
    text = " ".join(str(header).replace(" ", " ").split()).strip(" :*").casefold()
    for kind, pattern in COLUMN_HEADERS:
        if pattern.fullmatch(text):
            return kind
    return None


def header_row(headers, columns, texts):
    """Decide whether a table row with header-like cells starts new columns.

    Once columns are known, a data value such as "OP" must not end redaction of
    its row, so only rows made solely of headers (and blanks) qualify.
    """
    if not headers:
        return False
    if not columns:
        return True
    return sum(1 for text in texts if str(text).strip()) == len(headers)


def structured_identity(kind, value):
    """Normalize a standalone cell value so equal values share one label."""
    text = " ".join(str(value).split())
    if kind == "NAROZENI":
        match = re.fullmatch(r"(\d{1,2})\.[ ]*(\d{1,2})\.[ ]*(\d{4})", text)
        if match:
            try:
                return date(int(match[3]), int(match[2]), int(match[1])).isoformat()
            except ValueError:
                pass
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
    if kind == "OSOBA":
        words = text.split()
        if len(words) == 2 and all(re.fullmatch(rf"{NAME_WORD}(?:-{NAME_WORD})*", word) for word in words):
            return name_identity(*words)
    if kind in {"RC", "TELEFON", "UCET", "ICO", "DIC", "IBAN"}:
        text = re.sub(r"\s", "", text)
        if kind == "TELEFON":
            text = text.removeprefix("+420")
    return text.casefold()
