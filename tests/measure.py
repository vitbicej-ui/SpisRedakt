"""Leak and false-hit measurement on randomly composed synthetic Czech legal text.

The generator has its own declension tables, so the truth does not depend on the
anonymiser. Seeds below 100 are for development; report results from seeds >= 100.

    python tests/measure.py [first_seed] [count]
"""
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anonymizer.rules import Rules                      # noqa: E402

# given name: nominative, genitive(=accusative), dative, instrumental
MEN = [
    "Vendelín Vendelína Vendelínovi Vendelínem", "Bartoloměj Bartoloměje Bartolomějovi Bartolomějem",
    "Oto Oty Otovi Otou", "Zdeněk Zdeňka Zdeňkovi Zdeňkem", "Jiří Jiřího Jiřímu Jiřím",
    "Petr Petra Petrovi Petrem", "Lubomír Lubomíra Lubomírovi Lubomírem", "Kvido Kvida Kvidovi Kvidem",
    "Jaromír Jaromíra Jaromírovi Jaromírem", "Alois Aloise Aloisovi Aloisem", "Hynek Hynka Hynkovi Hynkem",
    "René Reného Renému Reném", "Dalibor Dalibora Daliborovi Daliborem", "Tadeáš Tadeáše Tadeášovi Tadeášem",
    "Horymír Horymíra Horymírovi Horymírem", "Mikoláš Mikoláše Mikolášovi Mikolášem",
]
WOMEN = [
    "Leontýna Leontýny Leontýně Leontýnou", "Hermína Hermíny Hermíně Hermínou", "Marie Marie Marii Marií",
    "Dagmar Dagmar Dagmar Dagmar", "Vratislava Vratislavy Vratislavě Vratislavou", "Olga Olgy Olze Olgou",
    "Blažena Blaženy Blaženě Blaženou", "Radka Radky Radce Radkou", "Libuše Libuše Libuši Libuší",
    "Vendula Venduly Vendule Vendulou", "Drahuše Drahuše Drahuši Drahuší", "Ludmila Ludmily Ludmile Ludmilou",
]
SURNAMES_M = [
    "Dvořák Dvořáka Dvořákovi Dvořákem", "Svoboda Svobody Svobodovi Svobodou",
    "Novotný Novotného Novotnému Novotným", "Němec Němce Němcovi Němcem", "Hájek Hájka Hájkovi Hájkem",
    "Procházka Procházky Procházkovi Procházkou", "Veselý Veselého Veselému Veselým",
    "Kříž Kříže Křížovi Křížem", "Sedláček Sedláčka Sedláčkovi Sedláčkem", "Doležal Doležala Doležalovi Doležalem",
    "Kučera Kučery Kučerovi Kučerou", "Beneš Beneše Benešovi Benešem", "Zeman Zemana Zemanovi Zemanem",
    "Kopecký Kopeckého Kopeckému Kopeckým", "Růžička Růžičky Růžičkovi Růžičkou", "Schwarz Schwarze Schwarzovi Schwarzem",
    "Vlk Vlka Vlkovi Vlkem", "Šťastný Šťastného Šťastnému Šťastným", "Krejčí Krejčího Krejčímu Krejčím",
    "Pospíšil Pospíšila Pospíšilovi Pospíšilem", "Macháček Macháčka Macháčkovi Macháčkem",
]
SURNAMES_F = [
    "Dvořáková Dvořákové Dvořákové Dvořákovou", "Novotná Novotné Novotné Novotnou",
    "Němcová Němcové Němcové Němcovou", "Veselá Veselé Veselé Veselou", "Kučerová Kučerové Kučerové Kučerovou",
    "Krejčí Krejčí Krejčí Krejčí", "Šťastná Šťastné Šťastné Šťastnou", "Růžičková Růžičkové Růžičkové Růžičkovou",
    "Schwarzová Schwarzové Schwarzové Schwarzovou", "Vlková Vlkové Vlkové Vlkovou",
]
COMPANIES = ["Dřevostavby Podhůří s.r.o.", "MORAVIA TRANS LOGISTIC a.s.", "Pekárna u Tří lip, spol. s r.o.",
             "AGRO Vysočina, a.s.", "První chodská stavební v.o.s.", "KOVO-TECH Beskydy s. r. o.",
             "Autodoprava Hruška a syn s.r.o.", "Lékárna Na Růžku k.s.", "ELEKTRO 2000 Plzeň, s.r.o.",
             "Zemědělské družstvo Háj, a.s.", "B+K servis s.r.o.", "Reality & Finance Group a.s."]
STREETS = ["Lipová 14", "nám. Míru 3", "tř. Tomáše Bati 1560", "U Stadionu 221/7", "Na Výsluní 8a",
           "ul. 17. listopadu 45", "Hlavní 112", "nábř. Kpt. Nálepky 9", "K Lesu 5", "Palackého 1234/56"]
CITIES = ["Prostějov", "Kutná Hora", "Ústí nad Orlicí", "Zlín", "Česká Třebová", "Písek", "Nový Jičín", "Opava"]
POSTCODES = ["796 01", "284 01", "562 01", "760 01", "560 02", "397 01", "741 01", "746 01"]
KEEP = ["125 000 Kč", "4 800 Kč", "1 250 000 Kč", "15 dnů", "§ 142 odst. 1 o. s. ř.", "zákona č. 99/1963 Sb.",
        "Krajský soud v Brně", "Okresní soud v Prostějově", "Nejvyšší soud", "Městský soud v Praze",
        "dne 3. 4. 2023", "dne 15. ledna 2022", "8,05 % ročně", "do tří dnů od právní moci", "Česká republika"]


class Person:
    def __init__(self, rng):
        self.woman = rng.random() < 0.4
        given = rng.choice(WOMEN if self.woman else MEN).split()
        last = rng.choice(SURNAMES_F if self.woman else SURNAMES_M).split()
        self.given, self.last = given, last
        self.nom, self.gen, self.dat, self.ins = (f"{a} {b}" for a, b in zip(given, last))
        self.acc = self.gen if not self.woman else None

    def secrets(self):
        return set(self.given) | set(self.last)


def document(seed):
    rng = random.Random(seed)
    secrets, keeps, lines = set(), set(), []
    people = [Person(rng) for _ in range(3)]

    def secret(value):
        secrets.add(value)
        return value

    def keep():
        value = rng.choice(KEEP)
        keeps.add(value)
        return value

    def address():
        index = rng.randrange(len(CITIES))
        street, city, code = rng.choice(STREETS), CITIES[index], POSTCODES[index]
        form = rng.choice(["{s}, {p} {c}", "{c}, {s}", "{s}, {c}", "{s}, {c}, PSČ {p}"])
        for part in re.findall(r"[^\W\d_]{4,}", street):
            secrets.add(part)
        secrets.add(re.search(r"\d[\d/a-z]*$", street)[0]) if len(re.search(r"\d[\d/a-z]*$", street)[0]) > 3 else None
        return form.format(s=street, c=city, p=code)

    for person in people:
        secrets.update(person.secrets())
    a, b, c = people
    company = secret(rng.choice(COMPANIES))
    ico = secret(f"{rng.randrange(10, 99)}{rng.randrange(100, 999)}{rng.randrange(100, 999)}")
    ico_shown = rng.choice([ico, f"{ico[:3]} {ico[3:5]} {ico[5:]}", f"{ico[:2]} {ico[2:5]} {ico[5:]}"])
    secrets.add(ico_shown)
    birth = secret(f"{rng.randrange(1, 28)}. {rng.randrange(1, 12)}. {rng.randrange(1950, 2005)}")
    rc = secret(f"{rng.randrange(50, 99)}{rng.randrange(1, 12):02d}{rng.randrange(1, 28):02d}/{rng.randrange(1000, 9999)}")
    mail = secret(f"{a.last[0].lower().encode('ascii', 'ignore').decode() or 'osoba'}{rng.randrange(99)}@posta.example")
    phone = secret(f"{rng.choice('67')}{rng.randrange(10, 99)} {rng.randrange(100, 999)} {rng.randrange(100, 999)}")
    account = secret(f"{rng.randrange(10**7, 10**9)}/0{rng.choice(['100', '300', '800'])}")
    case = secret(f"{rng.randrange(1, 60)} {rng.choice(['C', 'Co', 'Nc', 'EXE', 'T'])} {rng.randrange(1, 900)}/{rng.randrange(2015, 2026)}")
    templates = [
        lambda: f"{a.nom} se k jednání bez omluvy nedostavil{'a' if a.woman else ''}.",
        lambda: f"Soud vyslechl svědka, jímž byl {b.nom}, a poté rozhodl o částce {keep()}.",
        lambda: f"Usnesení bylo {a.dat} doručeno {keep()}.",
        lambda: f"S {b.ins} byla uzavřena smlouva o dílo.",
        lambda: f"Podle výpovědi {c.gen} k předání díla nedošlo.",
        lambda: f"{a.last[0]} dále uvedl{'a' if a.woman else ''}, že dlužnou částku {keep()} již uhradil{'a' if a.woman else ''}.",
        lambda: f"Zástupce žalobce JUDr. {c.nom} navrhl odročení jednání.",
        lambda: f"Žalovaný {a.nom}, nar. {birth}, bytem {address()}, je povinen zaplatit žalobci {keep()}.",
        lambda: f"Žalobce: {company}, IČO {ico_shown}, se sídlem {address()}.",
        lambda: f"Společnost {company} (dále jen „žalobce“) se domáhá zaplacení podle {keep()}.",
        lambda: f"Rodné číslo povinného: {rc}. Kontakt: {mail}, telefon {phone}.",
        lambda: f"Platbu poukažte na účet č. {account} vedený u banky.",
        lambda: f"Ve věci vedené pod sp. zn. {case} rozhodl {keep()} takto.",
        lambda: f"Proti {b.dat} bylo zahájeno řízení; {b.last[0]} se nevyjádřil{'a' if b.woman else ''}.",
        lambda: f"Rodiče nezletilého, {a.nom} a {b.nom}, s návrhem souhlasí.",
        lambda: f"Věřitel {company} postoupil pohledávku ve výši {keep()}.",
        lambda: f"Opatrovníkem byl jmenován {c.nom}, advokát se sídlem {address()}.",
        lambda: f"Odvolací soud ({keep()}) rozsudek potvrdil.",
        lambda: f"Pan{'í' if c.woman else ''} {c.nom if c.woman else c.nom} převzal{'a' if c.woman else ''} zásilku osobně.",
        lambda: f"K rukám: {a.nom}\n{address()}",
        lambda: f"Poučení: Proti tomuto usnesení lze podat odvolání {keep()}.",
        lambda: f"Jednatel společnosti {company} {b.nom} smlouvu podepsal.",
    ]
    for template in rng.sample(templates, 14):
        lines.append(template())
    return "\n".join(lines), secrets, keeps


def measure(first=0, count=50, verbose=True):
    totals = Counter()
    for seed in range(first, first + count):
        text, secrets, keeps = document(seed)
        output = Rules().replace(text)[0]
        totals["documents"] += 1
        for value in sorted(secrets):
            totals["secrets"] += 1
            if re.search(r"(?<![^\W_])" + re.escape(value) + r"(?![^\W_])", output):
                totals["leaks"] += 1
                if verbose:
                    line = next(line for line in output.splitlines() if value in line)
                    print(f"[{seed}] UNIK {value!r}: {line}")
        for value in sorted(keeps):
            totals["keeps"] += 1
            if value not in output:
                totals["false_hits"] += 1
                if verbose:
                    print(f"[{seed}] ZASAH {value!r}")
    return totals


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    print(dict(measure(first, count)))
