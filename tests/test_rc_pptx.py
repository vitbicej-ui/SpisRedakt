import random
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from anonymizer import rules as rules_module
from anonymizer.office import redact_office
from anonymizer.rules import Rules, valid_rc

# Invented numbers with a correct checksum (ten digits, divisible by 11) and a nine digit one from before 1954.
VALID10 = "8503141240"
VALID10_WOMAN = "8553141234"
VALID9 = "530314123"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14 = "http://schemas.microsoft.com/office/powerpoint/2010/main"


def kinds(text):
    rules = Rules()
    return [(text[item.start:item.end], item.kind) for item in rules.find(text)]


class BirthNumberTests(unittest.TestCase):
    def test_valid_rc(self):
        self.assertTrue(valid_rc(VALID10))
        self.assertTrue(valid_rc(VALID10_WOMAN))
        self.assertTrue(valid_rc(VALID9))
        self.assertFalse(valid_rc("8503141241"))  # checksum
        self.assertFalse(valid_rc("8513141240"))  # month 13
        self.assertFalse(valid_rc("8503321240"))  # day 32
        self.assertFalse(valid_rc("540314123"))  # nine digits after 1953
        self.assertFalse(valid_rc("85031412"))

    def test_every_spelling_gets_one_label(self):
        text = (f"A {VALID10[:6]}/{VALID10[6:]}, B {VALID10[:6]}-{VALID10[6:]}, C {VALID10[:6]} {VALID10[6:]}, "
                f"D {VALID10}, E {VALID10[:6]} - {VALID10[6:]}.")
        output, count = Rules().replace(text)
        self.assertEqual(output, "A [RC-1], B [RC-1], C [RC-1], D [RC-1], E [RC-1].")
        self.assertEqual(count, 5)

    def test_nine_digits_and_cue(self):
        self.assertEqual(Rules().replace(f"nar. {VALID9[:6]}-{VALID9[6:]}")[0], "nar. [RC-1]")
        self.assertEqual(Rules().replace(f"rodné číslo {VALID9}")[0], "rodné číslo [RC-1]")
        # A cue takes a number that fails the checksum; without it the number is left alone.
        self.assertEqual(Rules().replace("RČ 850314-1241 uvedeno")[0], "RČ [RC-1] uvedeno")
        self.assertEqual(Rules().replace("r. č. 850314 1241.")[0], "r. č. [RC-1].")
        self.assertEqual(Rules().replace("rodné číslo 850314 1241")[0], "rodné číslo [RC-1]")
        self.assertEqual(Rules().replace("850314-1241 uvedeno")[0], "850314-1241 uvedeno")

    def test_not_a_birth_number(self):
        for text in ("telefon 7771234567", "telefon 777123456", "účet 2800451239/2010", "IČO 12345678",
                     "datum 20260312", "sp. zn. 12 C 345/2026", "částka 12345678 Kč", "8503141241",
                     "850314 124", "č. 1234567890123", "obrat 1.8503141240", "19-8503141240/0100",
                     f"{VALID10}/0800", f"účet 19-{VALID10}/0100", f"123456-{VALID10}/0300"):
            found = [item for item in kinds(text) if item[1] == "RC"]
            self.assertEqual(found, [], text)
            # The digits are never cut: whatever is replaced is a whole number.
            output = Rules().replace(text)[0]
            self.assertNotIn("[RC-", output, text)

    def test_account_number_is_not_shortened(self):
        text = f"bankovní spojení: 123456-{VALID10}/0300 a účet {VALID10}/0800 a účet {VALID9}/0100"
        output = Rules().replace(text)[0]
        self.assertNotIn("/0300", output)
        self.assertNotIn("/0800", output)
        self.assertNotIn("/0100", output)
        self.assertNotIn("[RC-", output)

    def test_labels_and_cells_share_identity(self):
        rules = Rules()
        first = rules.replace(f"RČ {VALID10[:6]}/{VALID10[6:]}")[0]
        second = rules.replace(f"narozen, {VALID10}")[0]
        self.assertTrue(first.endswith("[RC-1]"))
        self.assertTrue(second.endswith("[RC-1]"))
        cell = rules.cell("RC", f"{VALID10[:6]}-{VALID10[6:]}")
        self.assertEqual(cell, "[RC-1]")

    def test_scanners_equal_their_patterns(self):
        rng = random.Random(7)
        pieces = ["850314", "1240", "123", "-", "/", " ", "  ", "\t", ".", ",", "RČ", "r. č.", "rodné číslo", ":",
                  "8503141240", "530314123", "a", "účet", "+", "0800", "9", "19", "Kč", "\n"]
        found = {"RC_BARE": 0, "RC_CTX": 0}
        for _ in range(6000):
            text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 12)))
            for kind in found:
                expected = [match.regs for match in rules_module.COMPILED[kind].finditer(text)]
                self.assertEqual([m.regs for m in rules_module.SCANNERS[kind](text)], expected, (kind, text))
                found[kind] += len(expected)
        self.assertTrue(all(found.values()), found)


class PptxTechnicalIdTests(unittest.TestCase):
    def process(self, slide_body, part="ppt/slides/slide1.xml"):
        folder = Path(tempfile.mkdtemp())
        source = folder / "in.pptx"
        xml = (f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
               f'xmlns:a="{DRAW}" xmlns:p14="{P14}">{slide_body}</p:sld>')
        with ZipFile(source, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<root/>")
            archive.writestr(part, xml)
        redact_office(source, folder / "out.pptx", Rules())
        with ZipFile(folder / "out.pptx") as archive:
            return archive.read(part).decode()

    def test_creation_id_is_not_a_phone_number(self):
        body = ('<p:extLst><p:ext uri="{BB962C8B}"><p14:creationId val="727027711"/></p:ext></p:extLst>'
                '<p:cNvPr id="777123456" name="Obsah"/><a:p><a:r><a:t>Text</a:t></a:r></a:p>')
        for part in ("ppt/slides/slide1.xml", "ppt/slideLayouts/slideLayout1.xml"):
            data = self.process(body, part)
            self.assertIn('val="727027711"', data)
            self.assertIn("Text", data)

    def test_description_is_still_protected(self):
        data = self.process('<p:cNvPr id="4" name="Obrázek" descr="Jan Novák"/><a:p><a:r><a:t>x</a:t></a:r></a:p>')
        self.assertNotIn("Novák", data)
        with self.assertRaises(ValueError):
            self.process('<p:cNvPr id="4" name="Tel. 777 123 456"/><a:p><a:r><a:t>x</a:t></a:r></a:p>')
        with self.assertRaises(ValueError):
            self.process('<p:cNvPr id="4" name="Obrázek" alt="777 123 456"/>')


if __name__ == "__main__":
    unittest.main()
