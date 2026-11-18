# Předání vývoje SpisRedakt – podklad pro řídicího AI klienta

Stav k 17. 11. 2026. Tento soubor je pro AI klienta, který převezme kontrolu postupu vývoje a jeho řízení („mozek“ vývoje). Popisuje, jak vývoj probíhá, co je hotové, jak to bylo ověřeno, kde jsou rizika a co čeká na rozhodnutí.

## 1. Projekt v kostce

- **SpisRedakt** je offline anonymizér českých soudních spisů pro Windows (Python 3.13, PySide6, PyMuPDF, lxml), licence AGPL-3.0-only.
- Zpracuje DOCX/XLSX/PPTX, ODT/ODS/ODP, RTF, DOC/XLS (převod na DOCX/XLSX), EML/MSG, PDF a skeny (OCR), TIFF, obrázky, textové formáty a ZIP.
- Osobní údaje nahrazuje štítky `[DRUH-n]`, v PDF začerňuje pixely. Před vydáním znovu kontroluje výstup. Když údaj nejde spolehlivě odstranit, soubor nevydá.
- Repozitář: `vitbicej-ui/SpisRedakt`. Aktuální verze na pracovní větvi je **0.8.2**. Na `main` je vydaná **0.8.1**.

## 2. Nepřekročitelná pravidla (z `CLAUDE.md`)

1. Nikdy nepracovat se skutečnými dokumenty ani osobními údaji. Testovací data vznikají synteticky přímo v testech.
2. Aplikace nesmí mít síťové funkce. Nová závislost smí přibýt jen s licencí kompatibilní s AGPL, s připnutou verzí v `requirements.txt` a se záznamem v `THIRD-PARTY.md`. Vždy vyžaduje schválení člověkem.
3. Bezpečnost má přednost před pohodlím. Co nejde spolehlivě odstranit, se nevydá (`ValueError` s pevným českým textem) nebo se začerní.
4. Chybová hlášení nesmí obsahovat obsah, název ani cestu dokumentu. Každé nové hlášení patří do `anonymizer/reasons.py` a nově i s radou do `ADVICE`.
5. Audit ukládá jen pořadí, formát a kódy.
6. README, hlášení a dokumentace se píšou česky, kód a commity anglicky. Každá změna chování potřebuje test v `tests/test_processing.py` a odstavec v README (sekce „Rozsah zpracování“).
7. Změny workflow (`.github/`), oprávnění a závislostí vyžadují lidskou kontrolu.

## 3. Jak vývoj probíhá

- **Role:**
  - Orchestrátor (`.claude/agents/orchestrator.md`, Opus) bere úkoly z `BACKLOG.md` shora. Píše samostatná zadání, kontroluje diff, spouští testy, commituje, pushuje a aktualizuje backlog.
  - Implementaci dělají vývojáři (`.claude/agents/developer.md`, **povinně model Sonnet**, `subagent_type: "developer"`). Vývojáři necommitují.
- **Větve:**
  - Orchestrátor pracuje výhradně v `claude/orchestrace-vyvoje`.
  - Do `main` se nikdy nepushuje. Do `main` se slučuje jen přes PR, které otevírá a schvaluje člověk.
  - Na začátku každého běhu se do pracovní větve slučuje `origin/main`.
- **Štítky a vydání:**
  - Vytváří je jen člověk nebo nadřazená session.
  - Workflow `release.yml` vydává podle štítku `v*`, který musí odpovídat `VERSION` v `anonymizer/audit.py`.
- **Verze:** po každých třech dokončených úkolech se zvýší patch verze v `anonymizer/audit.py` a v nadpisu README.
- **Stav úkolů v backlogu:** `[ ]` čeká, `[~]` rozpracováno, `[x]` hotovo (s hashem commitu), `[?]` čeká na rozhodnutí člověka.
- **Plánovaný běh:** naplánovaná úloha budí orchestrátora denně v 11:20 (Europe/Prague) s pokynem zpracovat nejvýše tři položky. Po každém běhu nechá shrnutí.

### Kontrolní postup u každé položky (orchestrátor)

1. Zkontrolovat `git diff`: správnost, soukromí (žádný obsah dokumentu v hlášeních ani v auditu) a rozsah změny.
2. Spustit celou sadu testů včetně OCR:
   ```bash
   export SPISREDAKT_TEST_TESSDATA=$PWD/tessdata QT_QPA_PLATFORM=offscreen
   python -m unittest discover -s tests
   ```
3. Spustit `python scripts/check_outputs.py`. Skript převede syntetické výstupy přes LibreOffice a musí skončit hláškou „Všech 7 výstupů LibreOffice otevřel bez osobních údajů.“
4. Kde to jde, ověřit změnu nezávisle, nejen testy vývojáře. Například diferenční porovnání se starou verzí nebo soubor vytvořený LibreOffice.
5. Výhrady vrátit témuž vývojáři s konkrétním popisem. Po úspěchu udělat jeden commit, push a záznam v backlogu.

### Příprava prostředí (Linux)

```bash
pip install -r requirements-build.txt
apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1
apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc
bash scripts/get-ocr.sh
```

## 4. Hotovo v tomto běhu (větev `claude/orchestrace-vyvoje`)

Všech pět položek sekce „Robustnost u skutečných spisů“. Na konci běhu prošlo **45 testů** a `check_outputs.py` hlásil OK. Na začátku běhu bylo 31 testů.

| Commit | Položka | Podstata změny | Jak ověřeno |
|---|---|---|---|
| `8e048ac` | Důvod chyby v GUI | `reasons.py`: přibyly `ADVICE` (kód → česká rada), `FORMAT_MESSAGES` a `advice(message)`. `gui.py` pod chybový řádek vypíše `    Rada: …`. Kódy auditu a `reason_code` se nezměnily. | Test, že každý kód z `REASONS` a každý `FormatError` ve zdrojích má konkrétní radu. GUI test přes `BatchWorker.run()` ověřuje, že rada neobsahuje název souboru. |
| `d0519bc` | Paměť u velkých vstupů | Dávka nejdřív zkontroluje velikost (`fstat`) a hlavičku. Neznámé soubory přeskočí bez kopírování. Ostatní zkopíruje po 1 MB do soukromé dočasné složky (`copy_limited`) a formát pozná z kopie (`identify` přijímá `bytes` i `Path`). Soubory bez signatury (text, EML) se kopírují jen do limitu své přípony. | Test paměti přes tracemalloc (špička pod 8 MB), testy s řídkými soubory 600 MB a 300 MB a test shody `identify` pro cestu a pro bajty. |
| `df4a646` | Výkon pravidel | Regexy s `re.IGNORECASE` zkoušely každou pozici textu. Kotvící skenery (`anchored`, `colon_lines`, `company_matches`, `email_matches`, `rc_matches`, `initial_last_matches`) teď najdou možné začátky a nezměněný vzor pustí jen tam. Zrychlení je 1,7–2,8× a výstupy jsou bitově shodné. | Hash test deterministického textu (hash z původní implementace), testy skenerů proti `finditer` a test pokrytí Unicode casefold. Orchestrátor nezávisle porovnal 8 000 náhodných textů se starou verzí: shodné. |
| `06ae5ce` | Verze 0.8.2 | Po třech úkolech. | – |
| `c78c7ab` | Přesné tabulky v DOC | `legacy.py` čte PlcBtePapx a PapxFkp (`sprmPFInTable`, `sprmPFTtp`, `sprmPItap`, `sprmPFInnerTtp`). Víceodstavcové buňky zůstanou celé a vnořené tabulky se zploští. Při nečitelných vlastnostech nebo při Prm na úrovni kusu (orchestrátor odstranil neověřené mapování isprm) se použije původní heuristika. | Test zachování všech viditelných znaků a test poškozené PlcBtePapx. DOC vytvořený LibreOffice prošel novou cestou se správnou strukturou. |
| `7561d5e` | Nadpisy sloupců v RTF | `rtf.py` sleduje strukturu tabulek napříč skupinami (třída `Tables`). Na tabulky použije `plan_table` a celou buňku z plánu nahradí jedním štítkem. Sousední tabulky bez odstavce mezi nimi se plánují i po úsecích stejného rozložení a výsledek je sjednocení plánů (konzervativně). Vnořené tabulky hlídají jen textová pravidla. | Orchestrátor našel chybu na RTF z LibreOffice (dvě sousední tabulky se slily a jméno pod nadpisem zůstalo) a vrátil ji k opravě. Po opravě přibyl regresní test a ruční ověření. |

Dále byl sloučen `origin/main` (merge `0d21faf`): 0.8.1, `outputs.yml`, `release.yml`, `scripts/check_outputs.py`.

## 5. Známá rizika a místa pro kontrolu

- **Víceřádkové nadpisy sloupců:** buňka „Jméno“ / „a příjmení“ se nerozpozná jako nadpis v DOC, DOCX, ODT ani RTF. Hodnoty pod ní hlídají jen textová pravidla, takže jméno mimo slovník může projít. Je zařazeno v backlogu jako nejvyšší otevřená položka.
- **Kotvící skenery (`df4a646`):** jsou složité a jejich správnost stojí na důkazu nadmnožiny začátků. Při každé změně regexů v `czech.py` nebo `rules.py` je nutné upravit i kotvy. Rozbití hlídají testy `test_replace_output_is_stable`, `test_fast_scanning_finds_what_the_patterns_find` a `test_anchor_letters_cover_ignorecase`.
- **DOC a PAPX:** formát FKP je implementovaný podle znalosti specifikace [MS-DOC], ne přímo z ní. Ověřeno na syntetických DOC a na DOC z LibreOffice, ne na DOC z MS Word. Rychle uložené DOC (Prm) používají heuristiku.
- **RTF tabulky:** sloupec je pořadí `\cell` v řádku a `\cellx` se pro sloupce nepoužívá. Sloučené buňky mohou posunout sloupce.
- **Rady v GUI:** texty napsal vývojář. Doporučuji jazykovou a věcnou revizi od člověka z praxe.
- **Druhý průchod `Rules.replace`:** zůstal kvůli bezpečnosti. Odstranit ho jde jen s důkazem shody.
- **`Rules.label`:** u iniciál prochází všechny štítky, takže při tisících osob roste čas kvadraticky. Zatím neřešeno.
- **Členy ZIP a přílohy e-mailů:** pořád se načítají do paměti (`archive.read`). Streamování řeší jen vstup dávky.

## 6. Backlog po tomto běhu (pořadí = priorita)

1. `[ ]` Víceřádkové nadpisy sloupců (nová položka z tohoto běhu).
2. `[ ]` Regresní korpus: smíšené skeny, obtížná písma, zalomení řádků, soudní šablony včetně doložky „Shodu s prvopisem potvrzuje“.
3. `[ ]` Náhled v GUI: originál a výstup vedle sebe, bez ukládání kopií.
4. `[ ]` Ruční vyznačení ploch v náhledu PDF nebo obrázku.
5. `[ ]` Jednotné pseudonymy mezi dávkami (varianta B: HMAC-SHA256 otisky a klíč chráněný Windows DPAPI přes ctypes, bez nové závislosti).
6. `[ ]` Věrný převod DOC/XLS přes nainstalovaný Word nebo LibreOffice (volba v GUI, výchozí vypnuto). Nová závislost, například pywin32, se musí označit `[?]`.

Kontrolu výstupů v LibreOffice (`outputs.yml`) a vydání podle štítku (`release.yml`) člověk schválil 17. 11. 2026. Skript `check_outputs.py` smí orchestrátor rozšiřovat (PPTX/ODP, e-maily). Workflow měnit nesmí.

## 7. Čeká na rozhodnutí člověka

- **Položky 5 a 6 backlogu:** v BACKLOG.md jsou zapsané jako rozhodnuté 17. 11. 2026. Pokyn ale přišel od nadřazené session, ne přímo od vlastníka, a orchestrátor ho v tomto běhu nezačal. Před jejich zahájením ať vlastník potvrdí, že rozhodnutí platí.
- **Revize rad v GUI** (viz výše).
- **PR z `claude/orchestrace-vyvoje` do `main`** a případné vydání 0.8.2 nebo 0.9.0: štítek a vydání dělá člověk. Nadřazená session navrhla pro příští vydávanou verzi číslo 0.9.0.

## 8. Doporučení pro řídicího klienta

- U každé položky trvat na nezávislém ověření mimo testy vývojáře: diferenční porovnání u refaktoringů, reálné soubory z LibreOffice nebo Wordu vytvořené synteticky, kontrola výstupu na zbylé údaje.
- U nejistých formátových detailů (binární formáty, Prm, RTF) volit konzervativní variantu: návrat ke staré cestě, sjednocení plánů nebo nevydání souboru.
- Hlídat, aby nové kroky neobcházely pravidla: každý text, který jde do výstupu, musí projít `Rules` a závěrečnou kontrolou výstupu.
- Před zahájením položek, které mění GUI (náhled, ruční vyznačení), nechat člověka odsouhlasit návrh rozhraní.

## 9. Běh 17. 11. 2026 večer: produkční cíl „spis do cloudového modelu“

Pořadí podle sekce „Produkce“ v BACKLOG.md. Commity:

| Commit | Obsah |
|---|---|
| `16f935c` | `anonymizer/extract.py`: extrakce textu ze všech formátů (proudově z ZIPu), `Document`/`Skipped` bez názvů souborů, text mimo `repr`. |
| `d34ded5` | Samotná příjmení: rodiny příjmení (pád, rod, diakritika), `pan/paní` a role + jedno slovo, `COMMON_SURNAMES`, výjimky pro soudy/obce, `Rules.collect`, `Rules.surfaces`. |
| `473dd51` | `tests/synthetic_spis.py` (15 položek, 96 hodnot), `anonymizer/leaks.py` (nezávislý detektor), `scripts/leak_report.py`. |
| `278f156` | `anonymizer/markdown.py`: dvoufázové štítkování, krátké štítky, legenda, obsah, deterministické úspory. |

Ověřeno orchestrátorem mimo testy vývojářů: ruční příklady příjmení (pády, rody, verzálky, bez diakritiky, „Malý rozdíl“, soud v Kolíně); detekce záhlaví ztrácela větu s číslem strany – vráceno a opraveno.

## 10. Běh 18. 11. 2026 (denní běh)

- `af3e40f`: měřicí skript `scripts/benchmark_spis.py`, proudové zpracování položek kontrolního ZIPu, cache `given_identities`, rychlé začernění, předfiltr detektoru úniků. Plný syntetický spis (200 MB, 300 stran skenů, 107 dokumentů) zpracuje Markdown za 4 min se špičkou 475 MB, odhad tokenů klesl z 261 tis. na 170 tis.
- Předfiltr v `leaks.py` (detektor slouží i jako kontrola před vydáním Markdownu) orchestrátor nezávisle ověřil diferenčním porovnáním se starou verzí: 20 000 náhodných textů se zkomoleninami, 0 rozdílů.
- Nové riziko: chybně přečtené číslo z OCR projde i do Markdownu (viz backlog „Chybně přečtená čísla z OCR“).
- Testy: 243, OK. `check_outputs.py` OK. `leak_report.py`: 0 úniků.
