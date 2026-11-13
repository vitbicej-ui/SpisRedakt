# SpisRedakt 0.3.0

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

1. Vyberte vstupní složku s dokumenty jednoho spisu. Program zpracuje DOCX, XLSX, PPTX a PDF i v podsložkách. Ostatní formáty přeskočí.
2. Vyberte samostatnou výstupní složku a tím spusťte dávku. Název `Smlouva.docx` se změní na `Smlouva_anonymizovano.docx`. Struktura podsložek zůstává zachována. Originály i existující výstupy zůstanou beze změny. Název souboru může obsahovat osobní údaj, proto zkontrolujte také názvy před sdílením.
3. Porovnejte každý výstup s originálem, včetně všech stran, příloh a významu ponechaného textu. Případné přehlédnuté údaje odstraňte před sdílením.

## Rozsah první verze

- DOCX, XLSX a PPTX zachovávají strukturu odstavců, tabulek, buněk a snímků a jejich styly. Štítek převezme formát začátku nahrazeného textu. Délka štítku může ovlivnit zalomení nebo výšku řádků. Dokumenty s obrázky, grafy, přílohami, makry nebo externími odkazy se mohou odmítnout. Kontrolujte i skryté listy, názvy, vzorce a číselné buňky. Automatická pravidla pro strukturované datum nebo identifikátor v samostatné buňce podle nadpisu sloupce zatím chybí.
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
