"""Stable audit codes for refusal reasons.

Error messages are fixed texts without document content. The audit stores
only the code derived here, so a failed batch can be diagnosed without
sending the document, its name or its text.
"""

REASONS = (
    ("OCR nenašlo čitelný text", "pdf_page_without_ocr_text"),
    ("Obraz vyžaduje česká data OCR", "ocr_data_missing"),
    ("Pro OCR vyberte složku", "ocr_path_not_ascii"),
    ("Slovenský výpis", "withheld_slovak_register"),
    ("PDF je šifrované", "pdf_encrypted_or_empty"),
    ("Stránka přesahuje limit", "pdf_page_too_large"),
    ("PDF neprošlo", "pdf_output_structure"),
    ("neočekávanou vrstvu", "pdf_output_layer"),
    ("Výstupní obraz neobsahuje", "pdf_output_redaction"),
    ("nelze umístit na stránku", "pdf_finding_without_position"),
    ("Oblast redakce je mimo", "pdf_area_outside_page"),
    ("nepodporované obrázky, přílohy, grafy, podpisy", "office_embedded_object_or_signature"),
    ("Soubor načítá externí data", "office_external_link"),
    ("Dokument načítá externí", "odf_external_link"),
    ("Vložený obrázek má nepodporovaný formát", "office_image_format"),
    ("Vložený obrázek má více snímků", "office_image_multiframe"),
    ("Vložený obrázek nelze vykreslit", "office_image_unreadable"),
    ("Vložený obrázek překračuje", "office_image_too_large"),
    ("Vložený BMP", "office_bmp"),
    ("Anonymizovaný BMP", "office_bmp"),
    ("Vzorec nebo pole obsahuje", "office_field_or_formula_sensitive"),
    ("Vzorec ve sloupci", "spreadsheet_formula_in_personal_column"),
    ("Název listu obsahuje", "spreadsheet_sheet_name_sensitive"),
    ("Citlivý údaj v názvu nebo popisku", "office_name_attribute_sensitive"),
    ("Citlivý údaj zůstal v části Office", "office_residual_in_other_xml"),
    ("Ve výstupu zůstal detekovatelný text", "residual_after_redaction"),
    ("Ve výstupu zůstal detekovatelný údaj", "residual_after_redaction"),
    ("Rozbalený dokument Office", "office_unpacked_too_large"),
    ("Neplatný dokument Office", "office_invalid_package"),
    ("nepodporované DTD", "xml_dtd"),
    ("Citlivý údaj zůstal v části dokumentu", "odf_residual_in_other_xml"),
    ("Citlivý údaj zůstal v atributu", "odf_attribute_sensitive"),
    ("OpenDocument", "odf_invalid"),
    ("vložené objekty nebo makra", "odf_object_or_macro"),
    ("obrázek v nepodporovaném formátu", "image_format"),
    ("nepodporovanou část", "odf_unknown_part"),
    ("Dokument je zašifrovaný", "odf_encrypted"),
    ("RTF", "rtf_unsupported_or_residual"),
    ("Word", "doc_unsupported"),
    ("Excel", "xls_unsupported"),
    ("MSG", "msg_unsupported"),
    ("e-mail", "mail_unsupported_or_residual"),
    ("Zašifrovan", "mail_encrypted"),
    ("Kontrola výstupu našla možný osobní údaj", "markdown_release_check"),
)


def reason_code(error):
    message = str(error)
    for fragment, code in REASONS:
        if fragment in message:
            return code
    return "other"


ADVICE = {
    "pdf_page_without_ocr_text": "Zkontrolujte, zda je sken čitelný, případně jej znovu naskenujte ve vyšší kvalitě. Jinak stránku anonymizujte ručně.",
    "ocr_data_missing": "Chybí česká data OCR. Přeinstalujte aplikaci včetně složky tessdata.",
    "ocr_path_not_ascii": "Zkopírujte aplikaci do složky, jejíž cesta neobsahuje diakritiku, a spusťte ji odtud.",
    "withheld_slovak_register": "Dokument se záměrně nevydává. Zpracujte jej ručně.",
    "pdf_encrypted_or_empty": "Odstraňte z PDF heslo nebo omezení, případně zkontrolujte, že PDF není prázdné, a uložte jej znovu.",
    "pdf_page_too_large": "Stránka je příliš velká. Zmenšete formát stránky nebo dokument rozdělte a zkuste to znovu.",
    "pdf_output_structure": "Výstup se nepodařilo bezpečně sestavit. Zkuste soubor znovu uložit v původním programu, jinak jej anonymizujte ručně.",
    "pdf_output_layer": "Výstup se nepodařilo bezpečně sestavit. Zkuste soubor znovu uložit v původním programu, jinak jej anonymizujte ručně.",
    "pdf_output_redaction": "Kontrola výstupu neprošla. Zkuste soubor znovu uložit v původním programu, jinak jej anonymizujte ručně.",
    "pdf_finding_without_position": "Nalezený údaj nelze v PDF přesně označit. Anonymizujte dokument ručně.",
    "pdf_area_outside_page": "Nalezený údaj leží mimo stránku. Zkuste PDF znovu uložit, jinak jej anonymizujte ručně.",
    "office_embedded_object_or_signature": "Odstraňte makra, vložené objekty nebo podpis a uložte dokument znovu jako DOCX, XLSX nebo PPTX bez nich.",
    "office_external_link": "Odstraňte propojení s externími daty (Data, Upravit odkazy) a uložte dokument znovu.",
    "odf_external_link": "Odstraňte propojení s externími daty nebo soubory a uložte dokument znovu.",
    "office_image_format": "Nahraďte vložené obrázky běžným formátem (PNG nebo JPEG) a uložte dokument znovu.",
    "office_image_multiframe": "Nahraďte vícesnímkové obrázky (například animace) jednoduchým PNG nebo JPEG a uložte dokument znovu.",
    "office_image_unreadable": "Odstraňte nebo znovu vložte poškozený obrázek a uložte dokument znovu.",
    "office_image_too_large": "Zmenšete vložené obrázky a uložte dokument znovu.",
    "office_bmp": "Nahraďte vložený obrázek BMP formátem PNG nebo JPEG a uložte dokument znovu.",
    "office_field_or_formula_sensitive": "Nahraďte pole nebo vzorce pevným textem a uložte dokument znovu.",
    "spreadsheet_formula_in_personal_column": "Nahraďte vzorce ve sloupcích s osobními údaji pevnými hodnotami a uložte sešit znovu.",
    "spreadsheet_sheet_name_sensitive": "Přejmenujte list tak, aby neobsahoval osobní údaje, a uložte sešit znovu.",
    "office_name_attribute_sensitive": "Odstraňte osobní údaje z názvů a popisků objektů a uložte dokument znovu.",
    "office_residual_in_other_xml": "Citlivý údaj je ve skryté části dokumentu. Zkopírujte obsah do nového dokumentu a zkuste to znovu, jinak anonymizujte ručně.",
    "residual_after_redaction": "Ve výstupu mohl zůstat osobní údaj, proto se nevydává. Anonymizujte dokument ručně nebo jej zkontrolujte a upravte.",
    "office_unpacked_too_large": "Dokument je po rozbalení příliš velký. Rozdělte jej na menší části a zkuste to znovu.",
    "office_invalid_package": "Soubor je poškozený nebo není platný dokument Office. Otevřete jej v původním programu, uložte znovu a zkuste to znovu.",
    "xml_dtd": "Odstraňte z XML definici DTD a zkuste to znovu, jinak anonymizujte ručně.",
    "odf_residual_in_other_xml": "Citlivý údaj je ve skryté části dokumentu. Zkopírujte obsah do nového dokumentu a zkuste to znovu, jinak anonymizujte ručně.",
    "odf_attribute_sensitive": "Odstraňte osobní údaje z názvů a vlastností objektů a uložte dokument znovu.",
    "odf_invalid": "Soubor je poškozený nebo není platný dokument OpenDocument. Otevřete jej v původním programu, uložte znovu a zkuste to znovu.",
    "odf_object_or_macro": "Odstraňte makra, vložené objekty nebo podpis a uložte dokument znovu jako ODT, ODS nebo ODP bez nich.",
    "image_format": "Převeďte obrázek do běžného formátu (PNG, JPEG nebo TIFF) a zkuste to znovu.",
    "odf_unknown_part": "Dokument obsahuje nepodporovanou část. Zkopírujte obsah do nového dokumentu a zkuste to znovu.",
    "odf_encrypted": "Odstraňte z dokumentu šifrování nebo heslo a uložte jej znovu.",
    "rtf_unsupported_or_residual": "Otevřete soubor v textovém editoru, odstraňte vložené objekty, uložte znovu jako RTF nebo DOCX a zkuste to znovu.",
    "doc_unsupported": "Otevřete soubor ve Wordu, odstraňte makra a vložené objekty a uložte jej jako DOCX.",
    "xls_unsupported": "Otevřete soubor v Excelu, odstraňte makra a vložené objekty a uložte jej jako XLSX.",
    "msg_unsupported": "Uložte zprávu znovu jako EML nebo ji anonymizujte ručně.",
    "mail_unsupported_or_residual": "Odstraňte z e-mailu nepodporované přílohy nebo skrytý obsah, případně zprávu anonymizujte ručně.",
    "mail_encrypted": "Odstraňte z e-mailu šifrování nebo podpis a uložte zprávu znovu, jinak ji anonymizujte ručně.",
    "size_limit": "Soubor nebo položka archivu je příliš velká. Rozdělte dokument na menší části a zkuste to znovu.",
    "invalid_signature": "Přípona souboru neodpovídá jeho obsahu. Otevřete soubor v původním programu, uložte jej znovu ve správném formátu a zkuste to znovu.",
    "archive_encrypted": "Odstraňte z archivu heslo a zkuste to znovu.",
    "archive_depth": "Archiv obsahuje příliš mnoho vnořených archivů. Rozbalte jej a zpracujte soubory zvlášť.",
    "archive_limit": "Archiv je po rozbalení příliš velký. Rozdělte jej na menší části a zkuste to znovu.",
    "archive_member_unsupported": "Archiv obsahuje nepodporovanou položku. Rozbalte jej a zpracujte podporované soubory zvlášť.",
    "attachment_unsupported": "Odstraňte přílohu v nepodporovaném formátu nebo ji zpracujte zvlášť.",
    "scan_pages": "Sken TIFF je prázdný nebo má příliš mnoho stran. Rozdělte jej na menší části nebo jej uložte znovu.",
    "binary_as_text": "Soubor není textový. Zkontrolujte jeho příponu nebo jej uložte ve správném formátu.",
    "xml_sensitive_name": "Osobní údaj je součástí názvu prvku XML. Takový soubor anonymizujte ručně.",
    "key_collision": "Po anonymizaci by vznikly duplicitní klíče JSON. Soubor anonymizujte ručně.",
    "corrupt_output": "Výstup se nepodařilo bezpečně sestavit. Zkuste soubor znovu uložit v původním programu, jinak jej anonymizujte ručně.",
    "residual_data": "Kontrola výstupu našla zbývající údaj, proto se soubor nevydává. Anonymizujte jej ručně.",
}

ADVICE.update({
    "markdown_release_check": "Kontrola výstupu našla možný osobní údaj, soubor nebyl vydán. Zpracujte spis znovu; "
                              "pokud se chyba opakuje, rozdělte spis a zjistěte, která položka ji způsobuje.",
    "format_unsupported": "Formát položky aplikace nepodporuje. Převeďte ji na podporovaný formát (například PDF nebo DOCX) a zkuste to znovu.",
    "processing_error": "Položku se nepodařilo zpracovat. Otevřete ji v původním programu, uložte znovu a zkuste to znovu; jinak ji anonymizujte ručně.",
})

PERMISSION_ADVICE = "Soubor je otevřený v jiném programu nebo k němu chybí oprávnění. Zavřete jej, zkontrolujte přístup a zkuste to znovu."
GENERAL_ADVICE = "Otevřete soubor v původním programu, uložte ho znovu a zkuste to znovu; jinak jej anonymizujte ručně."

# Fixed texts of FormatError (formats.py, batch.py); more specific than REASONS.
FORMAT_MESSAGES = (
    ("Vstup překračuje limit velikosti.", "size_limit"),
    ("Položka archivu překračuje limit velikosti.", "size_limit"),
    ("Obsah neodpovídá deklarovanému formátu.", "invalid_signature"),
    ("Po anonymizaci vznikl duplicitní klíč JSON.", "key_collision"),
    ("XML obsahuje nepodporované DTD.", "xml_dtd"),
    ("Citlivý údaj je součástí názvu prvku XML.", "xml_sensitive_name"),
    ("Soubor obsahuje binární data.", "binary_as_text"),
    ("Archiv překročil limit vnoření.", "archive_depth"),
    ("Archiv překročil limit rozbalení.", "archive_limit"),
    ("Archiv obsahuje zašifrovaný soubor.", "archive_encrypted"),
    ("Archiv obsahuje nepodporovanou položku.", "archive_member_unsupported"),
    ("Sken TIFF je prázdný", "scan_pages"),
    ("Výstup OpenDocument nelze přečíst.", "corrupt_output"),
    ("Výstup Office nelze přečíst.", "corrupt_output"),
    ("Výstupní archiv nelze přečíst.", "corrupt_output"),
    ("Kontrola výstupu našla zbývající údaj.", "residual_data"),
    ("E-mail nebo archiv obsahuje položku v nepodporovaném formátu.", "attachment_unsupported"),
)


def advice(message):
    """Fixed Czech advice for a refusal message; never contains document data."""
    text = str(message)
    for fragment, code in FORMAT_MESSAGES:
        if fragment in text:
            return ADVICE[code]
    code = reason_code(text)
    if code in ADVICE:
        return ADVICE[code]
    if "PermissionError" in text:
        return PERMISSION_ADVICE
    return GENERAL_ADVICE


# Fixed texts of the preview window (preview.py). They never contain a name, path or content of a document.
PREVIEW_MESSAGES = {
    "preview_input_changed": "Vstupní soubor se od zpracování změnil nebo zmizel. Zpracujte spis znovu.",
    "preview_markdown_missing": "Markdown nebyl vydán, výstup nelze zobrazit.",
    "preview_section_missing": "Sekce tohoto dokumentu ve vydaném Markdownu nebyla nalezena.",
    "preview_no_original": "Originál přílohy e-mailu nebo vloženého obrázku se v náhledu nezobrazuje.",
    "preview_not_page": "Originál tohoto formátu se v náhledu nezobrazuje. Otevřete jej v původním programu.",
    "preview_page_too_large": "Stránka je příliš velká pro náhled.",
    "preview_no_page": "Tato strana v dokumentu není.",
    "preview_no_copy": "Kontrolní kopie této položky neexistuje.",
    "preview_failed": "Náhled se nepodařilo zobrazit.",
    "preview_unprocessed": "Položka nebyla zpracována.",
    "preview_encrypted": "PDF je šifrované nebo poškozené, náhled nelze zobrazit.",
}
