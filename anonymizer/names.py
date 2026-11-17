"""Common Czech given names with generated case forms.

Regular paradigms are produced from the nominative so that the list stays
readable. Irregular or ambiguous names keep explicit rows in czech.py.
"""

MALE = """
Adolf Albert Alexandr Alois Ambrož Antonín Arnošt Bedřich Benjamin Bohdan Bohumil
Bohumír Bohuslav Boris Bořivoj Břetislav Ctibor Dalibor Denis Dušan Eduard Eliáš
Emanuel Emil Erik Evžen Ferdinand Hubert Ignác Igor Ivan Jáchym Jaromír Jindřich
Jonáš Josef Kamil Kristián Kryštof Ladislav Leoš Libor Lubomír Luboš Ludvík Lumír
Marcel Marian Matouš Matyáš Metoděj Milan Miloš Miloslav Mojmír Nikolas Norbert
Oldřich Oliver Oskar Otakar Přemysl Radim Radomír Radovan Rostislav Robert
Rudolf Richard Samuel Sebastián Šimon Stanislav Svatopluk Tadeáš Teodor Viktor
Vilém Vincenc Vincent Vít Vítězslav Vladimír Vladislav Vlastimil Vratislav
Vojtěch Zdeněk Bořek Hynek Vítek Leopold Kevin Robin Artur Herbert Hugo Ivo Oto
"""

# Nominative -> stem for names with a mobile e (Hynek -> Hynka).
MOBILE = {"zdeněk": "zdeňk", "bořek": "bořk", "hynek": "hynk", "vítek": "vítk"}

FEMALE = """
Adéla Adriana Agáta Alexandra Alžběta Aneta Anežka Blanka Bohumila Božena Dagmar
Dana Daniela Denisa Dita Drahomíra Ema Emílie Emma Ester Gabriela Hedvika Helena
Ilona Irena Iveta Iva Jarmila Jaroslava Jiřina Johana Jolana Julie Kamila Karin
Karolína Klára Klaudie Kristina Laura Leona Libuše Linda Ludmila Magdaléna
Marcela Marta Michaela Milada Milena Miriam Miroslava Natálie Nela Nikol Nikola
Olga Patricie Pavla Pavlína Radka Renata Romana Rozálie Sabina Silvie Sofie
Stanislava Stela Taťána Valerie Vanda Vendula Věra Viktorie Vladimíra Vlasta
Yvona Yveta Zdenka Zdeňka Zora Žaneta Alice Amálie Bára Dominika Jindřiška Ivona
Lucie Petronela Sára Tamara Viola
"""

SOFT = tuple("šžčřjcťďň")
# Forms that are ordinary Czech words at the start of a sentence.
SKIP = {"danou", "dany", "věrou", "věru"}


def male_forms(name):
    name = name.casefold()
    if name.endswith("o"):
        stem, soft = name[:-1], False
    else:
        stem, soft = MOBILE.get(name, name), name.endswith(SOFT)
    endings = ("e", "ovi", "i", "em") if soft else ("a", "ovi", "u", "em")
    return [name] + [stem + ending for ending in endings]


DATIVE = (("ch", "še"), ("k", "ce"), ("h", "ze"), ("g", "ze"), ("r", "ře"),
          ("d", "dě"), ("t", "tě"), ("n", "ně"), ("b", "bě"), ("p", "pě"),
          ("v", "vě"), ("m", "mě"), ("f", "fě"), ("l", "le"), ("s", "se"), ("z", "ze"))


def female_forms(name):
    name = name.casefold()
    if name.endswith("ie"):
        return [name, name[:-1] + "i", name[:-1] + "í"]
    if name.endswith("ce"):
        return [name, name[:-1] + "i", name[:-1] + "í"]
    if not name.endswith("a"):
        return [name]  # Dagmar, Ester, Karin, Miriam, Nikol stay unchanged.
    stem = name[:-1]
    forms = [name, stem + "u", stem + "ou", stem + "o"]
    if stem.endswith(SOFT):
        forms += [stem + "i", stem + "e"]
        return forms
    forms.append(stem + "y")
    for ending, dative in DATIVE:
        if stem.endswith(ending):
            forms.append(stem[: -len(ending)] + dative)
            break
    return forms


def generated():
    """Yield (nominative, forms, feminine) for the regular names above."""
    for name in MALE.split():
        yield name.casefold(), [form for form in male_forms(name) if form not in SKIP], False
    for name in FEMALE.split():
        yield name.casefold(), [form for form in female_forms(name) if form not in SKIP], True
