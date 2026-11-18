"""Regression tests for leaks found by the detector (synthetic data only)."""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anonymizer import documents, rules as rules_module  # noqa: E402
from anonymizer.leaks import Value, layers, scan_texts  # noqa: E402
from anonymizer.rules import Rules  # noqa: E402
import synthetic_spis as spis  # noqa: E402

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
needs_ocr = unittest.skipUnless(TESSDATA, "Chybí testovací jazyková data.")


def kinds(text):
    return [item.kind for item in Rules().find(text)]


class LabelAcrossLinesTests(unittest.TestCase):
    def test_account_label_at_line_end(self):
        replaced = Rules().replace("částku 1 Kč na účet\n1019283746/0300. Další věta.")[0]
        self.assertEqual(replaced, "částku 1 Kč na účet\n[UCET-1]. Další věta.")
        self.assertEqual(Rules().replace("č. ú.\n35-1122334455/0800")[0], "č. ú.\n[UCET-1]")
        self.assertEqual(Rules().replace("bankovní\nspojení: 1234567/0100")[0], "bankovní\nspojení: [UCET-1]")

    def test_document_and_company_id_across_lines(self):
        self.assertEqual(Rules().replace("OP č.:\nAB1234567 platí")[0], "OP č.:\n[DOKLAD-1] platí")
        self.assertEqual(Rules().replace("pas:\n\n12345678 platí")[0], "pas:\n\n[DOKLAD-1] platí")
        self.assertEqual(Rules().replace("IČO:\n60193531,")[0], "IČO:\n[ICO-1],")
        self.assertEqual(Rules().replace("IČ\n6019 3531.")[0], "IČ\n[ICO-1].")

    def test_label_without_number_is_left_alone(self):
        text = "Doklad: potvrzení\nÚčet je zřízen.\nIČO neuvedeno\n"
        self.assertEqual(Rules().replace(text), (text, 0))

    def test_scanners_still_return_exactly_the_pattern_matches(self):
        text = ("účet\n1019283746/0300 a OP č.:\nAB1234567, IČO:\n60193531, pas:\n12345678,\n"
                "tel. 774 220\n861, 777-123-456 +420 (777).123.456 (+420) 603 111 222 00420602333444")
        for kind, scan in rules_module.SCANNERS.items():
            expected = [match.regs for match in rules_module.COMPILED[kind].finditer(text)]
            self.assertEqual([match.regs for match in scan(text)], expected, kind)


class PhoneTests(unittest.TestCase):
    def test_common_czech_spellings(self):
        for number in ("774 220\n861", "777-123-456", "777.123.456", "+420 777-123-456", "+420777123456",
                       "(+420) 777 123 456", "00420 777 123 456", "(+420)777.123.456", "224-123-456",
                       "777 - 123 - 456", "777123456", "224 123 456"):
            self.assertEqual(Rules().replace(f"Tel. {number}.")[0], "Tel. [TELEFON-1].", number)

    def test_same_number_gets_one_label(self):
        rules = Rules()
        text = "a 777 123 456, b +420 777-123-456, c (+420) 777.123.456, d 00420777123456"
        self.assertEqual(rules.replace(text)[0], "a [TELEFON-1], b [TELEFON-1], c [TELEFON-1], d [TELEFON-1]")

    def test_no_false_findings(self):
        for text in ("Dne 27.12.2023 v 12.30 hod.", "sp. zn. 12 C 345/2026", "IČO: 60193531", "částka 1.234.567 Kč",
                     "č. j. 12 C 345 / 2026-100", "verze 7.12.3", "Strana 2 z 4", "RČ 850314/2010",
                     "4.5.2020 - 6.7.2021", "20-12-2020", "12 345-678"):
            self.assertNotIn("TELEFON", kinds(text), text)


class EmailLookalikeTests(unittest.TestCase):
    def test_ocr_misreadings_of_at_sign(self):
        for mail in ("jan.novak©example.test", "jan.novak®example.test", "jan.novak©Ďexample.test",
                     "jan.novak(at)example.test", "jan.novak[at]example.test", "jan.novak@example.test"):
            replaced, count = Rules().replace(f"e-mail {mail}, rodné číslo")
            self.assertEqual((replaced, count), ("e-mail [EMAIL-1], rodné číslo", 1), mail)

    def test_same_address_one_label_and_no_false_findings(self):
        self.assertEqual(Rules().replace("a jan@example.test, b jan©example.test")[0], "a [EMAIL-1], b [EMAIL-1]")
        for text in ("© 2026 example.test", "Copyright ©2026 Firma", "Značka® je chráněna", "a (at) b", "příklad (at)\n"):
            self.assertEqual(Rules().find(text), [], text)

    def test_span_covers_the_original_characters(self):
        text = "x jan©Ďexample.test y"
        (found,) = Rules().find(text)
        self.assertEqual(text[found.start:found.end], "jan©Ďexample.test")


class JoinedViewTests(unittest.TestCase):
    def test_hyphen_and_line_breaks(self):
        view, indexes = documents.joined_view("Ondráč-\nková a\nBéda-\nÚplná")
        self.assertEqual(view, "Ondráčková a Béda- Úplná")
        self.assertEqual(len(view), len(indexes))
        self.assertEqual(indexes[view.index("ková")], 8)

    def test_redaction_covers_both_parts_of_a_split_name(self):
        text = "Žalobkyně paní Radoslava Ondráč-\nková, nar.\n"
        boxes, x, line = [], 0, 0
        for char in text:
            if char == "\n":
                boxes.append(None)
                line, x = line + 1, 0
            else:
                boxes.append((line, pymupdf.Rect(x * 6, line * 20 + 2, x * 6 + 6, line * 20 + 14)))
                x += 1
        pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 300, 60), False)
        pixmap.clear_with(255)
        areas = documents.redact_geometry(pixmap, Rules(), text, boxes, pymupdf.Matrix(1, 1))
        self.assertTrue(areas)
        # a pixel inside "Ondráč-" on the first line and inside "ková" on the second one are black
        first = text.index("Ondráč") * 6 + 3
        self.assertEqual(pixmap.pixel(first, 8)[0], 0)
        self.assertEqual(pixmap.pixel(6, 28)[0], 0)
        self.assertEqual(pixmap.pixel(250, 50)[0], 255)

    def test_value_continuing_on_the_next_line_is_blacked_out(self):
        text = "tel. 774 220\n861.\n"
        boxes, x, line = [], 0, 0
        for char in text:
            if char == "\n":
                boxes.append(None)
                line, x = line + 1, 0
            else:
                boxes.append((line, pymupdf.Rect(x * 6, line * 20 + 2, x * 6 + 6, line * 20 + 14)))
                x += 1
        pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 200, 60), False)
        pixmap.clear_with(255)
        documents.redact_geometry(pixmap, Rules(), text, boxes, pymupdf.Matrix(1, 1))
        self.assertEqual(pixmap.pixel(8 * 6, 8)[0], 0)
        self.assertEqual(pixmap.pixel(6, 28)[0], 0)


@needs_ocr
class ScanTests(unittest.TestCase):
    def leaks(self, data, values):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "a.pdf", Path(folder) / "b.pdf"
            source.write_bytes(data)
            documents.redact_pdf(source, target, Rules(), TESSDATA)
            with pymupdf.open(source) as original:  # what the redaction saw (synthetic data only)
                self.ocr_seen = [documents.recognize_image(page.get_pixmap(dpi=300), TESSDATA)[0] for page in original]
            texts = list(layers(target, TESSDATA))
            self.seen = texts  # synthetic data only: shown when an assertion fails
            return scan_texts(texts, values)

    def test_scanned_page_with_hyphenated_name_and_email(self):
        data = spis.scan_pdf([["Žalobkyně paní Radoslava Ondráč-", "ková, nar. 2. 2. 1975, bytem Havlíčkova 31,",
                               "e-mail radoslava.ondrackova@example.test, tel. 733 456 789."]])
        counted = self.leaks(data, [Value("JMENO", "Radoslava Ondráčková"), Value("PRIJMENI", "Ondráčková"),
                                    Value("EMAIL", "radoslava.ondrackova@example.test"),
                                    Value("TELEFON", "733 456 789")])
        self.assertEqual(sum(counted.values()), 0, f"{dict(counted)} {ascii(self.seen)} REDACTION-OCR {ascii(self.ocr_seen)}")

    def test_text_layer_phone_split_over_two_lines(self):
        data = spis.text_pdf(["Plátce Karel Hájek, IČO: 60193531, účet 35-1122334455/0800, tel. 774 220 861."])
        counted = self.leaks(data, [Value("TELEFON", "774 220 861"), Value("UCET", "35-1122334455/0800"),
                                    Value("ICO", "60193531")])
        self.assertEqual(sum(counted.values()), 0, dict(counted))

    def test_account_label_ending_a_line_of_a_text_pdf(self):
        data = spis.text_pdf(["Žalovaný je povinen zaplatit žalobci do tří dnů částku 184 500 Kč na účet 1019283746/0300."])
        with pymupdf.open(stream=data) as document:
            self.assertRegex(document[0].get_text(), r"účet\n1019283746")
        self.assertEqual(sum(self.leaks(data, [Value("UCET", "1019283746/0300")]).values()), 0)

    def test_png_email_read_with_a_lookalike_sign(self):
        from anonymizer import office
        data = spis.png(["Ilona Kučerová", "tel. 736 405 128", "e-mail ilona.kucerova@example.test"])
        output = office.redact_image(data, "png", Rules(), TESSDATA)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "a.png"
            target.write_bytes(output)
            counted = scan_texts(layers(target, TESSDATA), [Value("EMAIL", "ilona.kucerova@example.test"),
                                                            Value("TELEFON", "736 405 128")])
        self.assertEqual(sum(counted.values()), 0, dict(counted))


if __name__ == "__main__":
    unittest.main()
