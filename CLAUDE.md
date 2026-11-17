# SpisRedakt – pokyny pro vývoj

Offline anonymizér českých spisů pro Windows (Python 3.13, PySide6, PyMuPDF, lxml). Licence AGPL-3.0-only.

## Nepřekročitelná pravidla

- Nikdy nepracuj se skutečnými dokumenty, spisy ani osobními údaji. Testovací data vytvářej synteticky přímo v testech.
- Aplikace nesmí mít síťové funkce. Nové závislosti jen s licencí kompatibilní s AGPL, s připnutou verzí v `requirements.txt` a záznamem v `THIRD-PARTY.md`.
- Bezpečnost má přednost před pohodlím: když nejde citlivý údaj spolehlivě odstranit, soubor se nevydá (`ValueError` s pevným českým textem bez obsahu dokumentu) nebo se daná část začerní.
- Chybová hlášení nesmí obsahovat obsah, název ani cestu dokumentu. Každé nové hlášení přidej do `anonymizer/reasons.py`.
- Audit (`anonymizer/audit.py`) ukládá jen pořadí, formát a kódy.

## Struktura

- `anonymizer/czech.py` – kontextová pravidla (jména, adresy, data narození, spisové značky, formuláře, tabulky podle nadpisů)
- `anonymizer/names.py` – slovník křestních jmen se skloňováním
- `anonymizer/rules.py` – `Rules`: hledání, štítky `[DRUH-n]`, `replace`, `cell`
- `anonymizer/office.py` – DOCX/XLSX/PPTX, `anonymizer/odf.py` – ODT/ODS/ODP, `anonymizer/rtf.py`, `anonymizer/mail.py` – EML/MSG, `anonymizer/legacy.py` – DOC/XLS
- `anonymizer/documents.py` – PDF a OCR, `anonymizer/formats.py` – rozpoznání formátu, limity, ZIP, `anonymizer/batch.py` – dávka
- `tests/test_processing.py` – regresní testy (unittest)

## Prostředí a testy

```bash
pip install -r requirements-build.txt
bash scripts/get-ocr.sh            # stáhne ověřená česká data OCR do tessdata/
export SPISREDAKT_TEST_TESSDATA=$PWD/tessdata QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests
```

V Linuxu může PySide6 vyžadovat `apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1`. Všechny testy musí projít, včetně OCR. Windows CI (`.github/workflows/windows.yml`) běží po každém pushi a sestavuje balíček.

## Konvence

- README, hlášení a dokumentace česky, kód a commity anglicky.
- Každá změna chování: test v `tests/test_processing.py` a odstavec v README (sekce „Rozsah zpracování“).
- Malé, samostatné commity. Verze je v `anonymizer/audit.py` (`VERSION`) a v nadpisu README.
- Úpravy workflow, oprávnění a závislostí vyžadují lidskou kontrolu (viz `AUTOMATION.md`).
