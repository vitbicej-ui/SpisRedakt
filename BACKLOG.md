# Backlog

Pořadí je priorita. Orchestrátor bere úkoly shora, stav zapisuje sem: `[ ]` čeká, `[~]` rozpracováno, `[x]` hotovo (s commitem), `[?]` čeká na rozhodnutí člověka.

## Produkce: spis do cloudového modelu

Cíl běhu 17. 11. 2026 (termín 18. 11.): jeden ZIP se spisem → `spis_anonymizovano.md` pro jazykové modely v cloudu. Tato sekce má přednost před ostatními. Náhled v GUI, ruční vyznačení ploch, jednotné pseudonymy mezi dávkami a převod přes Office jsou do té doby odloženy.

- [x] **0. Syntetický spis a detektor úniků.** (473dd51, opraveno 8ce9b00; počáteční úniky v kontrolních kopiích: JMENO 1, TELEFON 2, EMAIL 2, UCET 1 – účet zalomený na další řádek, jméno rozdělené spojovníkem a e-mail ve skenu, `@` čtený OCR jako `©`, telefon v PDF po OCR) Generátor syntetického ZIPu se všemi formáty a soudními šablonami, nezávislý detektor úniků (všechny vrstvy výstupu, varianty bez diakritiky, verzálkami, s mezerami, v pádech), vypisuje jen počty podle druhu.
- [x] **1. Samotná příjmení v celém spisu.** (d34ded5, dvoufázová dávka 3a5b252) Dvoufázové zpracování ZIPu i dávky, příjmení ve všech pádech a obou rodech, „pan/paní + slovo s velkým písmenem“ jako osoba.
- [x] **2. Úsporný Markdown spisu.** (extrakce 16f935c, vykreslení 278f156, dávka a GUI 3a5b252; opravy po ruční kontrole: citace a OCR dd1aa68, křestní jména 8880a8a) `spis_anonymizovano.md`: extrakce všech textů, čištění OCR, dvoufázová anonymizace, deterministické úspory (záhlaví/zápatí, duplicity, opakované odstavce, citované e-maily, krátké štítky), legenda, obsah s odhadem tokenů, kontrola před vydáním. Volby v GUI „Vytvořit také anonymizovaný ZIP pro kontrolu“ a „Rozdělit po dokumentech“ (výchozí vypnuto).
- [x] **3. Víceřádkové nadpisy sloupců.** (77a3120) Viz položka v sekci Robustnost; nově i XLSX a Markdown.
- [x] **4. Vydání spisu bez odmítnutých položek.** Markdown a kontrolní ZIP bez odmítnutých položek, seznam nezpracovaných položek (pořadí + kód), `NEZPRACOVANO.txt`, hlášení v GUI a auditu, README. (3a5b252)
- [x] **5. Bezpečné výchozí chování.** Kontrolní ZIP bez metadat a původních názvů, skryté listy/řádky/sloupce XLSX, obrázky bez textu začerněné celé, v Markdownu vynechané.
- [x] **6. Výkon na velkém spisu.** (af3e40f; 200 MB / 300 stran skenů: Markdown 4 min a 475 MB, s kontrolním ZIPem 7 min a 715 MB, 0,34 s na stranu OCR, tokeny 261 tis. → 170 tis.) ZIP ~200 MB / ~300 stran skenů, proudové čtení položek, průběh v GUI, změřený čas.
- [x] **7. README a GUI.** Sekce „Výstup pro jazykové modely v cloudu“, souhrn po dokončení, nové rady v `ADVICE`.
- [x] **Chybně přečtená čísla z OCR.** (d9703fa; čísla za klíčovými slovy všude, dlouhé skupiny číslic ve skenech jako `[CISLO-n]`, detektor hlásí varianty s jednou chybnou číslicí) Když OCR přečte telefon nebo účet chybně (např. `732 123 1609`), pravidla číslo nenajdou a kontrola před vydáním ho nepozná, protože zná jen nalezené hodnoty. Zvážit podezřelé číselné řetězce ve skenech (dlouhé skupiny číslic v kontextu `tel.`, `účet`, `nar.`) nahrazovat štítkem nebo je začernit celé.

## Robustnost u skutečných spisů

- [x] **Důvod chyby v GUI.** (8e048ac) U odmítnutého souboru zobrazit srozumitelnou radu podle kódu z `anonymizer/reasons.py` (např. „Odstraňte makra a uložte znovu jako DOCX“).
- [x] **Paměť u velkých vstupů.** (d0519bc) `batch.py` čte celý soubor do paměti (až 500 MB). Rozpoznat formát z hlavičky nebo cesty a soubor kopírovat po blocích.
- [x] **Výkon pravidel.** (df4a646, 1,7–2,8×) `Rules.find` zpracuje asi 1 MB textu za sekundu. Profilovat `contextual_findings` a zrychlit bez změny výsledků.
- [x] **Přesné tabulky v DOC.** (c78c7ab; víceřádkový nadpis buňky se zatím nerozpozná jako nadpis sloupce) `legacy.py` skládá tabulky heuristicky. Použít vlastnosti odstavců (PlcBtePapx, sprmPFInTable), aby se víceodstavcové buňky nerozpadaly.
- [x] **Nadpisy sloupců v RTF tabulkách.** (7561d5e) Napojit `plan_table` na buňky RTF (`\cell`, `\row`).
- [x] **Víceřádkové nadpisy sloupců.** (77a3120, = úkol 3 výše) Nadpis buňky rozdělený do odstavců (např. „Jméno“ / „a příjmení“) se nerozpozná jako sloupec jmen v DOC, DOCX, ODT ani RTF. Normalizovat zalomení v `column_kind`, aby se hodnoty pod takovým nadpisem označily.

## Kontrola výstupů

- [x] **Linuxový CI job s LibreOffice.** `scripts/check_outputs.py` a workflow `outputs.yml` (schváleno 17. 11. 2026). Rozšířit o PPTX/ODP a e-maily může orchestrátor úpravou skriptu, ne workflow.
- [x] **Regresní korpus.** (6fc8c33, 76e50a9; simulace Windows `tests/winsim.py`, obtížná písma, zalomení, smíšené skeny a soudní šablony; nalezeny a opraveny 3 úniky: hodnoty zalomené přes řádek, adresa po „e-mail“ bez čitelného „@“, zkomolená adresa v otočeném skenu) Rozšířit syntetické testy o smíšené skeny, obtížná písma, zalomení řádků a typické soudní šablony (záhlaví, zápatí, doložka „Shodu s prvopisem potvrzuje“).

## Funkce

- [?] **Náhled v GUI.** (návrh v PREDANI-VYVOJE.md, oddíl 14; čeká na schválení rozhraní) Zobrazit originál a výstup vedle sebe po stránkách, bez ukládání dalších kopií.
- [?] **Ruční vyznačení ploch.** (návrh v PREDANI-VYVOJE.md, oddíl 14; čeká na schválení rozhraní) V náhledu PDF nebo obrázku umožnit doplnit začernění myší a výstup znovu ověřit.
- [x] **Jednotné pseudonymy mezi dávkami** (hotovo, c9088d8) (rozhodnuto 17. 11. 2026: varianta B). Mapa se ukládá do výstupní složky spisu (`.spisredakt-mapa`). Neobsahuje jména, jen HMAC-SHA256 otisky normalizované identity (`druh + identity`) a k nim štítky a čítače. Klíč HMAC je náhodný, chráněný Windows DPAPI (CryptProtectData přes ctypes, bez nové závislosti) a uložený vedle mapy. Mimo Windows nebo při chybě DPAPI se mapa nepoužije a dávka běží jako dnes. Spojování iniciál se známými jmény musí fungovat i z mapy. Testy: dvě dávky do stejné výstupní složky dají stejné štítky a soubor mapy neobsahuje čitelné identity.
- [x] **Automatické vydání podle štítku.** Workflow `release.yml` (schváleno 17. 11. 2026). Štítek musí odpovídat `VERSION`. Štítky vytváří jen člověk.

- [x] **Věrný převod DOC a XLS přes nainstalovaný Office** (hotovo v 7b167dd, jen LibreOffice; COM viz níže) (rozhodnuto 17. 11. 2026: varianta B). Volba v GUI „Převádět DOC/XLS přes Word nebo LibreOffice“, výchozí stav vypnuto, uložená v nastavení aplikace. Když je zapnutá a na počítači je Word nebo Excel (COM přes ctypes/comtypes jen se schválenou závislostí, jinak `soffice --headless --convert-to`, nalezený v obvyklých cestách), převést soubor na DOCX/XLSX v dočasné složce a pokračovat běžnou cestou Office. Bez nalezeného programu nebo při chybě použít dnešní vlastní převod. Žádná síť, žádné makro, převod s vypnutými makry a aktualizacemi odkazů. Novou závislost (např. pywin32) označ `[?]`.
- [?] **Převod DOC/XLS přes COM (Word/Excel).** Vyžaduje novou závislost (pywin32 nebo comtypes), kterou musí schválit člověk.

## Hotovo

- [x] 0.9.0 (vydáno 18. 11. 2026 jako předběžné vydání `v0.9.0`, PR #4): Markdown spisu pro cloudové modely, samotná příjmení, detektor úniků, čísla z OCR, nezlomitelné mezery a měkké spojovníky, e-maily ve skenech z Windows.
- [x] 0.9.0: spis jako úsporný Markdown pro cloudové modely, samotná příjmení a křestní jména v celém spisu, víceřádkové nadpisy sloupců, vydání spisu bez odmítnutých položek, detektor úniků.

- [x] 0.8.2: rady k odmítnutým souborům v GUI, vstupy kopírované po blocích, 1,7–2,8× rychlejší pravidla.
- [x] 0.8.1: důvody chyb v auditu, šablony a pole ve Wordu, opravená a velká PDF, ZIP do 500 MB se všemi formáty, citace judikatury.
