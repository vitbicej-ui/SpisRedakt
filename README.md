# SpisRedakt 0.7.0 (rozpracováno, nevydáno)

Offline aplikace pro Windows pro dávkovou anonymizaci českých a slovenských spisů. Vznikla nezávisle s funkční inspirací z [veřejného popisu SKRYI](https://www.skryi.cz/index.html). Licence projektu je AGPL-3.0-only.

## Spuštění

Rozbalte celý balíček pro Windows a spusťte **SpisRedakt.exe**. Vedle programu je složka `tessdata` s českým modelem OCR. Program nemá síťové funkce.

Ze zdrojů v PowerShellu s Pythonem 3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\get-ocr.ps1
.\.venv\Scripts\python.exe start.py
```

Pokud OCR nedokáže otevřít cestu s diakritikou, rozbalte aplikaci do složky bez diakritiky, například `C:\SpisRedakt`.

Jednorázové zpracování bez okna: `SpisRedakt.exe --once <vstupní složka> <výstupní složka>`.

## Práce se spisem

Vstupní a výstupní složku nastavíte jednou; aplikace si je pamatuje. Dokud je otevřená, sleduje vstupní složku včetně podsložek a zpracuje každý nový nebo změněný soubor, jakmile se dvě kontroly po sobě nezmění jeho velikost ani čas a soubor jde otevřít (kopírování po částech tedy počká).

- `Smlouva.docx` se uloží jako `Smlouva_anonymizovano.docx`, struktura podsložek zůstává. Originály se nikdy nemění.
- Stejnou verzi souboru aplikace nezpracuje podruhé, ani po restartu. Smazání výstupu nové zpracování nespustí. Přejmenovaný nebo změněný vstup je nový soubor.
- Tlačítko „Zpracovat složku znovu“ dávku zopakuje; výsledky dostanou volné názvy `_anonymizovano_2`, `_3` atd.
- Zamčený soubor se zkouší znovu (nejvýše pětkrát). Je-li některá složka nedostupná, sledování počká na její připojení.

**Soubor, jehož výsledek nelze ověřit, se do výstupní složky nevydá.** Dávka pokračuje dalšími soubory a důvod je v protokolu aplikace i v auditu.

## Co se odstraňuje

- Osoby: jména ve všech pádech (slovník asi 2 800 tvarů českých a slovenských křestních jmen), jména po titulech a procesních rolích, příjmení před jménem, iniciály (`J. Novák`, `Vendulka H.`), neuvedená křestní jména před již známým příjmením a osamocená příjmení uprostřed věty. Příjmení poznaná kdekoli v dokumentu se odstraní ve všech jeho částech i pádech; jedna osoba má v textových formátech jeden štítek, různé osoby s podobným jménem různé.
- Organizace s právní formou (`s.r.o.`, `a.s.`, `spol. s r.o.`, `v.o.s.`, `k.s.` a další) včetně víceslovných názvů a jejich pozdějších zmínek bez právní formy.
- Adresy po slovech `bytem`, `sídlem`, `trvale bytom` apod. i samostatné zápisy ulice, čísla, PSČ a obce včetně zkratek `ul.`, `nám.`, `tř.`, `nábř.`.
- Data narození (podle uvození), rodná čísla, IČO i s mezerami, DIČ, doklady, bankovní účty, IBAN CZ/SK, telefony CZ/SK, e-maily, webové domény, datové schránky, registrační značky, VIN a spisové značky.
- Hodnoty osobních polí formulářů (`Meno:`, `Priezvisko:`, `Rodné číslo:` …) v textu i v tabulkách Wordu.

Zachovávají se peněžní částky, procesní data, lhůty, citace předpisů a názvy soudů.

## Slovenský výpis z registra trestov a osobní formuláře v PDF

Formulář se zpracovává **podle polohy**, ne podle pořadí textu. Aplikace najde popisky polí (snese i mírně poškozené OCR), a celý vodorovný pás každého popisku začerní kromě popisku samotného – hodnota tak zmizí, ať je vedle popisku nebo pod ním, a to i když ji OCR nepřečetlo (rukopis, razítko). Hodnoty přečtené z formuláře se pak odstraní i jinde v dokumentu. Husté grafické bloky (QR a čárové kódy, fotografie) se na formuláři začerní.

Výpis se vydá, jen když jsou nalezena pole meno, priezvisko, dátum narodenia, rodné číslo nebo číslo dokladu a celkem aspoň 8 z 12 očekávaných polí. Chybí-li některé, pásy se pro jistotu rozšíří o řádek. Jinak se soubor zadrží.

## Kontrola vydání

Každý výsledek se před vydáním znovu přečte nezávisle na tom, co nahlásily detektory:

- PDF a obrázky: hotový obraz stránky projde novým OCR. Najde-li se v něm údaj, začerní se a kontrola se opakuje; zůstane-li i potom, soubor se zadrží. Na formuláři nesmí v pásech hodnot zůstat nic čitelného. Uložený obraz se porovná s kontrolovaným.
- DOCX, XLSX, PPTX: znovu se otevře zapsaný balíček a projde se veškerý text všech částí včetně metadat, komentářů, poznámek a nepodporovaných částí.
- Textové formáty a ZIP: výstup se znovu zpracuje a nesmí se změnit.

Testy tuto kontrolu ověřují záměrně rozbitou detekcí: nezačerněné pole i nenahrazený text musí vést k zadržení.

## Meze, které je třeba znát

Žádný automat nemůže zaručit úplnost u libovolného dokumentu. Konkrétně:

- Osamocené příjmení na začátku věty, které se v dokumentu nikde nevyskytuje se jménem, titulem ani rolí, nemusí být rozpoznáno.
- Rukopis, razítka a podpisy mimo formulářová pole OCR nečte; zůstávají v obraze.
- Nepřímá identifikace z kontextu (funkce, popis události, vzácné povolání) se neposuzuje.
- OCR používá český model; slovenské znaky čte přibližně. Popisky formuláře se proto hledají s tolerancí.
- Aplikace raději odstraní víc: může začernit i název obce nebo běžné slovo psané s velkým písmenem uprostřed věty.
- Nepodporované jsou DOC, XLS, RTF, EML, MSG, 7z a RAR; přeskočí se. Office soubory s makry, vloženými objekty nebo externími daty se zadrží. Tabulkový formulář v XLSX/PPTX s osobními poli se zadrží.
- PDF výstup je obraz stránky při 300 DPI bez textové vrstvy.

## Audit dávky

Každá dávka zapíše `events.jsonl` a `souhrn.txt` do složky `audit` vedle aplikace (nebo do `SPISREDAKT_AUDIT_DIR`). Protokol obsahuje pořadová čísla, formáty, stav (`released`, `withheld`, `failed`, `skipped`), kód důvodu (`form_incomplete`, `form_field_unredacted`, `residual_data` …) a u vydaných souborů výsledky kontrol. Neobsahuje obsah, názvy ani cesty dokumentů.

## Testy a sestavení

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$env:SPISREDAKT_TEST_TESSDATA = (Resolve-Path tessdata).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\scripts\build-windows.ps1
```

Všechny testy používají výhradně smyšlené údaje.

- `tests/test_scenarios.py` – oba referenční scénáře (slovenský výpis: textové PDF i sken, hodnoty vedle i pod popisky, tři pořadí textových bloků, bez nadpisu, poškozené OCR, neúplný formulář, druhá strana; české usnesení: DOCX, PDF, sken), kontrola vydání se záměrnou poruchou a smíšená dávka všech formátů. Úniky se ověřují podle známých poloh v obraze, ne podle detektorů.
- `tests/measure.py` – měření na náhodně skládaných dokumentech s vlastním skloňováním: `python tests/measure.py 100 300`.
- `tests/challenge.py` – ručně psané záludné případy.
- `tests/test_watch.py` – sledování složek včetně běhu skutečného okna bez displeje.
- `tests/mixed.py` – smíšená dávka pro ověření sestavené aplikace: `build` vytvoří vstup, `verify` zkontroluje výstup.

Pokyny k dalším iteracím jsou v [AUTOMATION.md](AUTOMATION.md).
