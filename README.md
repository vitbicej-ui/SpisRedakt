# SpisRedakt 0.8.0

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

1. Vyberte vstupní složku s dokumenty jednoho spisu. Program zpracuje DOCX, XLSX, PPTX, ODT, ODS, ODP, RTF, PDF a další formáty níže, i v podsložkách. Ostatní formáty přeskočí.
2. Vyberte samostatnou výstupní složku a tím spusťte dávku. Název `Smlouva.docx` se změní na `Smlouva_anonymizovano.docx`. Struktura podsložek zůstává zachována. Originály i existující výstupy zůstanou beze změny. Název souboru může obsahovat osobní údaj, proto zkontrolujte také názvy před sdílením.
3. Porovnejte každý výstup s originálem, včetně všech stran, příloh a významu ponechaného textu. Případné přehlédnuté údaje odstraňte před sdílením.

## Audit dávky

Po každé dávce aplikace zobrazí cestu k vlastní složce s průběžným protokolem `events.jsonl` a závěrečným `souhrn.txt`. Výchozí složka `audit` leží vedle aplikace. Chcete-li audity zpřístupnit Codexu i při spuštění aplikace z jiné složky, nastavte před spuštěním proměnnou `SPISREDAKT_AUDIT_DIR` na místní zapisovatelnou složku v pracovním prostoru. Při chybě při založení auditu aplikace zkusí místní dočasnou složku. Protokol obsahuje pořadová čísla, formáty a kódy výsledků bez obsahu, názvů a cest zpracovaných dokumentů. Opakovaná dávka přidá k existujícímu výstupu příponu `_2`, `_3` atd.

## Rozsah zpracování

- RTF zachovává řídicí slova, a tedy formátování. Nahradí se jen viditelné znaky v odstavcích, buňkách, poznámkách, záhlavích a výsledcích polí. Vlastnosti dokumentu, autoři komentářů a záložní kopie obrázků se odstraní a odkazy se znefunkční. Obrázky PNG a JPEG projdou OCR. Jiné obrázky, binárně vložená data a vložené objekty zpracování zastaví, stejně jako citlivý údaj v záložce nebo rejstříku. Tabulky v RTF se kontrolují jen pravidly pro text, bez nadpisů sloupců.
- ODT, ODS a ODP zachovávají styly. Text se nahrazuje napříč úseky formátování, buňky tabulek se řídí nadpisy a popisky jako u Office a u data v ODS se odstraní i skrytá hodnota buňky. Náhled, nastavení rozhraní a osobní metadata se nevydávají a odkazy se znefunkční. Vložené objekty, makra, podpisy, šifrování a obrázky mimo PNG a JPEG zpracování zastaví.
- Starší DOC a XLS (Office 97-2003) se převedou na DOCX a XLSX a dál se zpracují jako moderní Office (`Smlouva.doc` → `Smlouva_anonymizovano.docx`). Převádí se jen text, tabulky a hodnoty buněk. Formátování, vzorce, komentáře a makra se nepřevádějí. Obrázky z DOC se vynechají a v textu je nahradí značka `[OBRÁZEK VYNECHÁN]`. Kódy polí (například cíle odkazů) se zahodí a ponechá se jen jejich zobrazený výsledek. Poznámky pod čarou, záhlaví, zápatí, komentáře a textová pole DOC se připojí na konec jako běžné odstavce. Tabulky DOC se rekonstruují přibližně: buňka s více odstavci se spojí do jedné a tabulka může skončit dřív. Zašifrované soubory a Word starší než 97 se nevydají. Výstup pečlivě porovnejte s originálem, protože převod mění vzhled dokumentu.
- E-maily EML zůstávají ve formátu EML. Zprávy Outlook MSG se vydávají jako EML (`Zprava.msg` → `Zprava_anonymizovano.eml`). Ponechá se jen odesílatel, příjemci, předmět a datum. Jména v adresách se nahradí štítkem i mimo slovník a adresy mají tvar `email-1@anonymizovano.invalid`. Ostatní hlavičky (`Received`, `Message-ID`, `X-…`) se nevydávají. Text i HTML tělo projdou pravidly. Z HTML se odstraní skripty a vzdálené obrázky a odkazy se znefunkční. Přílohy se zpracují stejně jako samostatné soubory a dostanou názvy `priloha-0001.pdf` atd. Příloha v nepodporovaném formátu, zašifrovaná zpráva nebo zpráva MSG bez textového těla či s vloženou zprávou Outlooku zpracování zastaví. Elektronický podpis zprávy se odstraní, protože po úpravě by nebyl platný.
- Skeny TIFF, i vícestránkové, se vydávají jako PDF z obrazů (`Sken.tif` → `Sken_anonymizovano.pdf`). Každá strana vždy prochází českým OCR a stejným začerněním a kontrolou jako PDF. TIFF se pozná i podle obsahu, pokud má jinou příponu.
- Dávka přijímá TXT, CSV, TSV, JSON a XML, samostatné PNG, JPEG a BMP a ZIP s textovými soubory nebo vnořenými ZIP. XML s DTD a archivy s jinými položkami se nevydávají. Rozpoznání podle signatury funguje u PDF, ZIP, moderního Office a podporovaných obrázků. Textové formáty používají příponu.
- Kontrola před zveřejněním znovu načte textové výstupy a ověří, že pravidla nenajdou další náhrady. U Office a ZIP ověřuje také čitelnost balíčku. Výsledek kontroly vypovídá o použitých pravidlech a zpracovaných vrstvách. Kontextově nepřímá identifikace a nerozpoznané údaje zůstávají možné.

- DOCX, XLSX a PPTX zachovávají strukturu odstavců, tabulek, buněk a snímků a jejich styly. Štítek převezme formát začátku nahrazeného textu. Délka štítku může ovlivnit zalomení nebo výšku řádků. U běžných externích hypertextových odkazů se cílová adresa nahradí nefunkční adresou. Externě načítaná data či obrázky, makra a vložené přílohy mohou zpracování zastavit. Kontrolujte i skryté listy, názvy, vzorce a číselné buňky.
- Tabulky XLSX, CSV, TSV a tabulky v DOCX a PPTX rozpoznávají sloupce podle nadpisu, například `Jméno a příjmení`, `Datum narození`, `Rodné číslo`, `IČO`, `Číslo OP`, `Telefon`, `E-mail`, `Číslo účtu` nebo `Adresa`. Celá neprázdná buňka pod takovým nadpisem dostane štítek, i když sama hodnotu pravidla nerozpoznají. Datum narození uložené v XLSX jako číslo s formátem data se také nahradí štítkem. Nadpis se musí shodovat celý, sloupce jako `Datum podpisu` nebo `Poznámka` zůstávají beze změny. Sdílený text XLSX z takového sloupce se nahradí ve všech buňkách sešitu. Vzorec ve sloupci s osobními údaji zastaví zpracování k ruční kontrole. V DOCX a PPTX převezme štítek formát prvního úseku buňky a sloučené buňky se počítají podle mřížky tabulky. Jakmile má tabulka známé sloupce, nový řádek nadpisů musí obsahovat jen nadpisy, aby hodnota jako `OP` nepřerušila redakci. Formuláře s popiskem vlevo, například `Jméno | Jan Novák` nebo `Rodné číslo: | …`, se rozpoznají také: s dvojtečkou v jakékoli tabulce, bez dvojtečky v tabulce, jejíž řádky převážně začínají popiskem. Mezery mezi popiskem a hodnotou se přeskočí. V textu se stejně rozpozná řádek `Popisek: hodnota`. U páru s dvojtečkou musí hodnota odpovídat druhu údaje (číslice, `@`, u jména aspoň dvě slova), aby se za hodnotu nepovažoval další nadpis. Tabulky bez nadpisů a popisků automaticky nerozpoznáváme.
- Vložené PNG a JPEG procházejí OCR v plném rozsahu včetně částí schovaných ořezem. Nalezený text se začerní přímo v pixelech a obrázek se znovu uloží bez původních obrazových metadat. Zachová se umístění, velikost a ořez v dokumentu. Pokud OCR nenajde žádný čitelný text, celý obrázek se začerní. Obrázky mohou ztratit průhlednost a OCR může přehlédnout údaje, zejména u malého textu, rukopisu a kódů. Další obrazové formáty zatím vyžadují převod na PNG nebo JPEG.
- Pravidla hledají e-maily, české telefony, rodná čísla, označené bankovní účty a spisové značky, CZ IBAN, DIČ, označená IČO a doklady. Datum narození se rozpoznává podle slov v okolí, například `nar. dne` nebo `datum narození`. Data podpisu, jednání a úmrtí se ponechávají.
- Jména hledá slovník asi 250 běžných českých křestních jmen se všemi pádovými tvary a právní kontext. Pravidelné tvary se odvozují automaticky, nepravidelné a víceznačné jsou zapsané ručně. Tvary, které jsou běžnými slovy (například `Danou`), se vynechávají. Slova jako `Datum` nebo `Rodné` na začátku dalšího řádku se ke jménu nepřipojí. Zahrnuje běžné zápisy verzálkami a příjmení se spojovníkem. Příjmení se sjednocují podle koncovek a některých případů pohyblivého e. Adresy se hledají po slovech jako `bytem` a v běžných zápisech s obcí a číslem domu. Iniciály jako `J. Novák`, `Novák J.` nebo `JUDr. K. Bílá` se rozpoznají v právním kontextu (titul, `svědek`, `žalobce`, `nar.`), u příjmení na `-ová` nebo u příjmení, které se v dávce už objevilo s celým jménem. Pokud dávka zná právě jednu osobu se stejným příjmením a počátečním písmenem, iniciála dostane její štítek. Jinak dostane vlastní. Neobvyklé tvary, netypické adresy nebo OCR chyby mohou uniknout.
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
