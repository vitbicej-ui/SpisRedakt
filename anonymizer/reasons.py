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
)


def reason_code(error):
    message = str(error)
    for fragment, code in REASONS:
        if fragment in message:
            return code
    return "other"
