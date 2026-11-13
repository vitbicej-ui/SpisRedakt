# SpisRedakt 0.1.0

Offline aplikace pro Windows pro dávkové zpracování DOCX a PDF. Vznikla nezávisle s funkční inspirací z [veřejného popisu SKRYI](https://www.skryi.cz/index.html). Licence projektu je AGPL-3.0-only.

## Spuštění

Rozbalte celý balíček pro Windows a spusťte **SpisRedakt.exe**. Vedle programu je složka `tessdata` s českým modelem. Program nemá síťové funkce. Instalace závislostí při sestavení ze zdrojů vyžaduje internet nebo místní balíčky.

Ze zdrojů v PowerShellu s Pythonem 3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe start.py
```

Pro OCR ze zdrojů stáhněte [ces.traineddata 4.1.0](https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/ces.traineddata) a v aplikaci vyberte jeho složku. Pokud knihovna neotevře cestu s diakritikou, přesuňte složku například do `C:\SpisRedakt\tessdata`.

## Práce se spisem

1. Vyberte všechny DOCX a PDF stejného spisu v jedné dávce.
2. Přidejte vlastní výrazy. Každá osoba nebo údaj má jeden řádek. Různé tvary zadejte např. jako `Testovací Osoba | Testovací Osoby`. Stejným způsobem lze zadat adresy nebo jiný citlivý text.
3. Vyberte místní výstupní složku. Program vytvoří novou podsložku `KE-KONTROLE-…` a použije neutrální názvy. V aplikaci spárujete výstupy se vstupy podle pořadových čísel. Při chybě u souboru dávka pokračuje a vadný výstup odstraní.
4. Porovnejte každý výstup s originálem, včetně všech stran, příloh a významu ponechaného textu. Citlivé výrazy, které chyběly v pravidlech, doplňte a zpracujte celý spis znovu. Ke sdílení použijte až ručně zkontrolované výstupy.

## Rozsah první verze

- DOCX vytváří nový soubor z textu odstavců hlavního dokumentu, tabulek, hlaviček, patiček, poznámek pod čarou a vysvětlivek. Zahrnuje vložený text sledovaných změn a vypouští smazaný text. Ztrácí původní sazbu, strukturu tabulek, textová pole, obrázky, komentáře, metadata zdroje a přílohy. Výstup znovu prochází stejnými pravidly.
- Pravidla hledají e-maily, tvary českých telefonů, rodných čísel, účtů, CZ IBAN, DIČ, označených IČO a dokladů a datum ve tvaru den.měsíc.rok. Jde o detekci tvaru a může zachytit i jiné údaje stejného formátu. Datum může být libovolné datum v dokumentu. Jména, adresy a jejich skloňování vyžadují vlastní výrazy. Zápisy rozdělené mezi odstavce, netypické zápisy a OCR chyby mohou uniknout.
- DOCX používá stejné štítky pro shodné hodnoty nebo zadané tvary v jedné dávce. Mapa je pouze v paměti. Stejnojmenné osoby se sloučí a číslování se při změně dávky může změnit. PDF používá černé plochy bez štítků.
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
