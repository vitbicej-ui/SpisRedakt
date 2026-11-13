"""Contextual rules for Czech documents, with no network or model dependency."""
import re
from datetime import date

SPACE = r"[ \t\n]+"
CAP = r"[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+"
NAME = re.compile(rf"\b(?=(({CAP}){SPACE}({CAP})(?![\w-])))")
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
):
    forms = row.casefold().split()
    for form in forms:
        GIVEN.setdefault(form, []).append(forms[0])

FEMININE = set("anna jana eva petra marie lucie hana alena kateřina tereza veronika lenka barbora ivana jitka martina markéta eliška kristýna".split())


def name_identity(first, last):
    first, last = first.casefold(), last.casefold()
    forms = GIVEN.get(first, [first])
    feminine = last.endswith(("ová", "ové", "ovou", "ovými", "á", "ou")) or all(
        form in FEMININE for form in forms
    )
    canonical = next((form for form in forms if (form in FEMININE) == feminine), forms[0])
    adjective = re.fullmatch(r"(.+(?:sk|ck|n))(?:ý|á|ého|ému|ém|ým|é|ou)", last)
    female = re.fullmatch(r"(.+ov)(?:á|é|ou|ými)", last)
    if female:
        last = female[1] + "á"
    elif adjective:
        last = adjective[1] + ("á" if feminine else "ý")
    elif not feminine:
        last = re.sub(r"(?<=[bcčdďfghjklmnňprřsštťvzž])(?:ovi|em|a|u|e|i)$", "", last)
    return f"{canonical} {last}"


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
