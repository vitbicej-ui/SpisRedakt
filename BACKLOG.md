# Backlog

Pořadí je priorita. Orchestrátor bere úkoly shora, stav zapisuje sem: `[ ]` čeká, `[~]` rozpracováno, `[x]` hotovo (s commitem), `[?]` čeká na rozhodnutí člověka.

## Robustnost u skutečných spisů

- [x] **Důvod chyby v GUI.** (8e048ac) U odmítnutého souboru zobrazit srozumitelnou radu podle kódu z `anonymizer/reasons.py` (např. „Odstraňte makra a uložte znovu jako DOCX“).
- [x] **Paměť u velkých vstupů.** (d0519bc) `batch.py` čte celý soubor do paměti (až 500 MB). Rozpoznat formát z hlavičky nebo cesty a soubor kopírovat po blocích.
- [x] **Výkon pravidel.** (df4a646, 1,7–2,8×) `Rules.find` zpracuje asi 1 MB textu za sekundu. Profilovat `contextual_findings` a zrychlit bez změny výsledků.
- [ ] **Přesné tabulky v DOC.** `legacy.py` skládá tabulky heuristicky. Použít vlastnosti odstavců (PlcBtePapx, sprmPFInTable), aby se víceodstavcové buňky nerozpadaly.
- [ ] **Nadpisy sloupců v RTF tabulkách.** Napojit `plan_table` na buňky RTF (`\cell`, `\row`).

## Kontrola výstupů

- [ ] **Linuxový CI job s LibreOffice.** Otevřít syntetické výstupy (DOCX, XLSX, PPTX, ODT, RTF) v LibreOffice headless a ověřit, že se převedou bez chyby. Úprava workflow vyžaduje lidskou kontrolu, proto ji připrav jako samostatný commit a v backlogu ji označ `[?]`.
- [ ] **Regresní korpus.** Rozšířit syntetické testy o smíšené skeny, obtížná písma, zalomení řádků a typické soudní šablony (záhlaví, zápatí, doložka „Shodu s prvopisem potvrzuje“).

## Funkce

- [ ] **Náhled v GUI.** Zobrazit originál a výstup vedle sebe po stránkách, bez ukládání dalších kopií.
- [ ] **Ruční vyznačení ploch.** V náhledu PDF nebo obrázku umožnit doplnit začernění myší a výstup znovu ověřit.
- [?] **Jednotné pseudonymy mezi dávkami.** Vyžaduje rozhodnutí: kde a jak šifrovaně uchovat mapu štítků, aby neobsahovala čitelné osobní údaje.
- [?] **Automatické vydání podle štítku.** Workflow, které po pushnutí štítku `v*` založí GitHub Release se sestavením. Vyžaduje lidskou kontrolu oprávnění.

## Hotovo

- [x] 0.8.1: důvody chyb v auditu, šablony a pole ve Wordu, opravená a velká PDF, ZIP do 500 MB se všemi formáty, citace judikatury.
