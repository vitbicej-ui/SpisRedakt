"""Synthetic court file: detector of leaks, generator and the whole path through the batch.

Messages never contain document values; only kinds and counts are shown.
"""
import base64
import hashlib
import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path

from docx import Document
import olefile
import pymupdf

from anonymizer.batch import process_folders
from anonymizer.leaks import KINDS, Value, layers, report, scan_texts, scan_tree
import synthetic_spis as spis

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
needs_ocr = unittest.skipUnless(TESSDATA, "Chybí testovací jazyková data.")

# Kinds that must have no leak in the output (ZIP and the unpacked batch): every kind,
# including a lone given name of a known person.
ZERO_LEAK_KINDS = tuple(KINDS)
assert "KRESTNI" in ZERO_LEAK_KINDS

WOLF = Value("JMENO", "Tadeáš Wolf")
WOLF_SURNAME = Value("PRIJMENI", "Wolf")
NOVAK = Value("PRIJMENI", "Novák")
MALY = Value("PRIJMENI", "Malý", common=True)
RC = Value("RC", "850314/1237")
PHONE = Value("TELEFON", "721 345 678")
BIRTH = Value("NAROZENI", "1980-02-01")
MAIL = Value("EMAIL", "tadeas.wolf@example.test")
ACCOUNT = Value("UCET", "123456789/0800")
PAPER = Value("DOKLAD", "204516973")
COMPANY = Value("ICO", "12345678")
STREET = Value("ADRESA", "Dlouhá 15, 280 02 Kolín")


class DetectorTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def found(self, texts, *values):
        texts = [texts] if isinstance(texts, str) else texts
        return dict(scan_texts(texts, values))

    def check(self, text, values, expected):
        counted = self.found(text, *values)
        self.assertEqual(counted, expected, "Nalezené druhy a počty se liší od očekávaných.")

    def test_case_diacritics_and_forms(self):
        for text, values, expected in (
            ("Obžalovaný Tadeáš Wolf mlčel.", [WOLF], {"JMENO": 1}),
            ("OBŽALOVANÝ TADEÁŠ WOLF", [WOLF], {"JMENO": 1}),
            ("tadeas wolf", [WOLF], {"JMENO": 1}),
            ("Tadeáš\n   Wolf", [WOLF], {"JMENO": 1}),
            ("Tadeáši Wolfovi, Tadeáše Wolfa, s Tadeášem Wolfem", [WOLF], {"JMENO": 3}),
            ("Wolf, Wolfa, Wolfovi, Wolfem, Wolfová", [WOLF_SURNAME], {"PRIJMENI": 5}),
            ("Novákovou, Nováka, Novákové, Nováková, NOVAKEM", [NOVAK], {"PRIJMENI": 5}),
            ("Hájka a Hájkovi", [Value("PRIJMENI", "Hájek")], {"PRIJMENI": 2}),
            ("Ondráč-\nková", [Value("PRIJMENI", "Ondráčková")], {"PRIJMENI": 1}),
            ("Malého, Malému, Malým, Malou", [Value("PRIJMENI", "Malý")], {"PRIJMENI": 4}),
            ("Vojmírovi Hlaváčkovi", [Value("JMENO", "Vojmír Hlaváček")], {"JMENO": 1}),
        ):
            with self.subTest(text=len(text), kind=values[0].kind):
                self.check(text, values, expected)

    def test_numbers_dates_contacts(self):
        for text, values, expected in (
            ("rč 850314/1237, 850314 1237, 8503141237, 850314-1237", [RC], {"RC": 4}),
            ("rč 18503141237 a 85031412370", [RC], {}),
            ("tel. +420 721 345 678, 721345678, 721.345.678, 00420 721 345 678", [PHONE], {"TELEFON": 4}),
            ("tel. 7213456789", [PHONE], {}),
            ("1. 2. 1980, 1.2.1980, 01.02.1980, 1980-02-01, 1. února 1980, 1 ÚNORA 1980", [BIRTH], {"NAROZENI": 6}),
            ("11. 2. 1980 a 1. 2. 1981", [BIRTH], {}),
            ("tadeas.wolf@example.test, TADEAS.WOLF@EXAMPLE.TEST, tadeas.wolf@jiny.test", [MAIL], {"EMAIL": 3}),
            ("č. ú. 123456789/0800 a 123 456 789 / 0800", [ACCOUNT], {"UCET": 2}),
            ("OP č.: 204 516 973", [PAPER], {"DOKLAD": 1}),
            ("IČO: 12 34 56 78", [COMPANY], {"ICO": 1}),
            ("bytem Dlouhá 15, Kolín; v Dlouhé ulici 15; DLOUHÁ  15", [STREET], {"ADRESA": 3}),
            ("Dlouhá 150", [STREET], {}),
        ):
            with self.subTest(kind=values[0].kind, size=len(text)):
                self.check(text, values, expected)

    def test_negative_cases(self):
        self.check("Byl to malý rozdíl.", [MALY], {})
        self.check("Malý rozdíl to nebyl. Malý přišel.", [MALY], {})
        self.check("Svědek Malý a pan Malého syn.", [MALY], {"PRIJMENI": 2})
        self.check("[OSOBA-1] a [OSOBA-2], [TELEFON-1], [RC-1], [EMAIL-1]", [WOLF, WOLF_SURNAME, NOVAK, PHONE, RC, MAIL], {})
        self.check("1 2 3 4 5 6 7 8 9", [ACCOUNT, PHONE], {})

    def test_overlaps_count_once(self):
        self.check("Tadeáš Wolf, tadeas.wolf@example.test", [WOLF, WOLF_SURNAME, MAIL], {"JMENO": 1, "EMAIL": 1})

    def test_layers_of_files(self):
        document = Document()
        document.core_properties.author = "Tadeáš Wolf"
        docx = self.root / "a.docx"
        document.save(docx)
        self.assertEqual(dict(scan_texts(layers(docx), [WOLF])), {"JMENO": 1})

        packed = self.root / "b.bin"
        with zipfile.ZipFile(packed, "w") as archive:
            archive.writestr("slozka/Wolf.txt", "nic")
            archive.writestr("vnoreny.zip", self.zip_bytes({"Novak.txt": "nic"}))
        self.assertEqual(dict(scan_texts(layers(packed), [WOLF_SURNAME, NOVAK])), {"PRIJMENI": 2})

        escaped = self.root / "c.rtf"
        escaped.write_bytes(b"{\\rtf1\\ansi\\ansicpg1250 H\\'e1jek a Nov\\u225?kov\\u225?\\par}")
        self.assertEqual(dict(scan_texts(layers(escaped), [Value("PRIJMENI", "Hájek"), NOVAK])), {"PRIJMENI": 2})

        message = EmailMessage()
        message["Subject"] = "=?utf-8?b?" + base64.b64encode("Tadeáš Wolf".encode()).decode() + "?="
        message["From"] = "x@example.test"
        message.set_content("Tělo")
        message.add_attachment("Telefon 721 345 678".encode(), maintype="text", subtype="plain",
                               filename="=?utf-8?b?" + base64.b64encode("Novák.txt".encode()).decode() + "?=")
        mail = self.root / "d.eml"
        mail.write_bytes(message.as_bytes())
        self.assertEqual(dict(scan_texts(layers(mail), [WOLF, NOVAK, PHONE])), {"JMENO": 1, "PRIJMENI": 1, "TELEFON": 1})

        pdf = self.root / "e.pdf"
        pdf.write_bytes(spis.text_pdf(["Telefon 721 345 678"]))
        counted = dict(scan_texts(layers(pdf), [PHONE]))
        self.assertEqual(counted, {"TELEFON": 1})

        xml = self.root / "f.xml"
        xml.write_text('<a><b hodnota="Wolf"/><!-- 721 345 678 --><c>Nov<span>ák</span></c></a>', encoding="utf-8")
        self.assertEqual(dict(scan_texts(layers(xml), [WOLF_SURNAME, PHONE, NOVAK])),
                         {"PRIJMENI": 2, "TELEFON": 1})

    @staticmethod
    def zip_bytes(members):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return stream.getvalue()

    @needs_ocr
    def test_ocr_of_image_layers(self):
        lines = [["Žádost o doplnění spisu", "Telefon 721 345 678", "Děkujeme za spolupráci"]]
        pdf = self.root / "sken.pdf"
        pdf.write_bytes(spis.scan_pdf(lines))
        self.assertEqual(dict(scan_texts(layers(pdf), [PHONE])), {})
        self.assertEqual(dict(scan_texts(layers(pdf, TESSDATA), [PHONE])), {"TELEFON": 1})
        png = self.root / "obraz.png"
        png.write_bytes(spis.png(lines[0]))
        self.assertEqual(dict(scan_texts(layers(png, TESSDATA), [PHONE])), {"TELEFON": 1})
        tiff = self.root / "sken.tif"
        tiff.write_bytes(spis.tiff(lines))
        self.assertEqual(dict(scan_texts(layers(tiff, TESSDATA), [PHONE])), {"TELEFON": 1})

    def test_report_contains_no_values(self):
        values = [value for person in spis.PEOPLE.values() for value in person.values()] + spis.STAFF
        counter = scan_texts(["Tadeáš Wolf, 721 345 678"], [WOLF, PHONE]) + scan_texts(["x"], [])
        text = report(counter)
        self.assertEqual(report(scan_texts(["nic"], [WOLF])), "Úniky: 0")
        self.assertTrue(text.startswith("Úniky podle druhu: "))
        for value in values:
            self.assertNotIn(value.text.lower(), text.lower(), "Zpráva obsahuje osobní údaj.")
        for value in (WOLF, PHONE):
            self.assertNotIn(value.text, text, "Zpráva obsahuje osobní údaj.")
        self.assertTrue(re.fullmatch(r"Úniky podle druhu: (?:[A-Z]+ \d+)(?:, [A-Z]+ \d+)*", text))


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.target = Path(cls.scratch.name) / "spis.zip"
        cls.values = spis.build_spis(cls.target, scan_pages=2)
        with zipfile.ZipFile(cls.target) as archive:
            cls.files = {name: archive.read(name) for name in archive.namelist()}

    def one(self, suffix):
        found = [data for name, data in self.files.items() if name.endswith(suffix)]
        self.assertTrue(found, "V souboru chybí požadovaný formát.")
        return found[0]

    def test_formats(self):
        with zipfile.ZipFile(io.BytesIO(self.one(".docx"))) as archive:
            self.assertIn("word/document.xml", archive.namelist())
            self.assertIn("word/header1.xml", archive.namelist())
        document = Document(io.BytesIO(self.one("01_zalobni_navrh.docx")))
        self.assertEqual([cell.text for cell in document.tables[0].rows[0].cells],
                         ["Jméno a příjmení", "Datum narození", "Telefon", "Poznámka"])
        self.assertIn(spis.CASE, document.sections[0].header.paragraphs[0].text)
        self.assertTrue(document.sections[0].footer.paragraphs[0].text)
        doc = self.one(".doc")
        with olefile.OleFileIO(io.BytesIO(doc)) as ole:
            self.assertTrue(ole.exists("WordDocument"))
        with pymupdf.open(stream=self.one("04_rozsudek_12C345-2026.pdf"), filetype="pdf") as pdf:
            self.assertGreaterEqual(len(pdf), 4)
            for number, page in enumerate(pdf, 1):
                text = page.get_text()
                self.assertIn(spis.HEADER, text)
                self.assertIn(f"strana {number} z {len(pdf)}", text)
        with pymupdf.open(stream=self.one("07_sken_podani_Ondrackova.pdf"), filetype="pdf") as pdf:
            self.assertEqual(len(pdf), 2)
            for page in pdf:
                self.assertEqual(page.get_text().strip(), "")
                self.assertEqual(len(page.get_images()), 1)
        tiff = self.one(".tif")
        self.assertTrue(tiff.startswith(b"II*\0"))
        with pymupdf.open(stream=tiff, filetype="tiff") as scan:
            self.assertEqual(len(scan), 2)
        self.assertTrue(self.one(".rtf").startswith(b"{\\rtf"))
        with zipfile.ZipFile(io.BytesIO(self.one(".odt"))) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertIn("meta.xml", archive.namelist())
        with zipfile.ZipFile(io.BytesIO(self.one(".xlsx"))) as archive:
            self.assertIn("xl/sharedStrings.xml", archive.namelist())
        self.assertTrue(self.one(".png").startswith(b"\x89PNG"))

    def test_mail_quote_and_attachment(self):
        from email import message_from_bytes, policy

        reply = message_from_bytes(self.one("10_zprava_Pospisil.eml"), policy=policy.default)
        names = [part.get_filename() for part in reply.walk() if part.get_filename()]
        self.assertEqual(names, ["plna_moc_Pospisil.docx"])
        body = next(part for part in reply.walk() if part.get_content_type() == "text/plain").get_content()
        self.assertIn("-----Původní zpráva-----", body)
        self.assertIn("\n> ", body)
        original = message_from_bytes(self.one("11_puvodni_zprava.eml"), policy=policy.default).get_content()
        self.assertIn(original.splitlines()[1], body)

    def test_nested_zip_duplicates_and_templates(self):
        with zipfile.ZipFile(io.BytesIO(self.one(".zip"))) as nested:
            self.assertEqual(len(nested.namelist()), 2)
        copies = [data for name, data in self.files.items() if "potvrzeni_o_zaplaceni" in name]
        self.assertEqual(len(copies), 2)
        self.assertEqual(hashlib.sha256(copies[0]).digest(), hashlib.sha256(copies[1]).digest())
        # The same long notice in three decisions, a certification clause and a judge's signature.
        notice = spis.NOTICE
        self.assertGreater(len(notice), 200)
        texts = {}
        for name in ("03_vyslech_Wolf.docx", "05_platebni_rozkaz.odt", "04_rozsudek_12C345-2026.pdf"):
            data = next(data for key, data in self.files.items() if key.endswith(name))
            texts[name] = " ".join(" ".join(layer.split()) for layer in
                                   layers_of_bytes(data, name))
            self.assertIn(" ".join(notice.split()), texts[name])
            self.assertIn("Shodu s prvopisem potvrzuje", texts[name])
            self.assertIn("JUDr. Alena Rýdlová, soudkyně", texts[name])
        self.assertTrue(any("Wolf" in name for name in self.files), "Název souboru neobsahuje příjmení.")

    def test_values_are_complete_and_inserted(self):
        self.assertEqual({value.kind for value in self.values}, set(KINDS))
        people = {person.last for person in spis.PEOPLE.values()}
        self.assertGreaterEqual(len(people), 8)
        self.assertEqual([value.text for value in self.values if value.common], ["Jan", "Malý"])
        # Every value is found in a text layer (the images carry only a part of them).
        texts = list(layers_of_bytes(self.target.read_bytes(), "spis.zip"))
        missing = [value.kind for value in self.values if not scan_texts(texts, [value])]
        self.assertEqual(missing, [], "Některé vložené hodnoty nejsou ve spisu.")


def layers_of_bytes(data, name, tessdata=None):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / Path(name).name
        path.write_bytes(data)
        yield from list(layers(path, tessdata))


class WholePathTests(unittest.TestCase):
    """The synthetic case file through the batch, once as one ZIP and once unpacked."""

    @needs_ocr
    def test_zip_and_unpacked_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "spis.zip"
            values = spis.build_spis(archive, scan_pages=2)
            totals = {}
            for mode in ("zip", "batch"):
                source, target = root / f"vstup-{mode}", root / f"vystup-{mode}"
                source.mkdir()
                target.mkdir()
                if mode == "zip":
                    (source / "spis.zip").write_bytes(archive.read_bytes())
                else:
                    with zipfile.ZipFile(archive) as packed:
                        packed.extractall(source)
                results = list(process_folders(source, target, TESSDATA))
                released = [result for result in results if result[1] is not None]
                if mode == "batch":
                    self.assertTrue(released, "Dávka nevydala žádný soubor.")
                counted = scan_tree(target, values, TESSDATA)
                totals[mode] = counted
                print(f"\n[{mode}] vydáno {len(released)} z {len(results)}; {report(counted)}")
                for kind in ZERO_LEAK_KINDS:
                    self.assertEqual(counted[kind], 0, f"Únik druhu {kind} v režimu {mode}.")
            self.assertTrue(all(isinstance(total, dict) for total in totals.values()))


class PerformanceTests(unittest.TestCase):
    """Memory and speed of the path through a big case file; synthetic data only."""

    def test_control_zip_streams_a_big_member(self):
        import tracemalloc

        from anonymizer.formats import redact_archive
        from anonymizer.rules import Rules

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import benchmark_spis

        size = 40_000_000
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            big = root / "velka.pdf"
            benchmark_spis.big_pdf(big, size)
            source, target = root / "vstup.zip", root / "vystup.zip"
            with zipfile.ZipFile(source, "w") as packed:
                packed.write(big, "velka.pdf", compress_type=zipfile.ZIP_STORED)
            big.unlink()
            self.assertGreater(source.stat().st_size, size)
            tracemalloc.start()
            try:
                skipped = redact_archive(source, target, Rules())
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertEqual(skipped, [], "Velká položka nebyla zpracována.")
            self.assertLess(peak, size // 4, "Položka archivu se při kontrolní kopii načetla celá do paměti.")
            with zipfile.ZipFile(target) as result:
                self.assertEqual(result.namelist(), ["polozka-0001.pdf"])
                self.assertLess(result.getinfo("polozka-0001.pdf").file_size, 1_000_000)

    def test_control_zip_keeps_order_and_codes(self):
        from anonymizer.formats import NOT_PROCESSED, redact_archive
        from anonymizer.rules import Rules

        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "vstup.zip", Path(folder) / "vystup.zip"
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, "w") as inner:
                inner.writestr("a.txt", f"Telefon {spis.PEOPLE['wolf'].phone}.")
                inner.writestr("b.bin", b"\x00\x01\x02 neznamy format")
            with zipfile.ZipFile(source, "w") as packed:
                packed.writestr("1.txt", f"Telefon {spis.PEOPLE['novak'].phone}.")
                packed.writestr("2.bin", b"\x00\x01\x02 neznamy format")
                packed.writestr("3.zip", nested.getvalue())
                packed.writestr("4.txt", "Bez osobních údajů.")
                packed.writestr("5.docx", spis.docx(lambda document: spis.with_frame(document, ["Text bez údajů."])))
            from anonymizer.rules import Rules as Fresh

            skipped = redact_archive(source, target, Fresh())
            self.assertEqual(skipped, [(2, "archive_member_unsupported"), (3, "archive_member_unsupported")])
            with zipfile.ZipFile(target) as result:
                self.assertEqual(sorted(result.namelist()),
                                 sorted(["polozka-0001.txt", "polozka-0003.zip", "polozka-0004.txt", "polozka-0005.docx",
                                         NOT_PROCESSED]))
                self.assertNotIn(spis.PEOPLE["novak"].phone, result.read("polozka-0001.txt").decode())
                with zipfile.ZipFile(io.BytesIO(result.read("polozka-0003.zip"))) as inner:
                    self.assertEqual(inner.namelist()[0], "polozka-0001.txt")
                    self.assertNotIn(spis.PEOPLE["wolf"].phone, inner.read("polozka-0001.txt").decode())

    def test_given_names_are_rebuilt_only_after_a_change(self):
        from anonymizer.rules import Rules

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import benchmark_spis

        rules = Rules()
        self.assertEqual(rules.given_identities(), {})
        previous = rules.given_identities()
        for number in range(60):
            person = benchmark_spis.make_person(number)
            rules.replace(f"Svědek {person.name} byl vyslechnut. Potom {person.first} odešel.")
            self.assertEqual(rules.given_identities(), rules.build_given_identities())
            if number:
                self.assertIsNot(rules.given_identities(), previous)
            self.assertIs(rules.given_identities(), rules.given_identities())
            previous = rules.given_identities()

    def test_black_fill_equals_set_rect(self):
        from anonymizer.documents import fill_black

        def sheet():
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 97, 61), False)
            pixmap.clear_with(200)
            return pixmap

        for area in (pymupdf.IRect(3, 4, 50, 20), pymupdf.IRect(0, 0, 97, 61), pymupdf.IRect(96, 60, 97, 61)):
            fast, slow = sheet(), sheet()
            self.assertTrue(fill_black(fast, area))
            self.assertTrue(slow.set_rect(area, (0, 0, 0)))
            self.assertEqual(bytes(fast.samples), bytes(slow.samples))
        gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 10, 10), False)
        gray.clear_with(255)
        self.assertTrue(fill_black(gray, pymupdf.IRect(1, 1, 5, 5)))
        self.assertEqual(tuple(gray.pixel(2, 2)), (0,))

    def test_leak_detector_shortcut_gives_the_old_counts(self):
        import re

        from anonymizer import leaks

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import benchmark_spis

        people = list(spis.PEOPLE.values()) + [benchmark_spis.make_person(i) for i in range(12)]
        values = [value for person in people for value in person.values()]
        texts = []
        for person in people:
            digits = person.rc.replace("/", "")
            texts += [f"Svědek {person.name}, nar. {person.date('padded')}, bytem {person.address}, tel. +420 {person.phone}.",
                      f"{person.last.upper()} {person.first}; RČ {digits[:6]} \u2013 {digits[6:]}; č. ú. {person.account}",
                      f"Doklad {person.document[:3]}\u2010{person.document[3:]}, e-mail {person.email.upper()}.",
                      f"{person.date('long')}; {person.date('iso')}; pan {person.last[:-1]}\u0131m",
                      "Nic osobního, jen věta o smlouvě z 1. 2. 2020, č. 12345."]
        texts.append("")

        def old(texts):
            """The detector as it was before: every pattern runs on every text."""
            found = leaks.collections.Counter()
            jobs = []
            for value in dict.fromkeys(values):
                for pattern in leaks._patterns(value):
                    check = (leaks._common_ok if value.common and value.kind == "PRIJMENI" else
                             leaks._capitalized if value.kind == "KRESTNI" else
                             leaks._grouped if value.kind in {"RC", "ICO", "UCET", "DOKLAD", "TELEFON"} else None)
                    jobs.append((leaks.PRIORITY.index(value.kind), value.kind, re.compile(pattern, re.IGNORECASE), check))
            for text in texts:
                plain = leaks._plain(text)
                spans = []
                for priority, kind, expression, check in jobs:
                    for match in expression.finditer(plain):
                        if match.end() > match.start() and (check is None or check(plain, match)):
                            spans.append((priority, match.start(), match.end(), kind))
                covered = bytearray(len(plain))
                for _, start, end, kind in sorted(spans):
                    if not any(covered[start:end]):
                        covered[start:end] = b"\1" * (end - start)
                        found[kind] += 1
            return found

        counted = scan_texts(texts, values)
        self.assertEqual(counted, old(texts))
        self.assertGreater(sum(counted.values()), len(people) * 5)

    @needs_ocr
    def test_benchmark_script_runs_in_its_smallest_configuration(self):
        import subprocess

        script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_spis.py"
        done = subprocess.run([sys.executable, "-I", str(script), "--pages", "2", "--mb", "3", "--rows", "30",
                               "--tessdata", TESSDATA], capture_output=True, text=True, timeout=900,
                              env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.assertEqual(done.returncode, 0, "Měřicí skript selhal.")
        self.assertIn("Čas celkem", done.stdout)
        self.assertIn("Špička paměti", done.stdout)
        self.assertEqual(done.stdout.count("Úniky: 0"), 2, "Měřicí skript našel únik nebo ho nezkontroloval.")


if __name__ == "__main__":
    unittest.main()
