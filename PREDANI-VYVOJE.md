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
- `d9703fa`: chybně přečtená čísla z OCR (`anonymizer/numbers.py`). Orchestrátor při kontrole našel, že dlouhé číslo ve skenu se zaměněnými písmeny (`604 22l 33O`) zůstávalo, vráceno a opraveno. Kontrola před vydáním teď odmítne i výstup s variantou známé hodnoty o jednu číslici; u spisu to může vést k nevydání celého Markdownu (bezpečné, ale je třeba to sledovat). Testy: 253, OK; `leak_report.py` 0 úniků i v Markdownu.

## 11. Oprava Windows CI (18. 11. 2026)

- Windows CI na větvi `claude/orchestrace-vyvoje` selhávalo od 17. 11. dopoledne. Orchestrátor po pushi nekontroloval výsledek CI. Kontrola je nově povinný krok v `.claude/agents/orchestrator.md`.
- Příčiny a opravy:
  - Test rad v GUI používal nerozvinuté cesty (na Windows krátké názvy 8.3). Opraveno v testu.
  - **Skutečné úniky na všech platformách:** pravidla nepoznala údaje zapsané s nezlomitelnou mezerou, měkkým spojovníkem nebo neviditelnými znaky („Jan\xa0Novák“, „850314\xad2010“). `Rules.find` teď hledá v normalizované kopii a pozice převádí zpět (`4565726`).
  - Textová vrstva PDF s písmem Arial vrací místo mezery a spojovníku U+00A0 a U+00AD. Extrakce je převádí (`PDF_LAYER`).
  - OCR na Windows čte „@“ v e-mailu na skenu jako „(A“, „©A“, „ W“ nebo jako znak navíc před „@“. Pravidla tyto podoby poznají (`a816e4d`, `7de407d` a následující commit).
  - Měřicí skript padal na kódování konzole cp1252. Výstup je nově v UTF-8.
- Ověřování bez Windows: simulace (podvržené `get_text` s U+00A0/U+00AD a písmo Liberation Sans místo DejaVu) a diagnostické výpisy syntetických dat v testech při selhání.
- Výsledek: Windows CI zelené (259 testů, sestavení a ověření balíčku).

## 12. Vydání 0.9.0 a regresní korpus (18. 11. 2026 večer)

- PR #4 do `main` sloučen (`a691312`) na pokyn vlastníka, ten orchestrátora pověřil i rozhodováním. Vydání spuštěno workflow Release z `main` a vzniklo předběžné vydání `v0.9.0` s balíčkem `SpisRedakt-v0.9.0-Windows-x64.zip`.
- Před použitím na skutečných spisech zbývá ruční zkouška vlastníka na Windows.
- Regresní korpus (`6fc8c33`, `76e50a9`): 276 testů. Simulace Windows (`tests/winsim.py`), písma (Liberation, serif, kurzíva, tučné, 7–18 pt, šedý text, otočené skeny), zalomení v PDF, skenu a DOCX, smíšené PDF, soudní šablony.
- Nalezené a opravené úniky:
  - hodnoty zalomené přes řádek (rodné číslo, účet, příjmení se spojovníkem),
  - adresa po „e-mail“ bez čitelného „@“,
  - zkomolená adresa v otočeném skenu na Windows. Řešení platí jen pro text z OCR: po „e-mail“ se nahradí úsek až k čárce, středníku nebo konci řádku, pokud obsahuje zbytek „@“.
- Cena: druhý průchod přes spojené řádky zpomalí text s mnoha zalomeními asi 1,8×. Proti OCR je to zanedbatelné.
- Kontrola v LibreOffice (`outputs.yml`) dvakrát skončila na limit 20 min, protože na runneru visel `apt-get install`. Jde o infrastrukturu, ne o kód. Případná úprava workflow (limit nebo opakování `apt`) vyžaduje lidskou kontrolu.

## 13. Úspora tokenů (18. 11. 2026)

- Denní běh v 11:20 zakládá vždy novou session (plánovaná úloha „SpisRedakt – denní běh orchestrátora (nová session)“, model Sonnet). Původní úloha, která budila jednu dlouhou session, je vypnutá.
- Orchestrátor běží na Sonnetu (`.claude/agents/orchestrator.md`, oddíl „Šetření kontextu a tokenů“). Vícekrokovou práci včetně pushe a oprav Windows CI dělá vývojář. U bezpečnostně citlivých změn se spouští krátká kontrola modelem Opus.
- Nová session začíná z tohoto souboru a `BACKLOG.md`. Zapisuj sem jen to, co další běh potřebuje.

## 14. Návrh rozhraní: náhled a ruční vyznačení (24. 11. 2026, čeká na schválení)

**Náhled v GUI.** Po zpracování se v seznamu souborů zpřístupní tlačítko „Náhled“ (jen u PDF, obrázků a formátů převoditelných na text).
- Okno s dvěma panely vedle sebe: vlevo originál, vpravo výstup, stejná stránka, posuv synchronizovaný. Navigace: předchozí/další stránka, číslo stránky, přiblížení.
- Stránky se vykreslují na požádání (PyMuPDF → `QPixmap`) z originálu a z výstupu; na disk se nic neukládá, vše jen v paměti a po zavření okna se zahodí. U textových formátů se vpravo ukáže text výstupu se zvýrazněnými štítky `[DRUH-n]`, vlevo originální text.
- Originál se čte jen z už vybraného zdroje; žádné nové kopie ani cesty v hlášeních. Bez sítě.

**Ruční vyznačení ploch.** Rozšíření téhož okna o režim „Doplnit začernění“ (jen PDF a rastrové obrázky).
- Myší se v panelu originálu nakreslí obdélníky; seznam obdélníků (strana, souřadnice, bez textu) je jediný nový stav. Tlačítka: smazat poslední, zrušit vše.
- „Použít“ znovu zpracuje soubor: obdélníky se přidají k automatickým začerněním jako další `redact` plochy a pod nimi se odstraní text i pixely (stejná cesta jako dnes pro OCR/obrázky). Výstup se znovu ověří kontrolou před vydáním a detektorem úniků; při selhání se soubor nevydá.
- Audit uloží jen počet ručních ploch na stranu, ne souřadnice ani obsah.

**K rozhodnutí:** (1) zda náhled smí zobrazit originál vedle výstupu (zobrazuje osobní údaje na obrazovce); (2) zda se ruční plochy mají ukládat do mapy dávky, nebo platit jen pro jedno zpracování (doporučeno: jen jedno zpracování).

## 15. Jednotné pseudonymy mezi dávkami (c9088d8)

`anonymizer/pseudonyms.py`: mapa `.spisredakt-mapa` a klíč `.spisredakt-klic` ve výstupní složce, HMAC-SHA256 otisky, DPAPI přes ctypes (v testech vyměnitelná `pseudonyms.protection`). Poškozená mapa nebo chybějící klíč: mapa se nepoužije ani nepřepíše. Skutečné DPAPI ověří jen test na Windows CI.

## 16. Převod DOC/XLS přes LibreOffice (7b167dd)

`legacy.to_modern`: volba `USE_OFFICE` z GUI, `soffice` jen z pevných cest, dočasný profil bez maker a aktualizace odkazů, časový limit, při selhání vlastní převod. COM zůstává jako `[?]` v BACKLOG.md.

## 14a. Rozhodnutí: Náhled v GUI (25. 11. 2026, Opus extra high z pověření vlastníka)

Schváleno s úpravou. Důvod: náhled nemění výstupy a zlepšuje ruční kontrolu; originál vedle výstupu je přípustný (README už ukládá porovnání s originálem), pokud se otevře jen výslovnou akcí a nic nejde na disk. GUI nemá seznam souborů, hlavní výstup je Markdown a vstupem bývá ZIP, proto zadání níže.
- `anonymizer/preview.py` (nový), úpravy `gui.py`, `extract.py`, `batch.py`, `reasons.py`, testy, README („Rozsah zpracování“, „Práce se spisem“ krok 3).
- `extract.Document.locator` (výchozí `None`): (input_id, řetězec pořadí položek ZIP) počítaný jako v `redact_archive` (všechny položky kromě adresářů, i šifrované a odmítnuté). Přílohy e-mailů a vložené obrázky `None`.
- `process_folders(catalog=)`: nový volitelný callback (ne `summary`), jen v paměti: pro každé `Dn` formát, počet stran, rodič, lokátor; vstup s podpisem (size+mtime_ns); cesta k Markdownu (soubor/složka); cíle kontrolních kopií.
- Tlačítko „Náhled“ v hlavním okně: aktivní jen po dávce dokončené v tomto spuštění a když dávka neběží; při otevřeném náhledu `scan()` nespouští dávku. PyMuPDF se nesmí používat ve dvou vláknech současně.
- Modální `QDialog` „SpisRedakt – náhled“: vlevo seznam „D3 (PDF, 2 s.)“, „D7 – nezpracováno“ bez názvů souborů; uprostřed originál (popisek „Originál – obsahuje osobní údaje“), vpravo výstup; předchozí/další strana, číslo strany, přiblížení. Bez ukládání, tisku a exportu; nic na disk (žádné dočasné soubory, mezipaměť, QSettings); po zavření vše zahodit.
- Originál: jen PDF, TIFF, PNG, JPEG, BMP (i v ZIP), formát přes `formats.identify` z bajtů; položky ZIP do paměti s limity `size_limit`, `_Limited`, `MAX_ZIP_DEPTH`; vykreslit jen aktuální stranu ze `stream=`, `annots=False`, stejné otočení a dekódování jako při zpracování, limit `MAX_PIXELS`. Ostatní formáty: pevný text.
- Výstup: sekce `## Dn` vydaného Markdownu načtená z disku (u „Rozdělit po dokumentech“ `D000n.md`), jen čtení, štítky zvýrazněné, posun podle `[s. N]`; přepínač „Kontrolní kopie“ jen kde existuje (u ZIP položka `spis_kontrola*.zip` podle pořadí, čtení v paměti).
- Okrajové případy, vše pevná hláška z `reasons.py`: změněný/zmizelý vstup (podpis), nevydaný Markdown (originál se dál ukáže), nezpracovaná položka (rada z `ADVICE`), šifrované/poškozené PDF, strana nad limitem, jakákoli výjimka při vykreslení. Nic do logu ani auditu.
- Testy: syntetický ZIP (adresář, šifrovaná, nepodporovaná položka, PDF se jménem, vnořený ZIP s TIFF, PNG, DOCX, EML s PDF přílohou) – vnořená strana odpovídá přímému vykreslení; žádný text dialogu neobsahuje název/cestu/jméno, pravý panel štítek; při otevření/listování/zavření selžou podvržené zápisové `open`, `Path.write_*`, `tempfile.*`, a náhled přesto funguje, QSettings/výstup/audit beze změny; `scan()` při otevřeném náhledu nespustí BatchWorker; DOCX, příloha e-mailu a změněný originál dají pevnou hlášku; Markdown, kontrolní kopie a `events.jsonl` totožné s katalogem i bez; `preview.py` do `test_surfaces_stay_in_memory`.
- Nesmí se změnit: obsah výstupů, auditu, mapy pseudonymů, kontrola před vydáním. Žádná nová závislost (jen QtWidgets/QtGui/QtCore, PyMuPDF; ne QtPdf, QtWebEngine, Pillow), žádné úpravy `requirements*.txt` ani `.github/`.

## 14b. Rozhodnutí: Ruční vyznačení ploch (25. 11. 2026, Opus extra high; až po hotovém Náhledu)

Schváleno s úpravou. Důvod: plochy jen přidávají začernění, ale musí platit i pro Markdown (jinak falešný pocit bezpečí) a fáze sběru osob nesmí přijít o text pod plochou (jinak únik samotného příjmení jinde).
- Rozsah: jen dokumenty zobrazené jako stránky (PDF, TIFF, PNG, JPEG, BMP, i v ZIP); obrázky v DOCX/ODT/PPTX/RTF/EML a přílohy e-mailů ne (uvést v README jako omezení).
- Ovládání: režim „Doplnit začernění“ (tažení myší na originálu, černá plocha s okrajem), „Smazat poslední“, „Zrušit vše“, „Použít“; pevný text „Ruční plocha platí jen pro toto místo, stejný údaj jinde ve spisu neodstraní.“
- Stav jen v paměti: lokátor+podpis vstupu → strana → obdélníky normalizované 0–1 vůči zobrazené straně (po /Rotate, u obrázku vůči dekódovanému obrazu), ořez na stranu, plochy <2×2 px ignorovat. Nikdy na disk, do QSettings, mapy, auditu, logu. Zahodit při zavření aplikace, změně složek, „Zrušit vše“ a spuštění jiné dávky (jedna pevná věta do logu).
- „Použít“: zavře náhled a znovu spustí celou poslední dávku (stejný výběr, aktuální volby); `manual_areas` do `process_folders` → `collect_documents`/`extract` → `redact_file` (`redact_pdf`, `redact_scan`, `redact_image`, i v `redact_archive`). Nikdy jeden dokument s novými `Rules`. Nové výstupy s příponou `_2`, starší se nepřepisují; věta do logu: „Ruční plochy platí jen pro toto zpracování. Starší výstup bez nich zůstal ve výstupní složce, nesdílejte ho.“ Po doběhnutí jde náhled otevřít s plochami zachovanými.
- Markdown: strana s plochou jen z obrazu, plochy se začerní před OCR (okraj 2 px jako `blackout`), textová vrstva ani OCR obrázků té strany do výstupu nejdou; bez dat OCR dokument nezpracovat (`ocr_data_missing`). Fáze sběru osob (`md.collect` → `Rules.collect`) dál dostává původní text včetně textu pod plochou (např. `Page.collect_only`, `repr=False`), do výstupu nikdy.
- Kontrolní kopie: `redact_pdf` (i přes `redact_scan`) začerní plochy v pixmapě před OCR a přidá do `expected` pro `validate_pdf`; `redact_image` po zakódování ověří, že plocha zmenšená o 2 px je černá (JPEG s tolerancí doloženou testem), jinak `ValueError`.
- Nesoulad (změněný podpis, lokátor bez položky, neexistující strana, nestránkový formát, jiný rozměr/orientace obrazu mezi Markdownem a kopií – EXIF): dokument se nevydá, v Markdownu nezpracováno, v kopii odmítnut, kód `manual_area_mismatch` (+ text a rada v `reasons.py`), ostatní dokumenty pokračují. Audit: jen počet ploch na `input_id` (`manual_areas`) a kód; žádné souřadnice ani čísla stran.
- Testy: PDF se slovem, které pravidla nenajdou, plocha přes něj (chybí v Markdownu i OCR kopie, pixely černé, `validate_pdf` projde, slovo mimo plochu zůstane); totéž pro /Rotate 90, posunutý CropBox, 2. stranu TIFF, PNG a JPEG s EXIF orientací 6 (samostatně i ve vnořeném ZIP); celé jméno jen pod plochou + samotné příjmení jinde (příjmení se dál nahradí a kontrola ho zná); změněný vstup → `manual_area_mismatch`, zbytek se vydá; bez ploch jsou výstupy totožné s Náhledem; souřadnice nikde na disku, jiná dávka plochy zahodí; kreslení dává správné normalizované souřadnice i při přiblížení, „Smazat poslední“/„Zrušit vše“.
- Nesmí se změnit: vše z 14a; plochy jen přidávají (automatická pravidla, kontrola před vydáním, `validate_pdf`, detektor úniků beze změny); formát mapy; existující výstupy se nepřepisují. Bezpečnostně citlivé: před commitem krátká kontrola Opus, nezávislé ověření OCR na syntetické slovo, zelené Windows CI.
- Pověření vlastníka: viz AUTOMATION.md.
