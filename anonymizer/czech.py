"""Contextual rules for Czech and Slovak documents, with no network or model dependency."""
import re
import unicodedata
from datetime import date

from .names import FORMS, feminine_surname, given_lemma, is_given, surname_root

SPACE = r"[ \t\n ]+"
UPPER = "A-ZÁÄČĎÉĚÍĽĹŇÓÔŔŘŠŤÚŮÝŽ"
LOWER = "a-záäčďéěíľĺňóôŕřšťúůýž"
CAP = rf"[{UPPER}][{LOWER}]+"
WORD = re.compile(rf"[{UPPER}{LOWER}]+(?:-[{UPPER}{LOWER}]+)*")
TITLE = re.compile(r"(?:JUDr|MUDr|MVDr|PhDr|RNDr|PaedDr|ThDr|Mgr|MgA|Ing|Bc|Dr|doc|prof)\.[ \t ]*\n?[ \t ]*\Z", re.I)
PERSON_CONTEXT = re.compile(
    r"\b(?:dědic\w*|účastník\w*|účastnic\w*|klient\w*|notář\w*|pan(?:a|em|u|í|ovi)?|svědk\w*|svědek|svedk\w*|"
    r"pozůstalost\w* po|zastoupen\w*|zastúpen\w*|podepsal[ay]?|žalobc\w*|žalovan\w*|povinn\w+|oprávněn\w+|"
    r"navrhovatel\w*|odpůrc\w*|obviněn\w+|obžalovan\w+|odsouzen\w+|poškozen\w+|jednatel\w*|opatrovník\w*|"
    r"zůstavitel\w*|manžel\w*|nezletil\w+|soudc\w*|soudkyn\w*|sudc\w*|exekutor\w*|advokát\w*|zmocněn\w+|"
    r"dlužník\w*|dlužnic\w*|věřitel\w*|insolvenční\w* správc\w*|znalc\w*|znalec|tlumočník\w*)"
    r"[ \t ]*:?(?:[ \t ]+|[ \t ]*\n[ \t ]*)\Z", re.I)       # same line or one wrapped line
PERSON_AFTER = re.compile(r"\s*,?\s*(?:nar\.|narozen\w*|naroden\w*|bytem\b|zemřel\w*\b|podepsal\w*\b|r\.[ \t]*č\.|rodné číslo)", re.I)

# Capitalised words that open sentences or name institutions, never a person.
STOP = set("""
soud soudu soudem okresní okresního okresním krajský krajského krajským městský městského vrchní
nejvyšší ústavní obvodní česká české českou slovenská slovenské republika republiky republice
republikou podle dne proti proto protože když pokud jestliže jelikož neboť tento tato toto tyto
ten ta to ti tím této tomu toho ke ku při pro před přes po od do na ve za se ze je jsou byl byla
bylo usnesení rozsudek rozsudku rozhodnutí odůvodnění poučení výrok žalobce žalobkyně žalobci
žalovaný žalovaná žalovaného žalované žalovanému povinný povinná povinného povinné oprávněný
oprávněná oprávněného oprávněné navrhovatel navrhovatelka odpůrce účastník účastníci svědek
svědkyně pan paní slečna společnost firma zákon zákona zákoník sbírka sbírky ministerstvo
úřad úřadu policie státní zastupitelství exekutor exekutorský notář notářka advokát advokátka
dále proto tedy však také rovněž následně poté dnes zde tam kde jak aby ani nebo jeho její jejich
náklady řízení návrh návrhu žaloba žaloby odvolání exekuce příloha přílohy strana list číslo
spisová značka datum místo jméno příjmení adresa telefon vážený vážená vážení věc
okresný okresného krajského mestský najvyšší ústavný súd súdu súdom uznesenie rozsudok
rozhodnutie odôvodnenie poučenie žalobca žalovaný navrhovateľ odporca svedok spoločnosť
register trestov výpis odpis generálna prokuratúra ministerstvo vnútra polícia
new times table list default heading normal light medium dark accent grid title
kč sb eur czk usd tel fax bic iban psč vám vás váš vaše vašeho vaší vašemu vaším vy vámi
ospod čssz ossz pčr vzp čnb čr sr dph
""".split())
# First words of two-word place names; only ever tested on the first word of a pair.
PLACE_FIRST = re.compile(
    r"(?:(?:nov|star|velk|veľk|mal|horn|doln|česk|slovensk|moravsk|slezsk|karlov|mariánsk|františkov|kutn|"
    r"uhersk|valašsk|bansk|spišsk|svat|jindřichův|králův|červen|bíl|vysok|dlouh)(?:ý|á|é|ého|ém|ou|ých|y|ej|ú|om)?"
    r"|hradec|hradce|hradci|ústí|frýdek|brandýs|kostelec|týn|dvůr|žďár|lázně|lázních|praha|prahy|praze|brno|"
    r"brna|brně|ostrava|ostravy|ostravě|plzeň|plzně|plzni|bratislava|košice|sv)$", re.I)


# Institution and geography words in any case form; they only block the weak guesses below.
WEAK = re.compile(
    r"(?:okresn|krajsk|městsk|mestsk|vrchn|nejvyšš|najvyšš|ústavn|obvodn|česk|slovensk|evropsk|európsk|státn|štátn|"
    r"finančn|katastráln|exekutorsk|notářsk|policejn|celn|probačn|vězeňsk|ministerstv|polici|úřad|soud|súd|"
    r"republik|sbír|zbier|listin|ústav|úmluv|uni[eií]|praha|prah[yuo]|praze|brn[oauě]|ostrav|plz[eň]|olomouc|"
    r"liber[ec]|hrad[ce]|pardubic|zlín|jihlav|budějovic|karlov|opav|bratislav|košic|nitr|žilin|trnav|trenčín|"
    r"prešov|kolín|kladn|most|teplic|děčín|chomutov|přerov|prostějov|tábor|znojm|třebíč|havířov|karvin|"
    r"morav|čech|slezsk|slovensk|česk|vysočin)\w*$", re.I)
PLACE_BEFORE = set("v ve do z ze nad pod města mesta obce obci městě okres okrese okresu kraj kraje kraji ulici ulice "
                   "náměstí soud soudu soudem súd súdu úřad úřadu úřadem pobočka pracoviště".split())


def fold(text):
    """Diacritics-free lower case of the same length, so offsets stay aligned."""
    result = []
    for char in text:
        low = unicodedata.normalize("NFD", char)[0].lower()
        result.append(low if len(low) == 1 else char)
    return "".join(result)


def name_identity(first, last):
    feminine = feminine_surname(last.casefold()) or (
        first.casefold() in FORMS and all(gender for _, gender in FORMS[first.casefold()]))
    surname = "-".join(surname_key(part) for part in last.split("-"))
    return f"{given_lemma(first, feminine)} {surname}"


def surname_key(last):
    return re.sub(r"(?:ová|ý)$", "", surname_root(last))


def capitalised(word):
    return bool(re.fullmatch(rf"(?:{CAP}|[{UPPER}]{{2,}})(?:-(?:{CAP}|[{UPPER}]{{2,}}))*", word))


def person_findings(text, known_surname=None, generic=True):
    """Yield (start, end, given or None, surname) for personal names."""
    words = [match for match in WORD.finditer(text) if capitalised(match[0])]
    runs, run = [], []
    for match in words:
        if run and re.fullmatch(r"[ \t ]+|[ \t ]*\n[ \t ]*", text[run[-1].end():match.start()]):
            run.append(match)
        else:
            run = [match]
            runs.append(run)
    for run in runs:
        index = 0
        while index < len(run):
            found = _person_at(text, run, index, known_surname, generic)
            if found is None:
                index += 1
                continue
            last, given, surname = found
            yield run[index].start(), run[last].end(), given, surname
            index = last + 1


def _usable(match):
    return match[0].casefold() not in STOP and fold(match[0]) not in LABEL_WORDS


def _same_line(text, left, right):
    return "\n" not in text[left.end():right.start()]


def _person_at(text, run, index, known_surname, generic=True):
    """Return (last index, given name or None, surname) when a name starts here."""
    word = run[index]
    if not _usable(word):
        return None
    following = []
    for item in run[index + 1:index + 4]:
        if not _usable(item):
            break
        following.append(item)
    if is_given(word[0]) and following:
        skip = 1 if len(following) > 1 and is_given(following[0][0]) else 0
        last = skip
        # a second surname on the same line belongs to the same person
        if (len(following) > last + 1 and _same_line(text, following[last], following[last + 1])
                and not is_given(following[last + 1][0])):
            last += 1
        return index + 1 + last, word[0], following[skip][0]
    if len(following) == 1 and is_given(following[0][0]):
        # surname first, as written in registers and forms
        return index + 1, following[0][0], word[0]
    if len(following) > 1 and is_given(following[0][0]):
        return None          # the given name that follows anchors the person
    if following and known_surname and known_surname(following[0][0]) and _same_line(text, word, following[0]):
        return index + 1, word[0], following[0][0]      # unlisted given name before a surname already met
    before = text[max(0, word.start() - 100):word.start()]
    tail = following[0] if following else word
    if TITLE.search(before) or PERSON_CONTEXT.search(before) or PERSON_AFTER.match(text, tail.end()):
        if following:
            return index + 1, word[0], following[0][0]
        return index, None, word[0]
    if generic and following and _same_line(text, word, following[0]):
        pair = (word[0], following[0][0])
        if (not any(item.isupper() for item in pair) and not PLACE_FIRST.match(pair[0])
                and not WEAK.match(pair[0]) and not WEAK.match(pair[1])):
            more = len(following) > 1 and _same_line(text, following[0], following[1]) and not following[1][0].isupper()
            return index + (2 if more else 1), word[0], following[0][0]
    if known_surname and known_surname(word[0]):
        return index, None, word[0]
    if generic and len(word[0]) > 2 and not word[0].isupper() and not WEAK.match(word[0]):
        # a lone capitalised word in the middle of a sentence is a proper name
        lead = re.search(r"(\S+)[ \t ]+$", text[max(0, word.start() - 40):word.start()])
        if (lead and re.fullmatch(rf"[{LOWER}]+,?|\d+,?", lead[1]) and lead[1].rstrip(",") not in PLACE_BEFORE
                and not (len(word[0]) < 5 and text[word.end():word.end() + 1] == ".")):
            return index, None, word[0]
    return None


INITIAL = re.compile(rf"(?<![\w.])((?:[{UPPER}]\.[  ]?){{1,2}})({CAP}(?:-{CAP})?)(?![\w])")
INITIAL_AFTER = re.compile(rf"(?<![\w.])({CAP})[  ][{UPPER}]\.(?=[\s,;)]|$)")


def initial_findings(text):
    for match in INITIAL.finditer(text):
        line_start = not text[:match.start()].rsplit("\n", 1)[-1].strip()
        roman = set(match[1].replace(".", "").replace(" ", "").replace(" ", "")) <= set("IVX")
        if match[2].casefold() in STOP or WEAK.match(match[2]) or (roman and line_start) or roman and re.search(
                r"[.;:]\s*$", text[:match.start()]):
            continue
        yield match.start(), match.end(), match[2]
    for match in INITIAL_AFTER.finditer(text):
        if match[1].casefold() not in STOP and not WEAK.match(match[1]) and len(match[1]) > 3:
            yield match.start(), match.end(), match[1]

LEGAL = (r"(?:a\.[ \t]?s\.|s\.[ \t]?r\.[ \t]?o\.|spol\.[ \t]?s[ \t]?r\.[ \t]?o\.|v\.[ \t]?o\.[ \t]?s\.|"
         r"k\.[ \t]?s\.|z\.[ \t]?s\.|z\.[ \t]?ú\.|o\.[ \t]?p\.[ \t]?s\.|s\.[ \t]?p\.|š\.[ \t]?p\.|"
         r"akciová společnost|společnost s ručením omezeným|akciová spoločnosť|"
         r"spoločnosť s ručením obmedzeným|komanditní společnost|veřejná obchodní společnost)")
LEGAL_FORM = re.compile(rf"(?<![\w.])({LEGAL})(?![\w])", re.I)
ORG_TOKEN = re.compile(r"[\w&+'’.-]+")
ROLE = set("společnost společnosti společností firma firmy firmou spoločnosť žalobce žalobkyně žalovaný "
           "žalovaná žalované žalovanou povinný povinná povinné oprávněný oprávněná oprávněné věřitel dlužník "
           "banka pojišťovna ičo ič dič".split())


def organisation_findings(text):
    for form in LEGAL_FORM.finditer(text):
        head = text[max(0, form.start() - 160):form.start()]
        offset = form.start() - len(head)
        trimmed = re.sub(r"[ \t\n ]*,?[ \t\n ]*$", "", head)
        tokens = []
        position = len(trimmed)
        while True:
            gap = re.search(r"[ \t\n „“”\"]*$", trimmed[:position])
            position = gap.start()
            token = None
            for candidate in ORG_TOKEN.finditer(trimmed[:position]):
                token = candidate
            if token is None or token.end() != position or len(tokens) >= 12:
                break
            tokens.append(token)
            position = token.start()
        start = None
        lowercase = 0
        for number, token in enumerate(tokens):
            value = token[0]
            upper = value[0].isupper() or bool(re.fullmatch(r"\d{1,4}\.?|\d+[^\W\d]\w*", value))
            if start is None:
                if upper and value.casefold() not in ROLE and value.casefold() not in STOP:
                    start = token.start()
                elif not upper and lowercase < 5 and not value.endswith("."):
                    lowercase += 1
                else:
                    break
            elif upper and value.casefold() not in ROLE and value.casefold() not in STOP:
                start = token.start()
            elif value in {"a", "&", "and", "-", "+"} and number + 1 < len(tokens) and tokens[number + 1][0][0].isupper():
                continue
            else:
                break
        if start is None:
            continue
        begin = offset + start
        # an opening quotation mark belongs to the name
        while begin > 0 and text[begin - 1] in "„“\"":
            begin -= 1
        name = text[begin:form.start()]
        yield begin, form.end(), organisation_identity(name), name.strip(" \t\n,„“”\"")


def organisation_identity(name):
    words = re.findall(r"[^\W_]+", fold(name))
    return " ".join(re.sub(r"[aeiouy]+$", "", word) if len(word) > 4 else word for word in words)


CITY = rf"(?:{CAP})(?:{SPACE}(?:(?:nad|pod|u|na|ve|v|a|pri|pod){SPACE})?{CAP}){{0,3}}"
STREET_PREFIX = r"(?:(?:ul|nám|tř|nábř|sídl|sídliště|náměstí|ulice|třída|nábřeží|námestie|ulica)\.?[ \t\n ]*)"
PREPOSITION = r"(?:(?:K|Ke|U|V|Ve|Na|Nad|Pod|Za|Před|Do|Od|Při|Mezi)[ \t\n ]+)"
STREET = (rf"(?:{STREET_PREFIX}?{PREPOSITION}?(?:{CAP}\.?|[{UPPER}]\.|[{LOWER}]{{2,}}|\d{{1,2}}\.)"
          rf"(?:{SPACE}(?:{CAP}\.?|[{UPPER}]\.|[{LOWER}]{{1,}})){{0,4}})")
STREET_STRICT = (rf"(?:{STREET_PREFIX}?{PREPOSITION}?(?:\d{{1,2}}\.[ \t ][{LOWER}]+"
                 rf"|(?:{CAP}\.?|[{UPPER}]\.)(?:{SPACE}(?:{CAP}\.?|[{UPPER}]\.)){{0,2}}))")
NUMBER = r"(?:č\.[ \t]?p\.[ \t]?)?\d{1,5}(?:/\d{1,4})?[a-z]?(?![\w/.]\d|\w)"
POSTCODE = r"(?:PSČ[ \t]*)?\d{3}[ \t ]?\d{2}"
ADDRESS_CUE = re.compile(
    r"\b(?:bytem|bydlištěm|bydliskom|bydlí(?:cí)?|se sídlem|sídlem|so sídlom|sídlom|místem podnikání|"
    r"trval(?:ý pobyt|é bydliště|e bytem|e bytom|ým pobytem)|trvalý pobyt|bytom|(?:doručovací )?adres[aeou]|pobytem)\b"
    r"[ \t\n ]*:?[ \t\n ]*(?:(?:v|ve|na)[ \t\n ]+)?", re.I)
ADDRESS = re.compile(
    rf"(?:{STREET}{SPACE}{NUMBER}(?:[ \t\n ]*,[ \t\n ]*(?:{POSTCODE}{SPACE})?{CITY}(?:{SPACE}\d{{1,2}}(?!\d|\.))?)?"
    rf"(?:[ \t\n ]*,[ \t\n ]*{POSTCODE})?"
    rf"|{CITY}[ \t\n ]*,[ \t\n ]*{STREET}{SPACE}{NUMBER}(?:[ \t\n ]*,[ \t\n ]*{POSTCODE}(?:{SPACE}{CITY})?)?"
    rf"|{CITY}{SPACE}{NUMBER}(?:[ \t\n ]*,[ \t\n ]*{POSTCODE}(?:{SPACE}{CITY})?)?"
    rf"|{POSTCODE}{SPACE}{CITY}|{CITY})(?=$|[\s,.;)])")
FREE_ADDRESS = re.compile(
    rf"(?<!\w)(?:{CITY}[ \t\n]*,[ \t\n]*{STREET_STRICT}{SPACE}{NUMBER}(?:[ \t\n]*,[ \t\n]*{POSTCODE}(?:{SPACE}{CITY})?)?"
    rf"|{CITY}{SPACE}{NUMBER}[ \t\n]*,[ \t\n]*(?:{POSTCODE}{SPACE})?{CITY}"
    rf"|{STREET_PREFIX}{STREET_STRICT}{SPACE}{NUMBER}(?:[ \t\n]*,[ \t\n]*(?:{POSTCODE}{SPACE})?{CITY})?(?:[ \t\n]*,[ \t\n]*{POSTCODE})?"
    rf"|{STREET_STRICT}{SPACE}{NUMBER}[ \t\n]*,[ \t\n]*{POSTCODE}{SPACE}{CITY}"
    rf"|{STREET_STRICT}{SPACE}{NUMBER}[ \t\n]*,[ \t\n]*{CITY}[ \t\n]*,[ \t\n]*{POSTCODE}"
    rf"|(?<!\d[  ])(?<!\d)\d{{3}}[  ]\d{{2}}{SPACE}(?!Kč|Sk\b|Eur)(?:{CITY}))(?=$|[\s,.;)])")
MONTHS = "ledna února března dubna května června července srpna září října listopadu prosince".split()
MONTHS_SK = "januára februára marca apríla mája júna júla augusta septembra októbra novembra decembra".split()
BIRTH_CUE = (r"\b(?:nar(?:ozen(?:ý|á|ého|é|ému|ou)?|oden(?:ý|á|ého|ej|ému|ou)?)?\.?|"
             r"dat(?:um|\.)[ \t\n ]*nar(?:ození|odenia|\.)|dátum[ \t\n ]+narodenia)"
             r"[ \t\n ]*:?[ \t\n ]*(?:(?:dne|dňa)[ \t\n ]+)?")
BIRTH = re.compile(BIRTH_CUE + r"(\d{1,2})\.[ \t\n ]*(\d{1,2})\.[ \t\n ]*(\d{4})(?!\d)", re.I)
BIRTH_TEXT = re.compile(BIRTH_CUE + r"(\d{1,2})\.[ \t\n ]*(" + "|".join(MONTHS + MONTHS_SK)
                        + rf"){SPACE}(\d{{4}})(?!\d)", re.I)
CASE = re.compile(
    r"(?:(?:sp\.[ \t\n]*zn\.|č\.[ \t\n]*j\.|spisová značka|číslo jednací|číslo jednacie)"
    r"[ \t\n ]*:?[ \t\n ]*)?"
    r"(?<![\w/])((?:\d{1,4}[ \t ]?)?[A-ZČŘŠŽ][A-Za-zČŘŠŽčřšž]{0,7}[ \t ]?\d{1,6}/\d{2,4}(?:[ \t]?-[ \t]?\d+)?)(?![\w/])")

# Field labels of personal forms. Folded (no diacritics); longest phrases first.
FORM_LABELS = sorted((phrase.split() for phrase in (
    "meno", "priezvisko", "rodne priezvisko", "meno a priezvisko", "datum narodenia", "miesto narodenia",
    "stat narodenia", "okres narodenia", "statne obcianstvo", "statna prislusnost", "cislo dokladu",
    "cislo dokladu totoznosti", "druh a cislo dokladu totoznosti", "cislo obcianskeho preukazu",
    "cislo pasu", "rodne cislo", "pohlavie", "cislo ziadosti", "kod vypisu", "kod rt", "kod odpisu",
    "meno otca", "priezvisko otca", "meno matky", "priezvisko matky", "rodne priezvisko matky",
    "trvaly pobyt", "prechodny pobyt", "adresa", "titul", "povodne meno", "povodne priezvisko",
    "predchadzajuce priezvisko", "prezyvka", "ziadatel", "jmeno", "prijmeni", "rodne prijmeni",
    "jmeno a prijmeni", "datum narozeni", "misto narozeni", "stat narozeni", "okres narozeni",
    "statni obcanstvi", "statni prislusnost", "cislo zadosti", "pohlavi", "bydliste", "cislo op",
    "cislo obcanskeho prukazu", "jmeno otce", "prijmeni otce", "jmeno matky", "prijmeni matky",
)), key=len, reverse=True)
LABEL_WORDS = {word for phrase in FORM_LABELS for word in phrase if len(word) > 1}
FIELD = re.compile(
    r"(?:^|(?<=[\s;|]))(?:" + "|".join(r"[ \t ]+".join(phrase) for phrase in FORM_LABELS)
    + r")[ \t ]*:[ \t ]*([^\n;|]*?[^\s;|:])(?=[ \t ]{2,}\S|[ \t ]*$|[ \t ]*[;|\t])", re.M)


def address_identity(value):
    parts = re.split(r"\s*,\s*", value.casefold())
    parts = [re.sub(r"^(?:psč\s*)?\d{3}[ \t ]?\d{2}\s+", "", part) for part in parts]
    return ", ".join(sorted(" ".join(part.split()) for part in parts))


def contextual_findings(text, known_surname=None, generic=True):
    for start, end, identity, _ in organisation_findings(text):
        yield start, end, "ORGANIZACE", identity
    folded = fold(text)
    for match in FIELD.finditer(folded):
        yield *match.span(1), "OSOBNI_UDAJ", text[match.start(1):match.end(1)]
    for match in CASE.finditer(text):
        if match[0] != match[1] or re.search(r"\d[ \t ]?[A-Za-z]", match[1]):
            yield *match.span(1), "SPIS", re.sub(r"\s", "", match[1])
    for pattern in (BIRTH, BIRTH_TEXT):
        for match in pattern.finditer(text):
            day, month, year = match.groups()
            if month.isdecimal():
                month = int(month)
            else:
                names = MONTHS if month.casefold() in MONTHS else MONTHS_SK
                month = names.index(month.casefold()) + 1
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
    for start, end, surname in initial_findings(text) if generic else ():
        yield start, end, "OSOBA", "příjmení " + surname_key(surname)
    for start, end, given, surname in person_findings(text, known_surname, generic):
        identity = name_identity(given, surname) if given else "příjmení " + surname_key(surname)
        yield start, end, "OSOBA", identity
