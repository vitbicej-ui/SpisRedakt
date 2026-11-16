# SpisRedakt 0.7.0

Offline aplikace pro Windows pro dávkové zpracování DOCX, XLSX, PPTX a PDF. Vznikla nezávisle s funkční inspirací z [veřejného popisu SKRYI](https://www.skryi.cz/index.html). Licence projektu je AGPL-3.0-only.

## Spuštění

Rozbalte celý balíček pro Windows a spusťte **SpisRedakt.exe**. Vedle programu je složka `tessdata` s českým modelem. Program nemá síťové funkce. Instalace závislostí při sestavení ze zdrojů vyžaduje internet nebo místní balíčky.

Ze zdrojů v PowerShellu s Pythonem 3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py
```

Při spuštění ze zdrojů připravte česká data OCR skriptem `./scripts/get-ocr.ps1`. Vytvoří složku `tessdata` vedle `start.py`. Pokud OCR nedokáže otevřít cestu s diakritikou, rozbalte celou aplikaci do složky s cestou bez diakritiky, například `C:\SpisRedakt`.

## Práce se spisem

Aplikace si pamatuje obě složky. Dokud je otevřená, sleduje nové a změněné soubory a čeká na dvě shodné kontroly velikosti a času změny. Stejný soubor pro stejný výstup automaticky neopakuje ani po restartu. Tlačítko „Zpracovat složku znovu“ umožní zopakovat dávku. Soubory zachycené v různých dávkách mohou dostat odlišné pseudonymy.

Pokud aplikace rozpozná slovenský výpis z rejstříku trestů nebo osobní formulář v PDF, výstup nevydá. Rozpoznání formuláře závisí na čitelnosti textu a OCR. Neúplnou redakci ze starších verzí nepoužívejte pro sdílení.

1. Vyberte vstupní složku s dokumenty jednoho spisu. Program zpracuje DOCX, XLSX, PPTX a PDF i v podsložkách. Ostatní formáty přeskočí.
2. Vyberte samostatnou výstupní složku a tím spusťte dávku. Název `Smlouva.docx` se změní na `Smlouva_anonymizovano.docx`. Struktura podsložek zůstává zachována. Originály i existující výstupy zůstanou beze změny. Název souboru může obsahovat osobní údaj, proto zkontrolujte také názvy před sdílením.
3. Porovnejte každý výstup s originálem, včetně všech stran, příloh a významu ponechaného textu. Případné přehlédnuté údaje odstraňte před sdílením.

## Audit dávky

Po každé dávce aplikace zobrazí cestu k vlastní složce s průběžným protokolem `events.jsonl` a závěrečným `souhrn.txt`. Výchozí složka `audit` leží vedle aplikace. Chcete-li audity zpřístupnit Codexu i při spuštění aplikace z jiné složky, nastavte před spuštěním proměnnou `SPISREDAKT_AUDIT_DIR` na místní zapisovatelnou složku v pracovním prostoru. Při chybě při založení auditu aplikace zkusí místní dočasnou složku. Protokol obsahuje pořadová čísla, formáty a kódy výsledků bez obsahu, názvů a cest zpracovaných dokumentů. Opakovaná dávka přidá k existujícímu výstupu příponu `_2`, `_3` atd.

## Rozsah zpracování

- Dávka přijímá TXT, CSV, TSV, JSON a XML, samostatné PNG, JPEG a BMP a ZIP s textovými soubory nebo vnořenými ZIP. XML s DTD a archivy s jinými položkami se nevydávají. Rozpoznání podle signatury funguje u PDF, ZIP, moderního Office a podporovaných obrázků. Textové formáty používají příponu.
- Kontrola před zveřejněním znovu načte textové výstupy a ověří, že pravidla nenajdou další náhrady. U Office a ZIP ověřuje také čitelnost balíčku. Výsledek kontroly vypovídá o použitých pravidlech a zpracovaných vrstvách. Kontextově nepřímá identifikace a nerozpoznané údaje zůstávají možné.

- DOCX, XLSX a PPTX zachovávají strukturu odstavců, tabulek, buněk a snímků a jejich styly. Štítek převezme formát začátku nahrazeného textu. Délka štítku může ovlivnit zalomení nebo výšku řádků. U běžných externích hypertextových odkazů se cílová adresa nahradí nefunkční adresou. Externě načítaná data či obrázky, makra a vložené přílohy mohou zpracování zastavit. Kontrolujte i skryté listy, názvy, vzorce a číselné buňky.
- Tabulky XLSX, CSV, TSV a tabulky v DOCX a PPTX rozpoznávají sloupce podle nadpisu, například `Jméno a příjmení`, `Datum narození`, `Rodné číslo`, `IČO`, `Číslo OP`, `Telefon`, `E-mail`, `Číslo účtu` nebo `Adresa`. Celá neprázdná buňka pod takovým nadpisem dostane štítek, i když sama hodnotu pravidla nerozpoznají. Datum narození uložené v XLSX jako číslo s formátem data se také nahradí štítkem. Nadpis se musí shodovat celý, sloupce jako `Datum podpisu` nebo `Poznámka` zůstávají beze změny. Sdílený text XLSX z takového sloupce se nahradí ve všech buňkách sešitu. Vzorec ve sloupci s osobními údaji zastaví zpracování k ruční kontrole. V DOCX a PPTX převezme štítek formát prvního úseku buňky a sloučené buňky se počítají podle mřížky tabulky. Jakmile má tabulka známé sloupce, nový řádek nadpisů musí obsahovat jen nadpisy, aby hodnota jako `OP` nepřerušila redakci. Formuláře s popiskem vlevo, například `Jméno | Jan Novák` nebo `Rodné číslo: | …`, se rozpoznají také: s dvojtečkou v jakékoli tabulce, bez dvojtečky v tabulce, jejíž řádky převážně začínají popiskem. Mezery mezi popiskem a hodnotou se přeskočí. V textu se stejně rozpozná řádek `Popisek: hodnota`. U páru s dvojtečkou musí hodnota odpovídat druhu údaje (číslice, `@`, u jména aspoň dvě slova), aby se za hodnotu nepovažoval další nadpis. Tabulky bez nadpisů a popisků automaticky nerozpoznáváme.
- Vložené PNG a JPEG procházejí OCR v plném rozsahu včetně částí schovaných ořezem. Nalezený text se začerní přímo v pixelech a obrázek se znovu uloží bez původních obrazových metadat. Zachová se umístění, velikost a ořez v dokumentu. Pokud OCR nenajde žádný čitelný text, celý obrázek se začerní. Obrázky mohou ztratit průhlednost a OCR může přehlédnout údaje, zejména u malého textu, rukopisu a kódů. Další obrazové formáty zatím vyžadují převod na PNG nebo JPEG.
- Pravidla hledají e-maily, české telefony, rodná čísla, označené bankovní účty a spisové značky, CZ IBAN, DIČ, označená IČO a doklady. Datum narození se rozpoznává podle slov v okolí, například `nar. dne` nebo `datum narození`. Data podpisu, jednání a úmrtí se ponechávají.
- Jména hledá rozšířený slovník tvarů běžných křestních jmen a právní kontext. Zahrnuje běžné zápisy verzálkami a příjmení se spojovníkem. Příjmení se sjednocují podle koncovek a některých případů pohyblivého e. Adresy se hledají po slovech jako `bytem` a v běžných zápisech s obcí a číslem domu. Neobvyklé tvary, iniciály, netypické adresy nebo OCR chyby mohou uniknout.
- DOCX používá stejné štítky pro shodné hodnoty a rozpoznané varianty v jedné dávce. Mapa je pouze v paměti. Stejnojmenné osoby se mohou sloučit a číslování se při změně dávky může změnit. PDF používá černé plochy bez štítků.
- PDF znovu sestaví každou stránku z jednoho obrazu při 300 DPI. Detekce používá souřadnice znaků původního textu a podle obsahu stránky i české OCR nad vykresleným obrazem. Začernění mění samotné pixely před vložením obrazu. Původní textová vrstva, odkazy, metadata, anotace a souborové přílohy se nepřenášejí. Kontrola znovu otevře výstup, zkontroluje jeho strukturu a černé pixely v nalezených oblastech. Výstup není prohledávatelný a může být větší než vstup.
- PDF s obrázky, vektorovou grafikou nebo bez čitelného textu vyžadují OCR. U prázdných nebo nečitelných stránek může zpracování souboru skončit chybou. Ručně ověřte zejména skeny, razítka, podpisy, fotografie, rukopis, grafiku, QR a čárové kódy. OCR neumí spolehlivě rozpoznat citlivý obsah v těchto prvcích. GUI zatím nenabízí ruční vyznačování ploch.
- Kontrola ověřuje provedení nalezených náhrad. Úplnost detekce a možnost nepřímé identifikace z kontextu musí posoudit člověk. Vstupy a pracovní výstupy držte v místní složce mimo synchronizované úložiště.

## Testy a sestavení

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$env:SPISREDAKT_TEST_TESSDATA = 'C:\SpisRedakt\tessdata'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name SpisRedakt start.py
```

Testy generují syntetické soubory. GitHub Actions sestavuje balíček na Windows, testuje OCR a ověřuje start zabalené aplikace. Pokyny k dalším iteracím jsou v [AUTOMATION.md](AUTOMATION.md).
