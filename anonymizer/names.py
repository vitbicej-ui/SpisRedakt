"""Czech and Slovak given names with generated case forms. No network, no model."""
import re

MASCULINE = """
adam adolf alan albert aleš alexandr alexander alexej alois andrej antonín arnošt artur augustin
bedřich benedikt bohdan bohumil bohumír bohuslav boleslav bořivoj branislav bronislav bruno
ctibor ctirad cyril čeněk čestmír dalibor dalimil daniel david denis dobroslav dominik drahomír
dušan eduard emanuel emil erik ervín evžen ferdinand filip florián františek gabriel gustav
hubert hugo hynek ignác igor ivan ivo jáchym jakub jan ján jarmil jaromír jaroslav jindřich
jiří jonáš josef jozef julius július kamil karel karol kazimír klement koloman kryštof kristián
ladislav leopold leoš libor lubomír ľubomír lubor luboš ľuboš ludvík luděk lukáš lumír marcel
marek marián marian martin matěj matej matouš matúš maxmilián michael michal mikuláš milan miloslav
miloš miroslav mojmír norbert oldřich oliver ondřej ondrej oskar otakar oto otto patrik pavel pavol
petr peter přemysl radek radim radomír radoslav radovan rastislav richard robert robin roman
rostislav rudolf samuel sebastian silvestr slavomír stanislav svatomír svatopluk šimon štefan
štěpán tadeáš teodor tibor tobiáš tomáš václav valentin vavřinec věroslav viktor vilém viliam
vincenc vít vítězslav vladan vladimír vladislav vlastimil vlastislav vojtěch vojtech vratislav
zbyněk zdeněk zdenek zdenko zikmund zoltán žigmund juraj ľudovít imrich gejza dezider attila
ladislav blahoslav bohumír borek bořek jarek mirek slávek vítek honza jirka pepa míla jano
""".split()

FEMININE = """
adéla adela adriana agáta alena alexandra alice alžběta alžbeta amálie anastázie andrea aneta
anežka anna antonie barbora beáta blanka bohdana bohumila bohuslava božena bronislava
cecílie dagmar dana daniela darina denisa diana dita dominika dorota drahomíra drahoslava
edita ela eliška ema emília emilie erika ester eva evelína františka gabriela hana hedvika helena
ilona ingrid irena iva ivana iveta ivona jana jarmila jaroslava jindřiška jiřina jitka johana
jolana judita julie júlia justýna kamila karin karla karolína kateřina katarína klára kornélie
kristina kristína kristýna květa květoslava lada laura lenka leona libuše lída lidmila linda
ľubica lubomíra ľudmila lucie lucia ludmila magdaléna magdaléna marcela margita marie mária
mariana marika markéta marta martina matylda michaela milada milena miloslava miluše miriam
miroslava monika naděžda natálie nela nikol nikola nina nora olga oľga oldřiška otýlie
patricie pavla pavlína petra radka regina renata renáta romana rozálie růžena sabina sandra
silvie simona soňa stanislava svatava světlana šárka štěpánka tamara taťána tereza terézia
valerie vanda věra vera veronika viera viktorie vilma vladimíra vlasta zdena zdeňka zdenka
zlata zora zuzana žaneta žofie alžbeta viola emília zita etela
""".split()

SOFT = "šžčřcjťďň"
FORMS = {}       # case form -> list of (lemma, feminine)


def masculine_forms(name):
    forms = {name}
    if name.endswith(("í", "y", "i")):
        return forms | {name + end for end in ("ho", "mu", "m")}
    if name.endswith("a"):
        return forms | {name[:-1] + end for end in ("y", "ovi", "u", "o", "ou", "om")}
    stems = [name[:-1] if name.endswith("o") else name]
    mobile = re.fullmatch(r"(.+?)([eě])(k)|(pav|kar|hav)(e)(l)", name)
    if mobile:
        head, vowel, tail = [part for part in mobile.groups() if part is not None]
        if vowel == "ě":
            head = head[:-1] + {"d": "ď", "n": "ň", "t": "ť"}.get(head[-1], head[-1])
        stems.append(head + tail)
    for stem in stems:
        forms.update(stem + end for end in ("a", "u", "e", "i", "ovi", "em", "om"))
    if re.search(r"[^aeiouyáéíóúůý]r$", name):
        forms.add(name[:-1] + "ře")
    return forms


def feminine_forms(name):
    forms = {name}
    if name.endswith(("ie", "ia")):
        return forms | {name[:-1] + end for end in ("e", "i", "í", "u", "ou", "a")}
    if name.endswith("e"):
        return forms | {name[:-1] + end for end in ("i", "í", "e")}
    if not name.endswith("a"):
        return forms | {name + end for end in ("y", "u", "ou", "e", "ě")}
    stem = name[:-1]
    forms.update(stem + end for end in ("y", "u", "o", "ou", "e", "ě", "i"))
    for old, new in (("ch", "š"), ("k", "c"), ("r", "ř"), ("h", "z"), ("g", "z")):
        if stem.endswith(old):
            forms.add(stem[:-len(old)] + new + "e")
            break
    return forms


for _name in MASCULINE:
    for _form in masculine_forms(_name):
        FORMS.setdefault(_form, []).append((_name, False))
for _name in FEMININE:
    for _form in feminine_forms(_name):
        FORMS.setdefault(_form, []).append((_name, True))


def is_given(word):
    return word.casefold() in FORMS


def feminine_surname(last):
    return bool(re.search(r"(?:ov(?:á|é|ou|ej|ú|ými)|[^o]á|ou|ej|ú)$", last))


def given_lemma(first, feminine):
    """Reduce a given-name case form to one stable key."""
    first = first.casefold()
    options = FORMS.get(first)
    if options:
        for lemma, gender in options:
            if gender == feminine:
                return lemma
        return options[0][0]
    stem = re.sub(r"(?:ovi|em|om|ou|[aueěyoií])$", "", first) if len(first) > 4 else first
    return stem + ("a" if feminine else "")


def surname_root(last):
    """Stable root shared by the case forms of one surname (both genders differ)."""
    last = last.casefold()
    female = re.fullmatch(r"(.+ov)(?:á|é|ou|ej|ú|ými)", last)
    if female:
        return female[1] + "á"
    adjective = re.fullmatch(r"(.{2,}?)(?:ý|á|ého|ému|ém|ým|é|ou|ej|ú)", last)
    if adjective:
        return adjective[1] + "ý"
    root = re.sub(r"(?<=[bcčdďfghjklľmnňprřsštťvzž])(?:ovi|em|om|a|u|e|i|ě)$", "", last) if len(last) > 3 else last
    root = re.sub(r"(?<=[bcčdďfghjklmnňprřsštťvzž])[eě](?=[kcl]$)", "", root) if len(root) > 4 else root
    if len(root) > 3:
        root = re.sub(r"[aoey]$", "", root)
    return root


def surname_pattern(last):
    """Regex source matching the case forms (and the other gender) of a surname."""
    root = surname_root(last)
    root = re.sub(r"(?:ová|ý)$", "", root)
    if len(root) < 3:
        return None
    if re.search(r"[^aeiouyáéíóúůý][kcl]$", root) and len(root) > 3:
        # mobile vowel: Hrubec / Hrubce, Pavel / Pavla
        root_pattern = re.escape(root[:-1]) + "[eě]?" + re.escape(root[-1])
    else:
        root_pattern = re.escape(root)
    return root_pattern + r"[a-záčďéěíľĺňóôŕřšťúůýž]{0,5}"
