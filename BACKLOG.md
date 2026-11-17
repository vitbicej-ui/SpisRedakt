# Backlog

Pořadí je priorita. Orchestrátor bere úkoly shora, stav zapisuje sem: `[ ]` čeká, `[~]` rozpracováno, `[x]` hotovo (s commitem), `[?]` čeká na rozhodnutí člověka.

## Robustnost u skutečných spisů

- [x] **Důvod chyby v GUI.** (8e048ac) U odmítnutého souboru zobrazit srozumitelnou radu podle kódu z `anonymizer/reasons.py` (např. „Odstraňte makra a uložte znovu jako DOCX“).
- [x] **Paměť u velkých vstupů.** (d0519bc) `batch.py` čte celý soubor do paměti (až 500 MB). Rozpoznat formát z hlavičky nebo cesty a soubor kopírovat po blocích.
- [x] **Výkon pravidel.** (df4a646, 1,7–2,8×) `Rules.find` zpracuje asi 1 MB textu za sekundu. Profilovat `contextual_findings` a zrychlit bez změny výsledků.
- [x] **Přesné tabulky v DOC.** (c78c7ab; víceřádkový nadpis buňky se zatím nerozpozná jako nadpis sloupce) `legacy.py` skládá tabulky heuristicky. Použít vlastnosti odstavců (PlcBtePapx, sprmPFInTable), aby se víceodstavcové buňky nerozpadaly.
- [x] **Nadpisy sloupců v RTF tabulkách.** (7561d5e) Napojit `plan_table` na buňky RTF (`\cell`, `\row`).
- [ ] **Víceřádkové nadpisy sloupců.** Nadpis buňky rozdělený do odstavců (např. „Jméno“ / „a příjmení“) se nerozpozná jako sloupec jmen v DOC, DOCX, ODT ani RTF. Normalizovat zalomení v `column_kind`, aby se hodnoty pod takovým nadpisem označily.

## Kontrola výstupů

- [x] **Linuxový CI job s LibreOffice.** `scripts/check_outputs.py` a workflow `outputs.yml` (schváleno 17. 11. 2026). Rozšířit o PPTX/ODP a e-maily může orchestrátor úpravou skriptu, ne workflow.
- [ ] **Regresní korpus.** Rozšířit syntetické testy o smíšené skeny, obtížná písma, zalomení řádků a typické soudní šablony (záhlaví, zápatí, doložka „Shodu s prvopisem potvrzuje“).

## Funkce

- [ ] **Náhled v GUI.** Zobrazit originál a výstup vedle sebe po stránkách, bez ukládání dalších kopií.
- [ ] **Ruční vyznačení ploch.** V náhledu PDF nebo obrázku umožnit doplnit začernění myší a výstup znovu ověřit.
- [ ] **Jednotné pseudonymy mezi dávkami** (rozhodnuto 17. 11. 2026: varianta B). Mapa se ukládá do výstupní složky spisu (`.spisredakt-mapa`). Neobsahuje jména, jen HMAC-SHA256 otisky normalizované identity (`druh + identity`) a k nim štítky a čítače. Klíč HMAC je náhodný, chráněný Windows DPAPI (CryptProtectData přes ctypes, bez nové závislosti) a uložený vedle mapy. Mimo Windows nebo při chybě DPAPI se mapa nepoužije a dávka běží jako dnes. Spojování iniciál se známými jmény musí fungovat i z mapy. Testy: dvě dávky do stejné výstupní složky dají stejné štítky a soubor mapy neobsahuje čitelné identity.
- [x] **Automatické vydání podle štítku.** Workflow `release.yml` (schváleno 17. 11. 2026). Štítek musí odpovídat `VERSION`. Štítky vytváří jen člověk.

- [ ] **Věrný převod DOC a XLS přes nainstalovaný Office** (rozhodnuto 17. 11. 2026: varianta B). Volba v GUI „Převádět DOC/XLS přes Word nebo LibreOffice“, výchozí stav vypnuto, uložená v nastavení aplikace. Když je zapnutá a na počítači je Word nebo Excel (COM přes ctypes/comtypes jen se schválenou závislostí, jinak `soffice --headless --convert-to`, nalezený v obvyklých cestách), převést soubor na DOCX/XLSX v dočasné složce a pokračovat běžnou cestou Office. Bez nalezeného programu nebo při chybě použít dnešní vlastní převod. Žádná síť, žádné makro, převod s vypnutými makry a aktualizacemi odkazů. Novou závislost (např. pywin32) označ `[?]`.

## Hotovo

- [x] 0.8.2: rady k odmítnutým souborům v GUI, vstupy kopírované po blocích, 1,7–2,8× rychlejší pravidla.
- [x] 0.8.1: důvody chyb v auditu, šablony a pole ve Wordu, opravená a velká PDF, ZIP do 500 MB se všemi formáty, citace judikatury.
