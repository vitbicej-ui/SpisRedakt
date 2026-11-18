"""Lone given names of known persons and unknown given names before a known surname (synthetic data only)."""
import tempfile
import unittest
from pathlib import Path

from anonymizer.batch import process_folders
from anonymizer.leaks import Value, scan_texts
from anonymizer.rules import Rules

FULL = "Karel Dvořák bydlí v obci. "
LONE = "Děkuji, Karle. Podklady předám Karlovi. Milá Hano, Karel."
POWER = "Plná moc: Vojmír Kvasnička zmocňuje advokáta."


def run(texts, collect=False):
    rules = Rules()
    if collect:
        for text in texts:
            rules.collect(text)
    return [rules.replace(text)[0] for text in texts]


class GivenNameTests(unittest.TestCase):
    def test_lone_given_name_of_known_person(self):
        for collect in (False, True):
            full, lone = run([FULL, LONE], collect)
            self.assertNotIn("Karel", full)
            self.assertNotIn("Karle", lone)
            self.assertNotIn("Karlovi", lone)
            self.assertIn("Milá Hano", lone, "Jméno neznámé osoby se nemá měnit.")
            self.assertEqual(lone.count("[OSOBA-1]"), 3)

    def test_given_name_in_same_text_and_after_comma(self):
        text, _ = Rules().replace("Karel Dvořák přišel. Karle, děkuji. Dne 3. 5. přišla Dana.")
        self.assertNotIn("Karle", text)
        self.assertIn("Dana", text)

    def test_ordinary_word_form_at_sentence_start_stays(self):
        full, text = run(["Dana Nováková žije v obci.", "Danou přišla. Dana přišla. Přišla Dana."], True)
        self.assertIn("Danou přišla", text)
        self.assertNotIn("Přišla Dana", text)

    def test_two_persons_share_one_label(self):
        first, second, text = run(["Jan Novák a Jana Nováková.", "x", "Děkuji, Jan."], True)
        self.assertEqual(text, "Děkuji, [OSOBA-3].")

    def test_unknown_given_name_stays(self):
        text, _ = Rules().replace("Svědek Jan řekl, že sv. Jan byl světec. Děkuji, Jan.")
        self.assertIn("Jan", text)
        self.assertNotIn("[OSOBA", text)

    def test_unknown_given_name_before_known_surname(self):
        for collect in (False, True):
            _, text = run(["Pan Kvasnička přišel.", POWER], collect)
            self.assertEqual(text, "Plná moc: [OSOBA-1] zmocňuje advokáta.")

    def test_known_surname_by_full_name(self):
        text, _ = Rules().replace("Plná moc: Vojmír Kvasnička zmocňuje. Vojmír Kvasnička přišel. Pan Kvasnička.")
        self.assertNotIn("Vojmír", text)

    def test_common_word_before_surname_stays(self):
        text, _ = Rules().replace("Pan Novák přišel. Podle Nováka, Proti Novákovi, Svědek Novák.")
        self.assertIn("Podle ", text)
        self.assertIn("Proti ", text)
        self.assertIn("Svědek ", text)
        self.assertNotIn("Novák", text)

    def test_lowercase_word_before_surname_stays(self):
        text, _ = Rules().replace("Pan Novák přišel. Včera přišel Novák.")
        self.assertIn("přišel [OSOBA-1]", text)

    def test_markdown_of_two_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder, "vstup"), Path(folder, "vystup")
            source.mkdir()
            target.mkdir()
            (source / "a.txt").write_text(FULL.replace("Karel Dvořák", "Karel Dvořák, nar. 3. 4. 1980,"), encoding="utf-8")
            (source / "b.txt").write_text(LONE + " " + POWER + " Pan Kvasnička.", encoding="utf-8")
            list(process_folders(source, target, markdown=True, copies=False))
            output = (target / "spis_anonymizovano.md").read_text(encoding="utf-8")
        self.assertEqual(scan_texts([output], [Value("KRESTNI", "Karel"), Value("KRESTNI", "Vojmír")]), {})
        self.assertIn("Milá Hano", output)
        self.assertNotIn("Vojmír", output)


class DetectorMailTests(unittest.TestCase):
    def test_short_plain_local_part_is_not_searched_alone(self):
        value = Value("EMAIL", "info@soud.example")
        self.assertEqual(scan_texts(["Napište na info nebo na [EMAIL-1]."], [value]), {})
        self.assertEqual(scan_texts(["Napište na info@soud.example."], [value]), {"EMAIL": 1})
        long_name = Value("EMAIL", "jan.novak@soud.example")
        self.assertEqual(scan_texts(["Pište jan.novak."], [long_name]), {"EMAIL": 1})

    def test_lone_given_name_detector(self):
        value = Value("KRESTNI", "Ilona")
        self.assertEqual(scan_texts(["Děkuji, Ilono a Ilona."], [value]), {"KRESTNI": 2})
        self.assertEqual(scan_texts(["ilona"], [value]), {})
        self.assertEqual(scan_texts(["Ilona Kučerová"], [Value("JMENO", "Ilona Kučerová"), value]), {"JMENO": 1})


if __name__ == "__main__":
    unittest.main()
