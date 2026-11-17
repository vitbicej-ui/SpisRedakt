# Ruční test verze 0.9.0

Testujte jen na kopiích dokumentů v místní složce mimo synchronizované úložiště. Výstupy nesdílejte, dokud je člověk neporovná s originálem.

## Instalace

1. Stáhněte artefakt `SpisRedakt-Windows-x64` z běhu GitHub Actions a rozbalte jej celý do složky bez diakritiky, například `C:\SpisRedakt`.
2. Spusťte `SpisRedakt.exe`. Vedle něj musí být složka `tessdata`.
3. Vyberte vstupní složku a potom samostatnou prázdnou výstupní složku.

## Co ověřit u každého formátu

U každého výstupu otevřete originál a výstup vedle sebe a zapište si do tabulky níže: soubor, formát, zda se otevřel, přehlédnuté údaje, zbytečně skryté údaje a poznámku.

| Oblast | Co zkusit | Očekávání |
|---|---|---|
| Jména | běžná i méně častá křestní jména, skloňování, `J. Novák`, `Novák J.`, `JUDr. K. Bílá` | Stejná osoba má všude stejný štítek. Iniciála bez kontextu může zůstat. |
| Formuláře | `Jméno: …`, `Rodné číslo: …`, tabulky s popiskem vlevo | Hodnota má štítek, popisek zůstane. |
| Tabulky | nadpisy `Jméno`, `Datum narození`, `Telefon`, `IČO`. Sloupce `Datum podpisu`, `Poznámka` | Citlivé sloupce mají štítky, ostatní zůstanou beze změny. |
| DOCX, XLSX, PPTX | styly, sloučené buňky, obrázky s textem | Formát zůstane, text v obrázcích je začerněn. |
| ODT, ODS, ODP | otevření v LibreOffice | Otevře se bez opravy, styly zůstanou. |
| RTF | otevření ve Wordu a WordPadu | Otevře se, formátování zůstane, metadata zmizí. |
| DOC, XLS | starší dokumenty a sešity | Výstup je DOCX/XLSX bez formátování, obrázky nahrazuje `[OBRÁZEK VYNECHÁN]`. |
| PDF, TIFF | skeny, vícestránkové TIFF, razítka | Výstup je PDF z obrazů, citlivý text je začerněn. Razítka, podpisy a rukopis zkontrolujte ručně. |
| EML, MSG | e-mail s přílohou, HTML zprávou a více příjemci | Výstup je EML. Otevře se v Outlooku nebo Thunderbirdu, přílohy jsou anonymizované. |
| Markdown | otevřít `spis_anonymizovano.md` z dávky se ZIPem a volbou kontrolní kopie, případně i „Rozdělit po dokumentech“ | Legenda štítků a obsah `D1…Dn` odpovídají spisu, sekce „Nezpracované položky“ uvádí skutečně vynechané položky. Namátkou porovnejte texty s originálem a s kontrolním ZIPem (`spis_kontrola.zip`). |
| Chyby | zašifrovaný soubor, makra, vložený objekt | Soubor se nevydá a audit uvede chybu. |

## Zpětná vazba

Do hlášení nevkládejte skutečné osobní údaje. Uveďte formát, druh údaje (například „příjmení v zápatí DOC“), číslo dávky z `souhrn.txt` a vlastní syntetický příklad, který chybu napodobí.
