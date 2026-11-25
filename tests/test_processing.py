import hashlib
import io
import json
import os
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from email.message import EmailMessage
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

from anonymizer.batch import process_batch, process_folders
from anonymizer.documents import ocr_path, recognize_image
from anonymizer import czech, pseudonyms, rules as rules_module
from anonymizer.rules import Rules
from anonymizer.manual import MANUAL_MISMATCH
from anonymizer.reasons import ADVICE, advice, reason_code


def synthetic_file(paragraphs=1000):
    """Deterministic synthetic text that mixes every kind of personal data with filler."""
    names = ["Jan Novák", "Petra Svobodová", "Josef Dvořák", "Karolína Bílá", "Zdeněk Veselý",
             "Marie Černá", "Hynek Kučera", "ZKUŠEBNÍ Pavel Horák"]
    streets = ["Dlouhá 12", "Na Příkopě 7/3", "Školní 128", "U Lesa 5a"]
    cities = ["Praha 1", "Brno", "Nový Bydžov", "Ústí nad Labem"]
    forms = ["a.s.", "a. s.", "s.r.o.", "s. r. o.", "spol. s r.o.", "A.S."]
    firms = ["Zkušební Leasing", "Alfa & Beta", "Stavby Morava", "Hutní závody"]
    filler = ["Soud dále konstatoval, že dokazování bylo provedeno řádně a v souladu se zákonem.",
              "Podle § 12 odst. 3 zákona č. 99/1963 Sb. je třeba postupovat dle zvláštních předpisů.",
              "Odvolání bylo podáno včas a obsahuje všechny náležitosti.", "Částka 12 345 Kč byla uhrazena.",
              "Na jednání dne 3. května 2021 v Praze se dostavili všichni účastníci. Zkušební Leasing ve věci rozhodl."]
    state = 12345
    out = []
    for _ in range(paragraphs):
        state = (state * 1103515245 + 12345) % 2 ** 31
        pick = state >> 8
        name, street, city = names[pick % 8], streets[pick // 8 % 4], cities[pick // 32 % 4]
        firm, form = firms[pick // 128 % 4], forms[pick // 512 % 6]
        day, month, year = pick % 28 + 1, pick % 12 + 1, 1950 + pick % 60
        kind = pick % 14
        if kind == 0:
            line = f"Žalobce {name}, nar. {day}. {month}. {year}, bytem {street}, 11{pick % 10} 00 {city}, podal žalobu."
        elif kind == 1:
            line = f"Žalovaná společnost {firm} {form}, IČO {10000000 + pick % 8999999}, se sídlem {street}, {city}."
        elif kind == 2:
            line = f"Kontakt: tel. +420 777 {pick % 900 + 100} {pick % 900 + 100}, e-mail osoba{pick % 50}@example.test."
        elif kind == 3:
            line = (f"Ve věci sp. zn. {pick % 90 + 1} C {pick % 900 + 1}/20{pick % 20 + 10} rozhodl soud. "
                    f"Srov. rozsudek sp. zn. 21 Cdo {pick % 9000 + 1}/2019.")
        elif kind == 4:
            line = f"Rodné číslo: {pick % 90 + 10}0101/{pick % 9000 + 1000}\nJméno: {name}\nTelefon: 602 {pick % 900 + 100} 222\n"
        elif kind == 5:
            line = f"Rozsudek převzal JUDr. K. Bílá a také {name.split()[-1]} J. dne {day}. {month}. {year}."
        elif kind == 6:
            line = (f"Platba na účet {pick % 900 + 100}-1234567890/0100 proběhla; IBAN CZ65 0800 0000 1920 0014 5399, "
                    f"doklad: OP: AB{pick % 900000 + 100000}, DIČ CZ{10000000 + pick % 8999999}, "
                    f"rodné číslo {pick % 90 + 10}0101/{pick % 9000 + 1000} uvedeno.")
        elif kind == 7:
            line = f"meno: {name}  dátum narodenia: {day}. {month}. {year}\n"
        elif kind == 8:
            line = f"Svědek {name} uvedl, že {firm}\t{form},\nsídlem {street}, {city}, neplnila povinnosti."
        else:
            line = filler[pick % 5]
        out.append(line)
    return "\n".join(out) + "\n"


def token_strings(count):
    """Deterministic strings of fragments that probe the boundaries of every pattern."""
    tokens = ["a.", "s.", "r.", "o.", "a. s.", "s. r. o.", "spol. s r. o.", "spol.", "A.S.", "S.R.O.", "ſ. r. o.",
              ",", " ", "  ", "\t", "\n", "\r\n", ";", ")", "(", "A", "Firma", "Alfa-Beta", "x", "@", "+", ".", "-",
              "_", "1", "7", "123456", "123 456", "/", "12/", "J.", "K.", "J. K.", "Novák", "NOVÁK", "Nov-ák", "ſ",
              "\u212a", "\u0130", "\u0131", "é", ":", ": ", "IČO", "ič", "CZ", "cz", "CZ1234567890", "nar", "NAR.",
              "dat", "DAT.", "datum narození", "bytem", "Bydlí", "sídlem", "trvalé bytem", "adresa", "účet", "účtu",
              "č. ú.", "č.", "ú.", "bankovní spojení", "ban", "tel", "doklad", "OP", "pas", "AB123456", "dne",
              "1. 2. 1980", "1. ledna 1980", "12345678", "+420", "777", "800101/1234", "a@b.cz", "x@y",
              "user.name+tag@sub.example.cz", "Jméno", "Telefon", "Rodné číslo", "meno", "priezvisko", "\u00a0",
              "\u2028", "Ⅰ", "Ⓐ", "JUDr.", "ZKUŠEBNÍ LEASING", "CZ12 3456 7890 1234 5678 9012"]
    state = 987654321
    for _ in range(count):
        state = (state * 1103515245 + 12345) % 2 ** 31
        length = state % 40 + 1
        parts = []
        for _ in range(length):
            state = (state * 1103515245 + 12345) % 2 ** 31
            parts.append(tokens[(state >> 8) % len(tokens)])
        if state % 7 == 0:
            # Company names around the 100 character reach of the pattern.
            state = (state * 1103515245 + 12345) % 2 ** 31
            body = "x" * (85 + state % 30)
            parts.append(f"\n{body}{' ' * ((state >> 8) % 5)} a. s.{',' if state % 2 else ''}")
        yield "".join(parts)


class ProcessingTests(unittest.TestCase):
    def test_company_and_grouped_identifier(self):
        output, _ = Rules().replace("Oprávněná: ZKUŠEBNÍ LEASING a.s., IČO 123 45 678")
        self.assertEqual(output, "Oprávněná: [ORGANIZACE-1], IČO [ICO-1]")

    def test_slovak_register_pdf_is_withheld(self):
        source = self.root / "register.pdf"
        with pdf.open() as document:
            document.new_page().insert_text((60, 90), "Register trestov - synteticky formular")
            document.save(source)
        _, target, error = self.run_batch(source)[0]
        self.assertIsNone(target)
        self.assertIn("nebyl vydán", error)

    def test_selected_batch_only_processes_ready_files(self):
        source, output = self.root / "input", self.root / "output"
        source.mkdir()
        output.mkdir()
        ready = source / "ready.txt"
        ready.write_text(self.email, encoding="utf-8")
        (source / "copying.txt").write_text(self.email, encoding="utf-8")
        results = list(process_folders(source, output, selected=[ready.resolve()]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][1].read_text(encoding="utf-8"), "[EMAIL-1]")

    def test_mixed_structured_formats_and_renamed_zip(self):
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        for name, payload in {
            "plain.txt": "Kontakt osoba@example.test",
            "table.csv": "Kontakt;Poznámka\nosoba@example.test;hodnota\n",
            "table.tsv": "Kontakt\tPoznámka\nosoba@example.test\thodnota\n",
            "data.json": '{"contact":"osoba@example.test"}',
            "data.xml": '<contact value="osoba@example.test"/>',
        }.items():
            (source / name).write_text(payload, encoding="utf-8")
        with ZipFile(source / "archive.bin", "w") as archive:
            archive.writestr("osoba@example.test.txt", "osoba@example.test")
        with ZipFile(source / "bad.zip", "w") as archive:
            archive.writestr("unknown.bin", b"unknown")
        results = list(process_folders(source, target, audit_root=self.root / "audit"))
        self.assertEqual(len(results), 7)
        for original, output, reason in results:
            if original.name == "bad.zip":
                # The unsupported member is left out; the archive itself is released with a list of codes.
                self.assertIsNotNone(output, reason)
                with ZipFile(output) as archive:
                    self.assertEqual(archive.namelist(), ["NEZPRACOVANO.txt"])
                    note = archive.read("NEZPRACOVANO.txt").decode()
                self.assertIn("Položka 1: archive_member_unsupported", note)
                self.assertNotIn("unknown", note)
                continue
            self.assertIsNotNone(output, reason)
            if output.suffix == ".zip":
                with ZipFile(output) as archive:
                    self.assertEqual(archive.namelist(), ["polozka-0001.txt"])
                    self.assertEqual(archive.read("polozka-0001.txt"), b"[EMAIL-1]")
            else:
                self.assertIn(b"[EMAIL-1]", output.read_bytes())
                self.assertNotIn(b"osoba@example.test", output.read_bytes())

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.email = "osoba@example.test"

    def make_pdf(self, name, scanned=False, rotation=0):
        source = self.root / name
        with pdf.open() as doc, pdf.open() as scan:
            page = doc.new_page()
            page.insert_text((72, 90), self.email, fontsize=22)
            page.set_rotation(rotation)
            if scanned:
                sheet = scan.new_page()
                sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=240))
                scan.save(source)
            else:
                doc.set_metadata({"author": "NEPRENOSIT"})
                doc.embfile_add("test.txt", b"NEPRENOSIT")
                doc.save(source)
        return source

    def run_batch(self, *paths, **kwargs):
        return list(process_batch(paths, self.root, **kwargs))

    def check_image(self, target):
        with pdf.open(target) as result:
            page = result[0]
            self.assertEqual(page.get_text(), "")
            self.assertFalse(result.embfile_count())
            self.assertIsNone(page.first_annot)
            self.assertNotIn("NEPRENOSIT", str(result.metadata))
            image = pdf.Pixmap(result, page.get_images()[0][0])
            self.assertGreater(image.color_count(colors=True).get(b"\0\0\0", 0), 10_000)
            return image

    def test_docx_rebuild_and_consistent_aliases(self):
        doc = Document()
        paragraph = doc.add_paragraph("Kontakt ")
        paragraph.add_run("osoba@")
        paragraph.add_run("example.test")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "Testovací Osoba"
        doc.core_properties.author = "NEPRENOSIT"
        doc.sections[0].header.paragraphs[0].text = "Testovací Osoby"
        first = self.root / "01.docx"
        doc.save(first)
        doc = Document()
        doc.add_paragraph("Testovací Osoby, " + self.email)
        second = self.root / "02.docx"
        doc.save(second)
        results = self.run_batch(second, first, alias_lines=["Testovací Osoba | Testovací Osoby"])
        for _, target, reason in results:
            self.assertIsNotNone(target, reason)
            text = "\n".join(p.text for p in Document(target).paragraphs)
            self.assertIn("[EMAIL-1]", text)
            self.assertIn("[VLASTNI-1]", text)
            with ZipFile(target) as archive:
                for entry in archive.namelist():
                    self.assertNotIn(b"NEPRENOSIT", archive.read(entry))
        self.assertIn(self.email, "".join(p.text for p in Document(second).paragraphs))

    def test_pdf_redacts_pixels_and_drops_hidden_data(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                source = self.make_pdf(f"rot{rotation}.pdf", rotation=rotation)
                _, target, reason = self.run_batch(source)[0]
                self.assertIsNotNone(target, reason)
                self.check_image(target)

    def test_ocr_required_and_missing(self):
        source = self.make_pdf("scan.pdf", scanned=True)
        _, target, reason = self.run_batch(source)[0]
        self.assertIsNone(target)
        self.assertIn("ces.traineddata", reason)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_ocr_redacts_scan(self):
        source = self.make_pdf("scan.pdf", scanned=True)
        _, target, reason = self.run_batch(source, tessdata=os.environ["SPISREDAKT_TEST_TESSDATA"])[0]
        self.assertIsNotNone(target, reason)
        image = self.check_image(target)
        with pdf.open("pdf", image.pdfocr_tobytes(language="ces", tessdata=ocr_path(os.environ["SPISREDAKT_TEST_TESSDATA"]))) as checked:
            self.assertNotIn(self.email, checked[0].get_text())

    def test_rules_merge_overlaps_and_normalize_phone(self):
        rules = Rules()
        self.assertEqual(rules.replace("+420 777 222 111 a 777222111")[0],
                         "[TELEFON-1] a [TELEFON-1]")
        self.assertEqual(rules.replace("123456/9999")[0], "[RC-1]")
        self.assertEqual(rules.replace("IČO: 12345678")[0], "IČO: [ICO-1]")

    def test_alias_conflict(self):
        with self.assertRaises(ValueError):
            Rules(["Test A | Test B", "Test C | Test B"])

    def test_czech_legal_context(self):
        rules = Rules()
        source = ("Ke sp. zn.: Nd 321/2026. V Táboře dne 13. 11. 2026. "
                  "Po Františku Hradilovi, nar. dne 12. února 1960, "
                  "posledně bytem Jabloňová 1027, Velké Lhotky, který dne 6. 4. 2020 zemřel. "
                  "Jednání 7. 5. 2026 u JUDr. Petrem Novákem – Tábor, J. Vrby 4.")
        output, count = rules.replace(source)
        self.assertEqual(count, 6)
        for value in ("Hradilovi", "Novákem", "12. února 1960", "Jabloňová", "Velké Lhotky",
                      "J. Vrby", "321/2026", "[UCET-"):
            self.assertNotIn(value, output)
        for value in ("13. 11. 2026", "6. 4. 2020", "7. 5. 2026", "[SPIS-", "[NAROZENI-"):
            self.assertIn(value, output)
        self.assertEqual(rules.replace("František Hradil a Petr Novák")[0], "[OSOBA-2] a [OSOBA-1]")
        self.assertEqual(rules.replace("Datum narození: 12.2.1960")[0], "Datum narození: [NAROZENI-1]")
        self.assertEqual(rules.replace("Účet: 12345678/0800")[0], "Účet: [UCET-1]")

    def test_folders_keep_originals_and_share_labels(self):
        source = self.root / "input"
        destination = self.root / "output"
        source.mkdir()
        destination.mkdir()
        (source / "nested").mkdir()
        (source / "ignore.bin").write_text("ignored")
        (source / "~$lock.docx").write_text("ignored")
        originals = {}
        for path, line in ((source / "a.docx", "Anna Hradilová, bytem Tábor, J. Vrby 4."),
                           (source / "nested" / "b.docx", "Podepsáno Annou Hradilovou.")):
            doc = Document()
            doc.add_paragraph(line)
            doc.save(path)
            originals[path] = path.read_bytes()
        results = list(process_folders(source, destination))
        self.assertEqual(len(results), 2)
        for original, target, reason in results:
            self.assertIsNotNone(target, reason)
            relative = original.relative_to(source.resolve())
            self.assertEqual(target, destination.resolve() / relative.with_stem(relative.stem + "_anonymizovano"))
            self.assertEqual(original.read_bytes(), originals[source / relative])
            self.assertIn("[OSOBA-1]", Document(target).paragraphs[0].text)
        import json
        audit_root = self.root / "audit"
        repeated = list(process_folders(source, destination, audit_root=audit_root))
        for _, target, reason in repeated:
            self.assertIsNotNone(target, reason)
            self.assertTrue(target.name.endswith("_anonymizovano_2.docx"))
        log = next(audit_root.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("a.docx", log)
        self.assertNotIn("Hradil", log)
        events = [json.loads(line) for line in log.splitlines()]
        self.assertEqual(events[-1]["counts"], {"released": 2, "skipped": 1})
        self.assertEqual(sum(event.get("fallback") == "numbered_filename" for event in events), 2)
        with self.assertRaises(ValueError):
            list(process_folders(source, source / "nested"))

    @staticmethod
    def fake_dpapi():
        """Reversible stand-in for DPAPI so the map can be tested on any system."""
        return lambda: (lambda data: b"FAKE" + data[::-1], lambda data: data[4:][::-1])

    def case_folder(self, name, lines):
        source = self.root / name
        source.mkdir()
        for index, line in enumerate(lines):
            doc = Document()
            doc.add_paragraph(line)
            doc.save(source / f"d{index}.docx")
        return source

    def run_labels(self, source, destination):
        texts = {}
        for original, target, reason in process_folders(source, destination):
            self.assertIsNotNone(target, reason)
            texts[original.name] = Document(target).paragraphs[0].text
        return texts

    def test_pseudonyms_persist_between_batches(self):
        destination = self.root / "mapped"
        destination.mkdir()
        first = self.case_folder("b1", ["Anna Hradilová, tel. 777 123 456.", "Karel Dvořák, Karel Dvořák."])
        second = self.case_folder("b2", ["Karel Dvořák a Anna Hradilová, tel. 777 123 456, e-mail: x.y@example.test.",
                                         "Podepsal K. Dvořák."])
        with mock.patch.object(pseudonyms, "protection", self.fake_dpapi()):
            one = self.run_labels(first, destination)
            two = self.run_labels(second, destination)
        self.assertEqual(one["d0.docx"], "[OSOBA-1], tel. [TELEFON-1].")
        self.assertEqual(one["d1.docx"], "[OSOBA-2], [OSOBA-2].")
        self.assertEqual(two["d0.docx"], "[OSOBA-2] a [OSOBA-1], tel. [TELEFON-1], e-mail: [EMAIL-1].")
        # initials join the known full name from the map even though it is absent in this batch
        self.assertEqual(two["d1.docx"], "Podepsal [OSOBA-2].")
        mapfile = (destination / pseudonyms.MAP_NAME).read_bytes()
        keyfile = (destination / pseudonyms.KEY_NAME).read_bytes()
        for secret in ("hradil", "dvořák", "dvorak", "777", "123 456", "example", "x.y"):
            self.assertNotIn(secret.encode("utf-8"), mapfile.lower())
            self.assertNotIn(secret.encode("utf-8"), keyfile.lower())
        # a new label continues after the stored counters
        third = self.case_folder("b3", ["Eva Malá."])
        with mock.patch.object(pseudonyms, "protection", self.fake_dpapi()):
            self.assertEqual(self.run_labels(third, destination)["d0.docx"], "[OSOBA-3].")

    def test_pseudonym_map_fails_safe(self):
        lines = ["Anna Hradilová, Anna Hradilová."]
        # without DPAPI (other systems): no files, labels as without a map
        plain = self.root / "plain"
        plain.mkdir()
        with mock.patch.object(pseudonyms, "protection", lambda: None):
            self.assertEqual(self.run_labels(self.case_folder("p1", lines), plain)["d0.docx"], "[OSOBA-1], [OSOBA-1].")
        self.assertFalse((plain / pseudonyms.MAP_NAME).exists())
        self.assertFalse((plain / pseudonyms.KEY_NAME).exists())
        # a run that finds nothing leaves no map and no key behind
        empty = self.root / "empty"
        empty.mkdir()
        with mock.patch.object(pseudonyms, "protection", self.fake_dpapi()):
            self.run_labels(self.case_folder("p0", ["Obyčejný text bez údajů."]), empty)
        self.assertEqual([p.name for p in empty.iterdir() if p.name in pseudonyms.INTERNAL_NAMES], [])
        # a failing DPAPI does not crash the batch
        def broken():
            raise OSError("DPAPI")
        with mock.patch.object(pseudonyms, "protection", broken):
            self.assertEqual(self.run_labels(self.case_folder("p2", lines), plain)["d0.docx"], "[OSOBA-1], [OSOBA-1].")
        # damaged map, missing key, damaged key: the batch starts from 1 and files stay untouched
        for index, damage in enumerate(("map", "key_missing", "key_bad")):
            folder = self.root / f"damaged{index}"
            folder.mkdir()
            with mock.patch.object(pseudonyms, "protection", self.fake_dpapi()):
                self.run_labels(self.case_folder(f"s{index}", ["Karel Dvořák, Eva Malá."]), folder)
                mapfile, keyfile = folder / pseudonyms.MAP_NAME, folder / pseudonyms.KEY_NAME
                if damage == "map":
                    mapfile.write_bytes(mapfile.read_bytes()[:-9] + b"tampered}")
                elif damage == "key_missing":
                    keyfile.unlink()
                else:
                    keyfile.write_bytes(b"garbage")
                before = mapfile.read_bytes()
                texts = self.run_labels(self.case_folder(f"t{index}", ["Eva Malá, Karel Dvořák."]), folder)
            self.assertEqual(texts["d0.docx"], "[OSOBA-2], [OSOBA-1].")
            self.assertEqual(mapfile.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "DPAPI exists only in Windows")
    def test_real_dpapi_roundtrip(self):
        protect, unprotect = pseudonyms.protection()
        secret = os.urandom(32)
        self.assertNotEqual(protect(secret), secret)
        self.assertEqual(unprotect(protect(secret)), secret)

    def test_word_keeps_formatting_and_redacts_across_runs(self):
        from docx.shared import Pt
        from anonymizer.office import redact_office

        source = self.root / "styled.docx"
        target = self.root / "redacted.docx"
        doc = Document()
        paragraph = doc.add_heading(level=2)
        name = paragraph.add_run("Šárkou ")
        name.font.name = "Cambria"
        name.font.size = Pt(16)
        name.bold = True
        paragraph.add_run("Hrubou")
        paragraph.add_run(" a důležitý text").italic = True
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "ŠÁRKA HRUBÁ"
        doc.save(source)
        redact_office(source, target, Rules())
        result = Document(target)
        paragraph = result.paragraphs[0]
        self.assertEqual(paragraph.style.name, "Heading 2")
        self.assertEqual(paragraph.text, "[OSOBA-1] a důležitý text")
        self.assertEqual(paragraph.runs[0].font.name, "Cambria")
        self.assertEqual(paragraph.runs[0].font.size, Pt(16))
        self.assertTrue(paragraph.runs[0].bold)
        self.assertTrue(paragraph.runs[2].italic)
        self.assertEqual(result.tables[0].cell(0, 0).text, "[OSOBA-1]")

    def test_spreadsheet_and_slide_keep_style_parts(self):
        from anonymizer.office import DRAW, SHEET, redact_office

        cases = [
            (".xlsx", "xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/styles.xml",
             f'<worksheet xmlns="{SHEET}"><sheetData><row><c r="A1" s="4" t="inlineStr"><is><r><rPr><b/></rPr><t>Šárka </t></r><r><t>Hrubá</t></r></is></c><c r="B1" s="2"><f>SUM(C1:C3)</f><v>12</v></c></row></sheetData></worksheet>',
             ('s="4"', '<b', 'SUM(C1:C3)')),
            (".pptx", "ppt/presentation.xml", "ppt/slides/slide1.xml", "ppt/theme/theme1.xml",
             f'<slide xmlns:a="{DRAW}"><a:p><a:r><a:rPr b="1" i="1"/><a:t>Šárka Hrubá</a:t></a:r></a:p></slide>',
             ('b="1"', 'i="1"')),
        ]
        for suffix, main, part, styles, xml, preserved in cases:
            with self.subTest(suffix=suffix):
                source = self.root / ("test" + suffix)
                target = self.root / ("redacted" + suffix)
                with ZipFile(source, "w") as archive:
                    archive.writestr(main, "<root/>")
                    archive.writestr(part, xml)
                    archive.writestr(styles, '<styles><font name="Cambria"/></styles>')
                redact_office(source, target, Rules())
                with ZipFile(target) as archive:
                    data = archive.read(part).decode()
                    self.assertIn("[OSOBA-1]", data)
                    self.assertNotIn("Hrubá", data)
                    for marker in preserved:
                        self.assertIn(marker, data)
                    self.assertIn(b"Cambria", archive.read(styles))

    def test_spreadsheet_columns_by_header(self):
        from anonymizer.office import SHEET, redact_office

        source, target = self.root / "registr.xlsx", self.root / "registr_out.xlsx"
        shared = ["Jméno a příjmení", "Datum narození:", "Datum podpisu", "IČO", "Poznámka",
                  "Karolína Bílá", "Telefon"]
        rows = [
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>6</v></c></row>',
            '<row r="2"><c r="A2" t="s"><v>5</v></c><c r="B2" s="3"><v>29252</v></c><c r="C2" s="3"><v>43831</v></c>'
            '<c r="D2"><v>12345678</v></c><c r="E2"><v>12345678</v></c>'
            '<c r="F2" t="inlineStr"><is><t>777 123 456</t></is></c></row>',
            # Omitted references continue from the previous cell; a repeated value keeps its label.
            '<row r="3"><c t="s"><v>5</v></c><c s="3"><v>29252</v></c></row>',
        ]
        with ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{SHEET}"><workbookPr/></workbook>')
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{SHEET}">' + "".join(
                f"<si><t>{value}</t></si>" for value in shared) + "</sst>")
            archive.writestr("xl/worksheets/sheet1.xml",
                             f'<worksheet xmlns="{SHEET}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
        redact_office(source, target, Rules())
        with ZipFile(target) as archive:
            strings = archive.read("xl/sharedStrings.xml").decode()
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn("Karolína", strings)
        self.assertIn("[OSOBA-1]", strings)
        self.assertIn("Datum podpisu", strings)
        self.assertEqual(sheet.count("[NAROZENI-1]"), 2)
        self.assertNotIn("29252", sheet)
        self.assertIn("<v>43831</v>", sheet)
        self.assertIn("[ICO-1]", sheet)
        self.assertIn('<c r="E2"><v>12345678</v></c>', sheet)
        self.assertIn("[TELEFON-1]", sheet)
        self.assertNotIn("777", sheet)

        formula = self.root / "vzorec.xlsx"
        with ZipFile(formula, "w") as archive:
            archive.writestr("xl/workbook.xml", "<root/>")
            archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{SHEET}"><sheetData>'
                             '<row><c t="inlineStr"><is><t>Rodné číslo</t></is></c></row>'
                             '<row><c t="str"><f>A5</f><v>800101/1234</v></c></row></sheetData></worksheet>')
        with self.assertRaisesRegex(ValueError, "Vzorec"):
            redact_office(formula, self.root / "vzorec_out.xlsx", Rules())

    def test_word_and_slide_tables_by_header(self):
        from docx.shared import Pt
        from anonymizer.office import DRAW, redact_office

        document = Document()
        table = document.add_table(rows=4, cols=4)
        for cell, text in zip(table.rows[0].cells, ["Poznámka", "Jméno a příjmení", "Číslo OP", "Datum podpisu"]):
            cell.text = text
        values = [["bez poznámky", "Karolína Bílá", "AB123456", "3. 4. 2020"],
                  ["", "Karolína Bílá", "OP", "3. 4. 2020"]]
        for row, texts in zip(table.rows[1:], values):
            for cell, text in zip(row.cells, texts):
                cell.text = text
        run = table.cell(1, 1).paragraphs[0].runs[0]
        run.bold = True
        run.font.size = Pt(13)
        # Cells spanning two grid columns keep later columns aligned with their headers.
        table.cell(3, 0).merge(table.cell(3, 1)).text = "Souhrn"
        table.cell(3, 2).text = "AB999999"
        source, target = self.root / "tabulka.docx", self.root / "tabulka_out.docx"
        document.save(source)
        redact_office(source, target, Rules())
        result = Document(target).tables[0]
        self.assertEqual([cell.text for cell in result.rows[1].cells],
                         ["bez poznámky", "[OSOBA-1]", "[DOKLAD-1]", "3. 4. 2020"])
        self.assertEqual([cell.text for cell in result.rows[2].cells],
                         ["", "[OSOBA-1]", "OP", "3. 4. 2020"])
        self.assertEqual(result.cell(3, 0).text, "Souhrn")
        self.assertEqual(result.cell(3, 2).text, "[DOKLAD-2]")
        self.assertTrue(result.cell(1, 1).paragraphs[0].runs[0].bold)
        self.assertEqual(result.cell(1, 1).paragraphs[0].runs[0].font.size, Pt(13))

        slide = self.root / "snimek.pptx"
        cell = '<a:tc{attr}><a:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></a:txBody></a:tc>'
        rows = [["Poznámka", "", "Číslo OP"], ["Karolína", "", "XY777888"]]
        xml = f'<slide xmlns:a="{DRAW}"><a:tbl>' + "".join(
            "<a:tr>" + "".join(cell.format(attr="", text=text) for text in row) + "</a:tr>" for row in rows
        ) + "<a:tr>" + cell.format(attr=' gridSpan="2"', text="Souhrn") + cell.format(attr=' hMerge="1"', text="")
        xml += cell.format(attr="", text="XY608111") + "</a:tr></a:tbl></slide>"
        with ZipFile(slide, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<root/>")
            archive.writestr("ppt/slides/slide1.xml", xml)
        redact_office(slide, self.root / "snimek_out.pptx", Rules())
        with ZipFile(self.root / "snimek_out.pptx") as archive:
            data = archive.read("ppt/slides/slide1.xml").decode()
        self.assertIn("[DOKLAD-1]", data)
        self.assertIn("[DOKLAD-2]", data)
        self.assertNotIn("XY", data)
        self.assertIn("Karolína", data)
        self.assertIn("Souhrn", data)

    def test_form_layouts(self):
        from anonymizer.czech import plan_table

        def plan(*rows):
            return plan_table([list(enumerate(row)) for row in rows])

        # A form table without colons: labels in the first column.
        self.assertEqual(plan(["Jméno a příjmení", "Karolína Bílá"], ["Rodné číslo", "800101/1234"],
                              ["Poznámka", "bez poznámky"]),
                         {(0, 1): "OSOBA", (1, 1): "RC"})
        # Two pairs in a row, with an empty spacer cell as in merged spreadsheet forms.
        self.assertEqual(plan(["Jméno", "", "Karolína Bílá", "Telefon", "777 123 456"],
                              ["Adresa", "", "Dlouhá 5, Praha", "E-mail", "kb@example.test"]),
                         {(0, 2): "OSOBA", (0, 4): "TELEFON", (1, 2): "ADRESA", (1, 4): "EMAIL"})
        # A single "Label:" row above a column table is labelled without breaking the columns.
        self.assertEqual(plan(["Účastník:", "Karolína Bílá", ""], ["Jméno", "Telefon", "Poznámka"],
                              ["Ota Hrubý", "608 111 222", "x"], ["Iva Malá", "", "y"]),
                         {(0, 1): "OSOBA", (2, 0): "OSOBA", (2, 1): "TELEFON", (3, 0): "OSOBA"})
        # Column headers are not mistaken for form pairs, with or without colons.
        self.assertEqual(plan(["Datum narození:", "Datum podpisu", "Jméno", "Poznámka"],
                              ["1. 2. 1980", "3. 4. 2020", "Ota Hrubý", "x"]),
                         {(1, 0): "NAROZENI", (1, 2): "OSOBA"})

        rules = Rules()
        text = ("Jméno a příjmení: Karolína Bílá\nDatum narození: 1. 2. 1980\n"
                "Adresa: Dlouhá 5, 110 00 Praha 1; Tel.: 777 123 456\nDatum podpisu: 3. 4. 2020\nJméno: Poznámka")
        self.assertEqual(rules.replace(text)[0],
                         "Jméno a příjmení: [OSOBA-1]\nDatum narození: [NAROZENI-1]\n"
                         "Adresa: [ADRESA-1]; Tel.: [TELEFON-1]\nDatum podpisu: 3. 4. 2020\nJméno: Poznámka")

        document = Document()
        table = document.add_table(rows=2, cols=2)
        for row, texts in zip(table.rows, [["Jméno", "Karolína Bílá"], ["Číslo OP", "XY777888"]]):
            for cell, value in zip(row.cells, texts):
                cell.text = value
        source, target = self.root / "formular.docx", self.root / "formular_out.docx"
        document.save(source)
        from anonymizer.office import redact_office
        redact_office(source, target, Rules())
        result = Document(target).tables[0]
        self.assertEqual([[cell.text for cell in row.cells] for row in result.rows],
                         [["Jméno", "[OSOBA-1]"], ["Číslo OP", "[DOKLAD-1]"]])

    def test_generated_given_names(self):
        from anonymizer.names import female_forms, male_forms

        self.assertEqual(male_forms("Hynek"), ["hynek", "hynka", "hynkovi", "hynku", "hynkem"])
        self.assertEqual(male_forms("Leoš")[1:], ["leoše", "leošovi", "leoši", "leošem"])
        self.assertIn("olze", female_forms("Olga"))
        self.assertIn("karolíně", female_forms("Karolína"))
        self.assertEqual(female_forms("Natálie"), ["natálie", "natálii", "natálií"])
        self.assertEqual(female_forms("Dagmar"), ["dagmar"])

        rules = Rules()
        text, count = rules.replace("Smlouvu uzavřeli Karolína Bílá a Hynek Dvořák. Hynka Dvořáka zastupuje "
                                    "Olga Kratochvílová, Bohumila Malého zastupuje Ivo Novák. "
                                    "Věc projednal Okresní soud v Praze.")
        # Labels are numbered from the end of the text; declined forms share a label.
        self.assertEqual(text, "Smlouvu uzavřeli [OSOBA-5] a [OSOBA-4]. [OSOBA-4] zastupuje "
                               "[OSOBA-3], [OSOBA-2] zastupuje [OSOBA-1]. Věc projednal Okresní soud v Praze.")
        self.assertEqual(count, 6)

    @staticmethod
    def write_tiff(path, pixmaps):
        """Write an uncompressed multi-page greyscale TIFF without extra dependencies."""
        import struct

        data = bytearray(b"II*\0\0\0\0\0")
        previous = 4
        for pixmap in pixmaps:
            pixels = pixmap.samples
            offset = len(data)
            data += pixels
            if len(data) % 2:
                data += b"\0"
            entries = [(256, 4, pixmap.width), (257, 4, pixmap.height), (258, 3, 8), (259, 3, 1),
                       (262, 3, 1), (273, 4, offset), (277, 3, 1), (278, 4, pixmap.height),
                       (279, 4, len(pixels)), (282, 5, 0), (283, 5, 0), (296, 3, 2)]
            rational = len(data) + 2 + 12 * len(entries) + 4
            directory = len(data)
            struct.pack_into("<I", data, previous, directory)
            data += struct.pack("<H", len(entries))
            for tag, kind, value in entries:
                if kind == 5:
                    value = rational
                data += struct.pack("<HHI", tag, kind, 1) + (struct.pack("<HH", value, 0) if kind == 3
                                                             else struct.pack("<I", value))
            previous = len(data)
            data += b"\0\0\0\0" + struct.pack("<II", 200, 1)
        path.write_bytes(bytes(data))

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_tiff_scan_becomes_redacted_pdf(self):
        pages = []
        for text in (self.email, "Telefon 777 123 456"):
            with pdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72, 90), text, fontsize=22)
                pages.append(page.get_pixmap(dpi=200, colorspace=pdf.csGRAY))
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        self.write_tiff(source / "sken.tif", pages)
        (source / "prejmenovany.bin").write_bytes((source / "sken.tif").read_bytes())
        results = list(process_folders(source, target, tessdata=os.environ["SPISREDAKT_TEST_TESSDATA"]))
        self.assertEqual(len(results), 2)
        for original, output, reason in results:
            self.assertIsNotNone(output, reason)
            self.assertEqual(output.suffix, ".pdf")
            with pdf.open(output) as result:
                self.assertEqual(len(result), 2)
                for page in result:
                    self.assertEqual(page.get_text(), "")
                    image = pdf.Pixmap(result, page.get_images()[0][0])
                    self.assertGreater(image.color_count(colors=True).get(b"\0\0\0", 0), 5_000)
                    remaining = recognize_image(image, os.environ["SPISREDAKT_TEST_TESSDATA"])[0]
                    self.assertEqual(Rules().find(remaining), [], remaining)

    def test_rtf_keeps_formatting(self):
        from anonymizer.rtf import redact_rtf

        document = (rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}"
                    rb"{\info{\author Karol\'edna B\'edl\'e1}{\title Spis}}" b"\n"
                    rb"\uc1\pard\b Smlouvu uzav\'f8eli {\i Karol\'edna} B\u237?l\u225? a Hynek Dvo\'f8\'e1k.\b0\par" b"\n"
                    rb"Tel.: 777 123 456\par" b"\n"
                    rb"{\field{\*\fldinst {HYPERLINK " b'"mailto:kb@example.test"' rb"}}{\fldrslt kb@example.test}}\par"
                    rb"{\*\bkmkstart Poznamka}{\*\bkmkend Poznamka}Datum podpisu: 3. 4. 2020\par}")
        output = redact_rtf(document, Rules())
        self.assertEqual(output, (
            rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
            rb"\uc1\pard\b Smlouvu uzav\'f8eli {\i [OSOBA-2]} a [OSOBA-1].\b0\par" b"\n"
            rb"Tel.: [TELEFON-1]\par" b"\n"
            rb"{\field{\*\fldinst { HYPERLINK " b'"https://example.invalid/"' rb" }}{\fldrslt [EMAIL-1]}}\par"
            rb"{\*\bkmkstart Poznamka}{\*\bkmkend Poznamka}Datum podpisu: 3. 4. 2020\par}"))
        with self.assertRaisesRegex(ValueError, "skryté"):
            redact_rtf(rb"{\rtf1{\*\bkmkstart kb@example.test}Text\par}", Rules())
        with self.assertRaisesRegex(ValueError, "objekt"):
            redact_rtf(rb"{\rtf1{\object\objemb{\*\objdata 00}}\par}", Rules())

    def test_rtf_tables_use_headers_and_form_labels(self):
        from anonymizer.rtf import collect, parse, redact_rtf
        from anonymizer.formats import redact_file

        def row(*cells):
            body = rb"\trowd\cellx2000\cellx4000\cellx6000" b"\n"
            for cell in cells:
                body += rb"\pard\intbl " + cell + rb"\cell" + b"\n"
            return body + rb"\row" + b"\n"

        head = rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
        table = (rb"Pred tabulkou\par" b"\n"
                 + row(rb"{\b Jm\'e9no}", rb"Telefon", rb"Pozn\'e1mka")
                 + row(rb"{\b Xaver Blum}", rb"777 123 456", rb"bez pozn\'e1mky")
                 + rb"\pard Za tabulkou\par}")
        output = redact_rtf(head + table, Rules())
        self.assertNotIn(b"Xaver", output)
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[OSOBA-", output)
        self.assertIn(b"[TELEFON-1]", output)
        self.assertIn(b"bez pozn\\'e1mky", output)
        self.assertIn(rb"\trowd\cellx2000\cellx4000\cellx6000", output)
        self.assertIn(rb"{\b [OSOBA-", output)
        self.assertIn(b"Za tabulkou", output)
        collect(parse(output))

        form = (head + row(rb"Rodn\'e9 \'e8\'edslo:", rb"800101/1234", b"")
                + row(rb"Jm\'e9no", rb"Xaver Blum", b"") + rb"\pard\par}")
        output = redact_rtf(form, Rules())
        self.assertNotIn(b"800101", output)
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[RC-1]", output)
        self.assertIn(b"[OSOBA-", output)

        split = head + row(rb"Jm\'e9no", b"", b"") + row(rb"Xaver\par Blum", b"", b"") + rb"\pard\par}"
        output = redact_rtf(split, Rules())
        self.assertNotIn(b"Xaver", output)
        self.assertNotIn(b"Blum", output)
        self.assertEqual(output.count(b"[OSOBA-1]"), 1)

        grouped = (head + rb"\trowd\cellx3000\cellx6000" b"\n"
                   rb"\pard\intbl {Jm\'e9no\cell}{Telefon\cell}\row" b"\n"
                   rb"\trowd\cellx3000\cellx6000" b"\n"
                   rb"\pard\intbl {Xaver Blum\cell}{777 123 456\cell}\row\pard Konec\par}")
        output = redact_rtf(grouped, Rules())
        self.assertNotIn(b"Blum", output)
        self.assertIn(b"[OSOBA-1]", output)
        self.assertIn(b"Konec", output)

        nested = (head + row(rb"Jm\'e9no", rb"Telefon", b"")
                  + rb"\trowd\cellx2000\cellx4000" b"\n"
                  rb"\pard\intbl Xaver Blum\par" b"\n"
                  rb"\pard\intbl\itap2 Jan Tester 777 123 456\nestcell"
                  rb"{\*\nesttableprops\trowd\cellx1000\nestrow}"
                  rb"\pard\intbl\cell\pard\intbl x\cell\row\pard\par}")
        output = redact_rtf(nested, Rules())
        self.assertNotIn(b"777 123", output)
        self.assertNotIn(b"Blum", output)
        collect(parse(output))

        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory, "a.rtf"), Path(directory, "b.rtf")
            source.write_bytes(head + table)
            redact_file(source, target, ".rtf", Rules(), None)
            self.assertNotIn(b"Blum", target.read_bytes())

    def test_rtf_adjacent_tables_are_planned_separately(self):
        from anonymizer.rtf import redact_rtf

        def row(edges, *cells):
            body = rb"\trowd" + b"".join(rb"\cellx%d" % edge for edge in edges) + b"\n"
            for cell in cells:
                body += rb"\pard\intbl " + cell + rb"\cell" + b"\n"
            return body + rb"\row" + b"\n"

        columns, form = (1000, 2000, 3000), (1500, 3000)
        document = (rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Calibri;}}" b"\n"
                    + row(columns, rb"Jm\'e9no", rb"Telefon", rb"Pozn\'e1mka")
                    + row(columns, rb"Xaver\par Blum", rb"777 123 456", rb"bez pozn\'e1mky")
                    + row(form, rb"Rodn\'e9 \'e8\'edslo:", rb"800101/1234")
                    + row(form, rb"Jm\'e9no", rb"Oto Kramerius") + rb"\pard\par}")
        output = redact_rtf(document, Rules())
        for word in (b"Xaver", b"Blum", b"Kramerius", b"800101", b"777 123"):
            self.assertNotIn(word, output)
        self.assertIn(b"bez pozn\\'e1mky", output)
        self.assertEqual(output.count(b"[OSOBA-"), 2)

    def test_opendocument_text_and_spreadsheet(self):
        from anonymizer.formats import identify, redact_file

        office = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
        xlink = "http://www.w3.org/1999/xlink"
        namespaces = f'xmlns:office="{office}" xmlns:text="{text}" xmlns:table="{table}" xmlns:xlink="{xlink}"'
        content = (
            f'<office:document-content {namespaces}><office:body><office:text>'
            '<text:p text:style-name="P1">Smlouvu uzavřeli <text:span text:style-name="T1">Karolína</text:span>'
            '<text:s/>Bílá a<text:s text:c="2"/>Hynek Dvořák.</text:p>'
            f'<text:p><text:a xlink:href="mailto:kb@example.test">kb@example.test</text:a></text:p>'
            '<table:table table:name="Seznam"><table:table-row>'
            '<table:table-cell office:value-type="string"><text:p>Jméno</text:p></table:table-cell>'
            '<table:table-cell office:value-type="string"><text:p>Datum narození</text:p></table:table-cell>'
            '</table:table-row><table:table-row>'
            '<table:table-cell office:value-type="string"><text:p>Ota Hrubý</text:p></table:table-cell>'
            '<table:table-cell office:value-type="date" office:date-value="1980-02-01"><text:p>01.02.80</text:p></table:table-cell>'
            '</table:table-row></table:table></office:text></office:body></office:document-content>')
        meta = (f'<office:document-meta {namespaces} xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:creator>Karolína Bílá</dc:creator>'
                '<meta:generator>Test</meta:generator></office:meta></office:document-meta>')
        manifest = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                    '<manifest:file-entry manifest:full-path="Thumbnails/thumbnail.png" manifest:media-type="image/png"/>'
                    '</manifest:manifest>')
        source, target = self.root / "smlouva.bin", self.root / "smlouva_out.odt"
        with ZipFile(source, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr("content.xml", content)
            archive.writestr("meta.xml", meta)
            archive.writestr("styles.xml", f'<office:document-styles {namespaces}/>')
            archive.writestr("META-INF/manifest.xml", manifest)
            archive.writestr("Thumbnails/thumbnail.png", b"\x89PNG")
        self.assertEqual(identify(source.read_bytes(), ".bin"), ".odt")
        redact_file(source, target, ".odt", Rules(), None)
        with ZipFile(target) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertNotIn("Thumbnails/thumbnail.png", archive.namelist())
            self.assertNotIn(b"Thumbnails", archive.read("META-INF/manifest.xml"))
            self.assertNotIn("Karolína".encode(), archive.read("meta.xml"))
            result = archive.read("content.xml").decode()
        for value in ("Karolína", "Bílá", "Dvořák", "kb@example", "Hrubý", "1980-02-01", "01.02.80"):
            self.assertNotIn(value, result)
        # Tables are planned first; the removed single space keeps the following text.
        self.assertIn('text:style-name="T1">[OSOBA-3]</text:span> a<text:s text:c="2"/>[OSOBA-2].', result)
        self.assertIn("<text:p>[OSOBA-1]</text:p>", result)
        self.assertIn('office:value-type="string"><text:p>[NAROZENI-1]</text:p>', result)
        self.assertIn("https://example.invalid/", result)

    @staticmethod
    def compound_file(streams):
        """Build a minimal OLE compound file; ``streams`` maps "a/b" paths to bytes."""
        import struct

        tree = {"children": {}}
        for path, data in streams.items():
            node = tree
            parts = path.split("/")
            for part in parts[:-1]:
                node = node["children"].setdefault(part, {"children": {}})
            node["children"][parts[-1]] = {"data": data}
        entries = []

        def add(name, node, kind):
            index = len(entries)
            entries.append({"name": name, "kind": kind, "node": node, "child": 0xFFFFFFFF, "right": 0xFFFFFFFF})
            previous = None
            for child_name, child in node.get("children", {}).items():
                child_index = add(child_name, child, 2 if "data" in child else 1)
                if previous is None:
                    entries[index]["child"] = child_index
                else:
                    entries[previous]["right"] = child_index
                previous = child_index
            return index

        add("Root Entry", tree, 5)
        sector, mini = 512, 64
        # Streams below 4096 bytes live in the mini stream; the test data are all small.
        ministream, minifat = b"", []
        for entry in entries:
            data = entry["node"].get("data")
            if data is None:
                entry["start"], entry["size"] = 0xFFFFFFFE, 0
                continue
            assert len(data) < 4096
            count = max(1, -(-len(data) // mini))
            first = len(ministream) // mini
            minifat += [first + i + 1 for i in range(count - 1)] + [0xFFFFFFFE]
            ministream += data.ljust(count * mini, b"\0")
            entry["start"], entry["size"] = first, len(data)
        directory_sectors = -(-len(entries) * 128 // sector)
        minifat_sectors = max(1, -(-len(minifat) * 4 // sector))
        stream_sectors = max(1, -(-len(ministream) // sector))
        total = directory_sectors + minifat_sectors + stream_sectors
        fat_sectors = 1
        while -(-(total + fat_sectors) * 4 // sector) > fat_sectors:
            fat_sectors += 1
        fat = [0xFFFFFFFD] * fat_sectors
        position = fat_sectors
        layout = {}
        for name, count in (("directory", directory_sectors), ("minifat", minifat_sectors),
                            ("stream", stream_sectors)):
            layout[name] = position
            fat += [position + i + 1 for i in range(count - 1)] + [0xFFFFFFFE]
            position += count
        fat += [0xFFFFFFFF] * (fat_sectors * sector // 4 - len(fat))
        entries[0]["start"], entries[0]["size"] = layout["stream"], len(ministream)
        header = struct.pack("<8s16sHHHHH6sIIIIIIIII", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\0" * 16, 0x3E, 3,
                             0xFFFE, 9, 6, b"\0" * 6, 0, fat_sectors, layout["directory"], 0, 4096,
                             layout["minifat"], minifat_sectors, 0xFFFFFFFE, 0)
        header += b"".join(struct.pack("<I", i) for i in range(fat_sectors))
        header += b"\xff" * (sector - len(header))
        minifat += [0xFFFFFFFF] * (minifat_sectors * sector // 4 - len(minifat))
        chunks = [b"".join(struct.pack("<I", value) for value in minifat),
                  ministream.ljust(stream_sectors * sector, b"\0")]
        directory = b""
        for entry in entries:
            name = (entry["name"] + "\0").encode("utf-16-le")
            directory += struct.pack("<64sHBBIII16sIQQIQ", name, len(name), entry["kind"], 1, 0xFFFFFFFF,
                                     entry["right"], entry["child"], b"\0" * 16, 0, 0, 0,
                                     entry.get("start", 0xFFFFFFFE), entry.get("size", 0))
        directory = directory.ljust(directory_sectors * sector, b"\0")
        return header + b"".join(struct.pack("<I", value) for value in fat) + directory + b"".join(chunks)

    def word_file(self, main, header="", paragraphs=None, bad_page=False):
        """Build a minimal DOC. ``paragraphs`` lists (in_table, ttp, itap) for the leading
        paragraphs (ending with \\r or \\x07); None leaves out PlcBtePapx."""
        import struct

        text = main + header
        ends = [index + 1 for index, char in enumerate(text) if char in "\r\x07"]
        starts = [0] + ends[:-1]
        ranges = list(zip(starts, ends))
        flags = list(paragraphs or []) + [(0, 0, 0)] * (len(ranges) - len(paragraphs or []))
        runs = []
        for (first, last), (in_table, ttp, itap) in zip(ranges, flags):
            grpprl = b""
            if in_table:
                grpprl += struct.pack("<HB", 0x2416, 1)
            if ttp:
                grpprl += struct.pack("<HB", 0x2417, 1)
            if itap:
                grpprl += struct.pack("<Hi", 0x6649, itap)
            runs.append((first, last, grpprl))
        chunks = [runs[index:index + 6] for index in range(0, len(runs), 6)]
        base = 1024 + 512 * len(chunks)
        pages = []
        for chunk in chunks:
            page = bytearray(512)
            edges = [base + 2 * chunk[0][0]] + [base + 2 * last for _, last, _ in chunk]
            struct.pack_into(f"<{len(edges)}I", page, 0, *edges)
            top = 511
            for number, (_, _, grpprl) in enumerate(chunk):
                content = b"\0\0" + grpprl
                if len(content) % 2 == 0:
                    content += b"\0"
                top -= len(content) + 1
                top -= top % 2
                page[top] = (len(content) + 1) // 2
                page[top + 1:top + 1 + len(content)] = content
                page[4 * (len(chunk) + 1) + 13 * number] = top // 2
            page[511] = len(chunk)
            pages.append(bytes(page))
        word = bytearray(1024)
        struct.pack_into("<HHH", word, 0, 0xA5EC, 0xC1, 0)
        struct.pack_into("<H", word, 0x0A, 0x0200)
        struct.pack_into("<H", word, 32, 14)
        struct.pack_into("<H", word, 62, 22)
        struct.pack_into("<ii", word, 64 + 12, len(main), 0)
        struct.pack_into("<i", word, 64 + 20, len(header))
        struct.pack_into("<H", word, 152, 93)
        plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, base, 0)
        clx = b"\x02" + struct.pack("<I", len(plc)) + plc
        struct.pack_into("<II", word, 154 + 33 * 8, 0, len(clx))
        table = clx
        if paragraphs is not None:
            numbers = [2 + index + (100 if bad_page else 0) for index in range(len(chunks))]
            edges = [base + 2 * chunk[0][0] for chunk in chunks] + [base + 2 * len(text)]
            papx = struct.pack(f"<{len(edges)}I", *edges) + struct.pack(f"<{len(numbers)}I", *numbers)
            struct.pack_into("<II", word, 154 + 13 * 8, len(clx), len(papx))
            table += papx
        for page in pages:
            word += page
        word += text.encode("utf-16-le")
        return self.compound_file({"WordDocument": bytes(word), "1Table": table})

    def doc_structure(self, doc):
        """Convert DOC to DOCX and check that no visible character was lost or duplicated."""
        from collections import Counter
        from docx.oxml.ns import qn
        from anonymizer.legacy import clean, doc_text, doc_to_docx

        result = Document(io.BytesIO(doc_to_docx(doc)))
        shown = "".join(node.text or "" for node in result.element.body.iter(qn("w:t")))
        given = clean(doc_text(doc)[0])

        def count(value):
            return Counter(char for char in value if not char.isspace() and char != "\x07")

        self.assertEqual(count(given), count(shown))
        return result

    def test_legacy_word_tables_by_paragraph_properties(self):
        from anonymizer.formats import redact_file
        from anonymizer.legacy import doc_to_docx

        T, ROW = (1, 0, 1), (1, 1, 1)
        grid = "Jméno\ra příjmení\x07Telefon\x07\x07Karolína\rBílá\x07777 123 456\x07\x07"
        props = [T, T, T, ROW, T, T, T, ROW]
        doc = self.word_file(grid, "", props)
        result = self.doc_structure(doc)
        self.assertEqual(len(result.tables), 1)
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["Jméno\na příjmení", "Telefon"], ["Karolína\nBílá", "777 123 456"]])
        source, target = self.root / "tabulka.doc", self.root / "tabulka_out.docx"
        source.write_bytes(doc)
        redact_file(source, target, ".doc", Rules(), None)
        cells = [[cell.text for cell in row.cells] for row in Document(target).tables[0].rows]
        self.assertEqual(cells[1][1], "[TELEFON-1]")
        # The two-line heading ("Jméno" / "a příjmení") marks a name column, so the
        # whole cell gets a label (its empty second paragraph remains).
        self.assertEqual(cells[1][0], "[OSOBA-1]\n")

        # Text around the table stays in paragraphs and the table does not end early.
        doc = self.word_file("Před\r" + grid + "Za\r", "", [(0, 0, 0)] + props)
        result = self.doc_structure(doc)
        self.assertEqual([paragraph.text for paragraph in result.paragraphs if paragraph.text], ["Před", "Za"])
        self.assertEqual(len(result.tables), 1)
        self.assertEqual(len(result.tables[0].rows), 2)

        # A nested table is flattened into the cell of the outer table.
        main = "Úvod\rX\rVnitřní1\x07Vnitřní2\x07\x07\x07Druhá\x07\x07Konec\r"
        props = [(0, 0, 0), T, (1, 0, 2), (1, 0, 2), (1, 1, 2), T, T, ROW]
        result = self.doc_structure(self.word_file(main, "", props))
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["X\nVnitřní1\nVnitřní2", "Druhá"]])
        self.assertEqual([paragraph.text for paragraph in result.paragraphs if paragraph.text], ["Úvod", "Konec"])

        # Damaged paragraph properties give the same result as no properties at all.
        main = "Jméno\x07Telefon\x07\x07Karolína\rBílá\x07777\x07\x07Konec\r"
        expected = self.word_file(main)
        damaged = self.word_file(main, "", [T, T, ROW, T, T, T, ROW], bad_page=True)

        def body(data):
            with ZipFile(io.BytesIO(doc_to_docx(data))) as archive:
                return archive.read("word/document.xml")

        self.assertEqual(body(damaged), body(expected))
        self.doc_structure(damaged)

    def test_email_formats(self):
        from email import policy
        from email.message import EmailMessage
        from email.parser import BytesParser
        from anonymizer.formats import identify, redact_file

        inner = EmailMessage()
        inner["From"] = "Ota Hrubý <ota@example.test>"
        inner["Subject"] = "Přeposlaná zpráva"
        inner.set_content("Volejte 777 123 456.")
        message = EmailMessage()
        message["From"] = "Xaver Blum <xb@example.test>"
        message["To"] = "Karolína Bílá <kb@example.test>, ota@example.test"
        message["Subject"] = "Spis Karolíny Bílé"
        message["Received"] = "from mail.example.test (10.0.0.1)"
        message["Message-ID"] = "<abc@example.test>"
        message.set_content("Dobrý den, Karolína Bílá, rodné číslo 800101/1234.")
        message.add_alternative('<p>Dobrý den, <b>Karolína</b> Bílá. <a href="mailto:kb@example.test">napište</a>'
                                '<img src="https://tracker.example.test/x.png"><script>alert(1)</script></p>',
                                subtype="html")
        message.make_mixed()
        attachment = EmailMessage()
        attachment.set_content(b"Kontakt kb@example.test", "text", "plain", disposition="attachment",
                               filename="Karolína Bílá.txt")
        message.attach(attachment)
        wrapper = EmailMessage()
        wrapper.set_content(inner)
        message.attach(wrapper)
        source, target = self.root / "posta.eml", self.root / "posta_out.eml"
        source.write_bytes(message.as_bytes(policy=policy.SMTP))
        self.assertEqual(identify(source.read_bytes(), ".eml"), ".eml")
        redact_file(source, target, ".eml", Rules(), None)
        output = target.read_bytes()
        result = BytesParser(policy=policy.default).parsebytes(output)
        for value in (b"Karol", b"Xaver", b"example.test", b"800101", b"10.0.0.1", b"tracker", b"<script"):
            self.assertNotIn(value, output)
        self.assertIsNone(result["Received"])
        self.assertIsNone(result["Message-ID"])
        self.assertEqual(str(result["From"]), '"[OSOBA-1]" <email-1@anonymizovano.invalid>')
        self.assertEqual(str(result["To"]), '"[OSOBA-2]" <email-2@anonymizovano.invalid>, '
                                            "email-3@anonymizovano.invalid")
        parts = {}
        for part in result.walk():
            parts.setdefault(part.get_content_type(), part)
        self.assertIn("[OSOBA-2], rodné číslo [RC-1].", parts["text/plain"].get_content())
        self.assertIn("<b>[OSOBA-2]</b>.", parts["text/html"].get_content())
        attached = [part for part in result.walk() if part.get_filename()]
        self.assertEqual(attached[0].get_filename(), "priloha-0001.txt")
        self.assertEqual(attached[0].get_content(), b"Kontakt [EMAIL-2]")
        nested = next(part for part in result.walk() if part.get_content_type() == "message/rfc822")
        self.assertIn("[TELEFON-1]", nested.get_content().get_content())

        def utf16(text):
            return text.encode("utf-16-le")

        msg = self.compound_file({
            "__substg1.0_0037001F": utf16("Spis Karolíny Bílé"),
            "__substg1.0_0C1A001F": utf16("Xaver Blum"),
            "__substg1.0_0C1F001F": utf16("xb@example.test"),
            "__substg1.0_1000001F": utf16("Paní Karolína Bílá, tel. 777 123 456."),
            "__recip_version1.0_#00000000/__substg1.0_3001001F": utf16("Karolína Bílá"),
            "__recip_version1.0_#00000000/__substg1.0_3003001F": utf16("kb@example.test"),
            "__attach_version1.0_#00000000/__substg1.0_3707001F": utf16("seznam.csv"),
            "__attach_version1.0_#00000000/__substg1.0_37010102": "Jméno;Telefon\nOta Hrubý;608 111 222\n".encode(),
        })
        source, target = self.root / "zprava.msg", self.root / "zprava_out.eml"
        source.write_bytes(msg)
        self.assertEqual(identify(msg, ".bin"), ".msg")
        redact_file(source, target, ".msg", Rules(), None)
        output = target.read_bytes()
        self.assertNotIn(b"example.test", output)
        result = BytesParser(policy=policy.default).parsebytes(output)
        self.assertEqual(str(result["To"]), '"[OSOBA-2]" <email-2@anonymizovano.invalid>')
        body = next(part for part in result.walk() if part.get_content_type() == "text/plain")
        self.assertEqual(body.get_content().strip(), "Paní [OSOBA-2], tel. [TELEFON-1].")
        attached = next(part for part in result.walk() if part.get_filename())
        self.assertEqual(attached.get_content(), "Jméno;Telefon\r\n[OSOBA-3];[TELEFON-2]\r\n".encode())

    def test_legacy_word_and_excel(self):
        import struct
        from anonymizer.formats import identify, output_suffix, redact_file
        from anonymizer.office import SHEET

        main = ("Smlouva s Karolínou Bílou\r"
                "Jméno\x07Telefon\x07\x07Xaver Blum\x07777 123 456\x07\x07"
                "Pole \x13 HYPERLINK \"mailto:kb@example.test\" \x14odkaz\x15 a obrázek \x01\r")
        header = "Zpracovala Karolína Bílá\r"
        text = main + header
        word = bytearray(1024)
        struct.pack_into("<HHH", word, 0, 0xA5EC, 0xC1, 0)
        struct.pack_into("<H", word, 0x0A, 0x0200)
        struct.pack_into("<H", word, 32, 14)
        struct.pack_into("<H", word, 62, 22)
        struct.pack_into("<ii", word, 64 + 12, len(main), 0)
        struct.pack_into("<i", word, 64 + 20, len(header))
        struct.pack_into("<H", word, 152, 93)
        plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, 1024, 0)
        clx = b"\x02" + struct.pack("<I", len(plc)) + plc
        struct.pack_into("<II", word, 154 + 33 * 8, 0, len(clx))
        word += text.encode("utf-16-le")
        doc = self.compound_file({"WordDocument": bytes(word), "1Table": clx})
        self.assertEqual(identify(doc, ".bin"), ".doc")
        self.assertEqual(output_suffix(".doc"), ".docx")
        source, target = self.root / "stary.doc", self.root / "stary_out.docx"
        source.write_bytes(doc)
        redact_file(source, target, ".doc", Rules(), None)
        result = Document(target)
        paragraphs = [paragraph.text for paragraph in result.paragraphs]
        self.assertEqual(paragraphs[0], "Smlouva s [OSOBA-2]")
        self.assertEqual(paragraphs[1], "Pole odkaz a obrázek [OBRÁZEK VYNECHÁN]")
        self.assertEqual(paragraphs[-1], "Zpracovala [OSOBA-2]")
        self.assertEqual([[cell.text for cell in row.cells] for row in result.tables[0].rows],
                         [["Jméno", "Telefon"], ["[OSOBA-1]", "[TELEFON-1]"]])
        with ZipFile(target) as archive:
            self.assertNotIn(b"example.test", archive.read("word/document.xml"))

        try:
            import xlwt
        except ImportError:
            self.skipTest("Pro vytvoření testovacího XLS chybí xlwt.")
        from datetime import date
        from io import BytesIO

        book = xlwt.Workbook()
        sheet = book.add_sheet("Seznam")
        for column, value in enumerate(["Jméno", "Datum narození", "Telefon", "Poznámka"]):
            sheet.write(0, column, value)
        sheet.write(1, 0, "Xaver Blum")
        sheet.write(1, 1, date(1980, 2, 1), xlwt.easyxf(num_format_str="DD.MM.YYYY"))
        sheet.write(1, 2, 777123456)
        sheet.write(1, 3, "bez poznámky")
        stream = BytesIO()
        book.save(stream)
        self.assertEqual(identify(stream.getvalue(), ".bin"), ".xls")
        source, target = self.root / "stary.xls", self.root / "stary_out.xlsx"
        source.write_bytes(stream.getvalue())
        redact_file(source, target, ".xls", Rules(), None)
        with ZipFile(target) as archive:
            data = archive.read("xl/worksheets/sheet1.xml").decode()
        for value in ("Xaver", "29252", "777123456"):
            self.assertNotIn(value, data)
        for value in ("[OSOBA-1]", "[NAROZENI-1]", "[TELEFON-1]", "bez poznámky", f'xmlns="{SHEET}"'):
            self.assertIn(value, data)

    def test_word_from_office_template(self):
        """Typical office document: template path, mail merge, co-authors, split fields, EMF logo."""
        from anonymizer.office import redact_office
        from anonymizer.reasons import reason_code

        document = Document()
        paragraph = document.add_paragraph("Kontakt: ")
        source, target = self.root / "sablona.docx", self.root / "sablona_out.docx"
        document.save(source)
        relationship = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        patched = self.root / "sablona_patched.docx"
        with ZipFile(source) as original, ZipFile(patched, "w") as archive:
            for entry in original.infolist():
                data = original.read(entry)
                if entry.filename == "word/document.xml":
                    field = (f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> HYPER'
                             f'</w:instrText></w:r><w:r><w:instrText xml:space="preserve">LINK "mailto:kb@</w:instrText></w:r>'
                             f'<w:r><w:instrText>example.test"</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                             f'<w:r><w:t>napište nám</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
                             f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> MERGEFIELD '
                             f'</w:instrText></w:r><w:r><w:instrText>"Karolína Bílá"</w:instrText></w:r>'
                             f'<w:r><w:fldChar w:fldCharType="end"/></w:r>')
                    marker = '<w:t xml:space="preserve">Kontakt: </w:t></w:r>'
                    self.assertIn(marker, data.decode())
                    data = data.decode().replace(marker, marker + field).encode()
                elif entry.filename == "word/settings.xml":
                    data = data.decode().replace("</w:settings>",
                                                 f'<w:attachedTemplate xmlns:r="{relationship}" r:id="rIdTemplate"/>'
                                                 '<w:mailMerge><w:query w:val="SELECT * FROM C:\\Users\\karolina.bila\\adresy.xlsx"/>'
                                                 "</w:mailMerge></w:settings>").encode()
                elif entry.filename == "word/_rels/document.xml.rels":
                    data = data.decode().replace("</Relationships>",
                                                 f'<Relationship Id="rIdLogo" Type="{relationship}/image" Target="media/image1.emf"/>'
                                                 f'<Relationship Id="rIdPeople" Type="http://schemas.microsoft.com/office/2011/relationships/people" Target="people.xml"/>'
                                                 "</Relationships>").encode()
                elif entry.filename == "[Content_Types].xml":
                    data = data.decode().replace("</Types>", '<Default Extension="emf" ContentType="image/x-emf"/>'
                                                 '<Override PartName="/word/people.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"/>'
                                                 "</Types>").encode()
                archive.writestr(entry, data)
            archive.writestr("word/_rels/settings.xml.rels",
                             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                             f'<Relationship Id="rIdTemplate" Type="{relationship}/attachedTemplate" '
                             'Target="file:///C:\\Users\\karolina.bila\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm" '
                             'TargetMode="External"/></Relationships>')
            archive.writestr("word/media/image1.emf", b"\x01\x00\x00\x00EMF")
            archive.writestr("word/people.xml", '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
                             '<w15:person w15:author="Karolína Bílá"><w15:presenceInfo w15:providerId="AD" '
                             'w15:userId="kb@example.test"/></w15:person></w15:people>')
        redact_office(patched, target, Rules())
        with ZipFile(target) as archive:
            names = archive.namelist()
            everything = b"".join(archive.read(name) for name in names)
            self.assertIn("word/media/image1_emf.png", names)
            self.assertNotIn("word/media/image1.emf", names)
            self.assertNotIn("word/people.xml", names)
            self.assertIn(b'Target="media/image1_emf.png"', archive.read("word/_rels/document.xml.rels"))
            self.assertIn(b'Extension="png"', archive.read("[Content_Types].xml"))
            self.assertNotIn(b"attachedTemplate", archive.read("word/settings.xml"))
        for leaked in (b"karolina.bila", b"Karol\xc3\xadna", b"example.test", b"Normal.dotm", b"people.xml"):
            self.assertNotIn(leaked, everything)
        self.assertIn(b"https://example.invalid/", everything)
        self.assertEqual(Document(target).paragraphs[0].text, "Kontakt: napište nám")
        self.assertEqual(reason_code(ValueError("OCR nenašlo čitelný text. Stránku zkontrolujte ručně.")),
                         "pdf_page_without_ocr_text")

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_pdf_scanner_quirks(self):
        """Repaired cross-references, a blank back page, a photo page and an oversized drawing."""
        from anonymizer.documents import redact_pdf

        tessdata = os.environ["SPISREDAKT_TEST_TESSDATA"]
        with pdf.open() as text_doc, pdf.open() as scan:
            page = text_doc.new_page()
            page.insert_text((72, 90), self.email, fontsize=22)
            sheet = scan.new_page()
            sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=200))
            white = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 560), False)
            white.clear_with(255)
            scan.new_page().insert_image(pdf.Rect(0, 0, 595, 842), pixmap=white)
            photo = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 560), False)
            photo.clear_with(90)
            scan.new_page().insert_image(pdf.Rect(0, 0, 595, 842), pixmap=photo)
            data = scan.tobytes()
        # Break the cross-reference offset; MuPDF repairs such files when opening them.
        source = self.root / "sken.pdf"
        source.write_bytes(data.replace(b"startxref\n", b"startxref\n9").replace(b"startxref\r\n", b"startxref\r\n9"))
        with pdf.open(source) as check:
            self.assertTrue(check.is_repaired)
        target = self.root / "sken_out.pdf"
        redact_pdf(source, target, Rules(), tessdata)
        with pdf.open(target) as result:
            self.assertEqual(len(result), 3)
            pages = [pdf.Pixmap(result, page.get_images()[0][0]) for page in result]
        self.assertGreater(pages[0].color_count(colors=True).get(b"\0\0\0", 0), 5_000)
        self.assertEqual(set(pages[1].color_count(colors=True)), {b"\xff\xff\xff"})
        self.assertEqual(set(pages[2].color_count(colors=True)), {b"\0\0\0"})

        poster = self.root / "vykres.pdf"
        with pdf.open() as doc:
            doc.new_page(width=4000, height=4000).insert_text((72, 90), self.email, fontsize=40)
            doc.save(poster)
        redact_pdf(poster, self.root / "vykres_out.pdf", Rules(), tessdata)
        with pdf.open(self.root / "vykres_out.pdf") as result:
            image = pdf.Pixmap(result, result[0].get_images()[0][0])
            self.assertLessEqual(image.width * image.height, 30_000_000)

    def test_archive_with_documents(self):
        from io import BytesIO
        from anonymizer.formats import ARCHIVE_BYTES, size_limit

        self.assertEqual(size_limit(".zip"), ARCHIVE_BYTES)
        self.assertEqual(ARCHIVE_BYTES, 500_000_000)
        word = BytesIO()
        document = Document()
        document.add_paragraph("Smlouvu uzavřela Karolína Bílá.")
        document.save(word)
        with pdf.open() as doc:
            doc.new_page().insert_text((72, 90), self.email, fontsize=16)
            portable = doc.tobytes()
        inner = BytesIO()
        with ZipFile(inner, "w") as archive:
            archive.writestr("poznamka.txt", "Telefon 777 123 456")
        source, target = self.root / "input", self.root / "output"
        source.mkdir()
        target.mkdir()
        with ZipFile(source / "spis.zip", "w") as archive:
            archive.writestr("1/smlouva.docx", word.getvalue())
            archive.writestr("2/podani.pdf", portable)
            archive.writestr("3/prilohy.zip", inner.getvalue())
        [(original, output, reason)] = list(process_folders(source, target))
        self.assertIsNotNone(output, reason)
        with ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ["polozka-0001.docx", "polozka-0002.pdf", "polozka-0003.zip"])
            self.assertEqual(Document(BytesIO(archive.read("polozka-0001.docx"))).paragraphs[0].text,
                             "Smlouvu uzavřela [OSOBA-1].")
            with pdf.open(stream=archive.read("polozka-0002.pdf")) as result:
                self.assertEqual(result[0].get_text(), "")
            with ZipFile(BytesIO(archive.read("polozka-0003.zip"))) as nested:
                self.assertEqual(nested.read("polozka-0001.txt"), b"Telefon [TELEFON-1]")

    def test_case_law_citations_stay(self):
        rules = Rules()
        text = ("nikoliv však již dobu přesahující 12 let (srov. usnesení Krajského soudu v Brně ze dne "
                "26. 5. 2023, č. j. 70 Co 123/2023-45).\nV řešené věci byla exekuce vedena na základě usnesení "
                "Okresního soudu v Uherském Hradišti ze dne 22. 3. 2010, č. j. 12 Nc 3456/2010-7, které nabylo "
                "právní moci. Náhrada nepříslušela (mutatis mutandis usnesení Nejvyššího soudu ze dne 20. 12. 2016, "
                "sp. zn. 20 Cdo 4567/2016). Dovolání (sp. zn. 26 Cdo 12/2024) bylo odmítnuto. Nejvyšší soud zrušil "
                "usnesení Okresního soudu v Kroměříži ze dne 1. 2. 2020, č. j. 12 C 1/2020-5. Rozsudek Nejvyššího "
                "soudu ze dne 1. 1. 2020, sp. zn. 21 Ko 5/2020. Návrh ze dne 3. 2. 2023, sp. zn. 120 EX 1234/10, "
                "exekutor zamítl.")
        result = rules.replace(text)[0]
        for kept in ("70 Co 123/2023-45", "20 Cdo 4567/2016", "26 Cdo 12/2024", "21 Ko 5/2020"):
            self.assertIn(kept, result)
        for removed in ("12 Nc 3456/2010-7", "12 C 1/2020-5", "120 EX 1234/10"):
            self.assertNotIn(removed, result)
        self.assertIn("Hradišti ze dne 22. 3. 2010, č. j. [SPIS-", result)

    def test_initials(self):
        rules = Rules()
        lines = ["Žalobce Jan Novák podal žalobu.", "Rozsudek převzal J. Novák dne 3. 4.",
                 "Podle Nováka J. je věc jasná.", "Zástupce: JUDr. K. Bílá, advokátka",
                 "Podepsala M. Dvořáková.", "Svědek P. Horák, nar. 1. 1. 1970, uvedl",
                 "A. Úvod", "Č. Budějovice", "Viz bod B. Smlouvy"]
        self.assertEqual([rules.replace(line)[0] for line in lines], [
            "Žalobce [OSOBA-1] podal žalobu.", "Rozsudek převzal [OSOBA-1] dne 3. 4.",
            "Podle [OSOBA-1] je věc jasná.", "Zástupce: JUDr. [OSOBA-2], advokátka",
            "Podepsala [OSOBA-3].", "Svědek [OSOBA-4], nar. [NAROZENI-1], uvedl",
            "A. Úvod", "Č. Budějovice", "Viz bod B. Smlouvy"])
        # Two known people with the same surname and initial: no guess, a separate label.
        rules = Rules()
        rules.replace("Jan Novák a Josef Novák")
        self.assertEqual(rules.replace("Podepsal J. Novák.")[0], "Podepsal [OSOBA-3].")

    def test_table_columns_by_header(self):
        from anonymizer.formats import redact_text

        rules = Rules()
        data = ("Jméno a příjmení;Dat. nar.;Datum podpisu;E-mail;Poznámka\n"
                "Karolína Bílá;1. 2. 1980;3. 4. 2020;kb;bez poznámky\n"
                "Karolína Bílá;01.02.1980;3. 4. 2020;;\n").encode()
        output = redact_text(data, ".csv", rules)
        lines = output.decode().splitlines()
        self.assertEqual(lines[1], "[OSOBA-1];[NAROZENI-1];3. 4. 2020;[EMAIL-1];bez poznámky")
        self.assertEqual(lines[2], "[OSOBA-1];[NAROZENI-1];3. 4. 2020;;")
        self.assertEqual(redact_text(output, ".csv", rules), output)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_word_image_and_hyperlink(self):
        from io import BytesIO
        from docx.oxml import OxmlElement
        from docx.opc.constants import RELATIONSHIP_TYPE
        from docx.shared import Inches
        from anonymizer.office import redact_office

        source = self.root / "image.docx"
        target = self.root / "clean.docx"
        doc = Document()
        paragraph = doc.add_paragraph("Kontakt ")
        link = OxmlElement("w:hyperlink")
        link.set("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
                 doc.part.relate_to("mailto:" + self.email, RELATIONSHIP_TYPE.HYPERLINK, is_external=True))
        run = paragraph.add_run(self.email)
        run.bold = True
        link.append(run._element)
        paragraph._element.append(link)
        with pdf.open() as sample:
            page = sample.new_page(width=430, height=90)
            page.insert_text((40, 50), self.email, fontsize=22)
            image = doc.add_picture(BytesIO(page.get_pixmap(dpi=240).tobytes("png")), width=Inches(4))
        crop = OxmlElement("a:srcRect")
        crop.set("l", "5000")
        image._inline.graphic.graphicData.pic.blipFill.insert(1, crop)
        doc.save(source)
        redact_office(source, target, Rules(), os.environ["SPISREDAKT_TEST_TESSDATA"])
        output = Document(target)
        self.assertEqual(output.inline_shapes[0].width, Inches(4))
        with ZipFile(target) as archive:
            xml = archive.read("word/document.xml")
            self.assertIn(b'l="5000"', xml)
            self.assertIn(b"[EMAIL-1]", xml)
            self.assertNotIn(self.email.encode(), archive.read("word/_rels/document.xml.rels"))
            media = next(name for name in archive.namelist() if name.startswith("word/media/"))
            pixmap = pdf.Pixmap(archive.read(media))
            colors = pixmap.color_count(colors=True)
            self.assertGreater(colors.get(bytes(3), 0), 7_000)
            self.assertGreater(colors.get(b"\xff\xff\xff", 0), 10_000)
            text, _, _ = recognize_image(pixmap, os.environ["SPISREDAKT_TEST_TESSDATA"])
            self.assertNotIn(self.email, text)

    @unittest.skipUnless(os.environ.get("SPISREDAKT_TEST_TESSDATA"), "Chybí testovací jazyková data.")
    def test_bmp_ocr_and_mixed_audit(self):
        import json
        from io import BytesIO
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        source, output, audit = (self.root / part for part in ("input", "output", "audit"))
        source.mkdir()
        output.mkdir()
        with pdf.open() as sample:
            page = sample.new_page(width=430, height=90)
            page.insert_text((40, 50), self.email, fontsize=22)
            image = QImage.fromData(page.get_pixmap(dpi=240).tobytes("png"))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(image.save(buffer, "BMP"))
        doc = Document()
        doc.add_picture(BytesIO(bytes(buffer.data())))
        doc.save(source / "private-name.docx")
        (source / "broken.docx").write_bytes(b"invalid")
        (source / "unknown.bin").write_bytes(b"unknown")
        results = list(process_folders(source, output, os.environ["SPISREDAKT_TEST_TESSDATA"], audit))
        self.assertEqual(sum(target is not None for _, target, _ in results), 1, results)
        target = next(target for _, target, _ in results if target)
        with ZipFile(target) as archive:
            bitmap = QImage.fromData(archive.read("word/media/image1.bmp"))
            self.assertFalse(bitmap.isNull())
            png = QBuffer()
            png.open(QIODevice.OpenModeFlag.WriteOnly)
            self.assertTrue(bitmap.save(png, "PNG"))
            pixels = pdf.Pixmap(bytes(png.data()))
            colors = pixels.color_count(colors=True)
            self.assertGreater(colors.get(bytes(3), 0), 7_000)
            self.assertGreater(colors.get(bytes([255, 255, 255]), 0), 10_000)
            text, _, _ = recognize_image(pixels, os.environ["SPISREDAKT_TEST_TESSDATA"])
            self.assertNotIn(self.email, text)
        log = next(audit.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("private-name", log)
        self.assertNotIn(self.email, log)
        self.assertEqual(json.loads(log.splitlines()[-1])["counts"],
                         {"released": 1, "failed": 1, "skipped": 1})

    def folders(self, name):
        source, target = self.root / name / "in", self.root / name / "out"
        source.mkdir(parents=True)
        target.mkdir()
        return source, target

    def test_batch_rejects_large_text_without_loading_it(self):
        import tracemalloc

        source, target = self.folders("large")
        with (source / "big.txt").open("wb") as f:
            for _ in range(45):
                f.write(b"a" * 1_000_000)
        tracemalloc.start()
        try:
            results = list(process_folders(source, target))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual([r[1:] for r in results], [(None, "Vstup překračuje limit velikosti.")])
        self.assertLess(peak, 8_000_000)
        self.assertEqual(list(target.rglob("*")), [])

    def test_batch_rejects_oversized_sparse_file_fast(self):
        import time
        from unittest import mock

        source, target = self.folders("sparse")
        with (source / "huge.pdf").open("wb") as f:
            f.write(b"%PDF-")
            f.truncate(600_000_000)
        started = time.monotonic()
        with mock.patch("anonymizer.batch.copy_limited") as copy:
            results = list(process_folders(source, target))
        self.assertFalse(copy.called)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual([r[1:] for r in results], [(None, "Vstup překračuje limit velikosti.")])

    def test_batch_skips_unknown_sparse_file_without_copying(self):
        from unittest import mock

        source, target = self.folders("unknown")
        with (source / "data.bin").open("wb") as f:
            f.truncate(300_000_000)
        audit = self.root / "unknown-audit"
        with mock.patch("anonymizer.batch.copy_limited") as copy:
            results = list(process_folders(source, target, audit_root=audit))
        self.assertFalse(copy.called)
        self.assertEqual(results, [])
        self.assertEqual(list(target.rglob("*")), [])
        log = next(audit.glob("*/events.jsonl")).read_text(encoding="utf-8")
        self.assertIn('"skipped"', log)
        self.assertIn("unsupported_format", log)

    def test_identify_path_matches_bytes(self):
        from anonymizer.formats import identify

        docx = self.root / "a.docx"
        doc = Document()
        doc.add_paragraph("text")
        doc.save(docx)
        archive = self.root / "a.zip"
        with ZipFile(archive, "w") as z:
            z.writestr("x.txt", "text")
        for path, expected in ((docx, ".docx"), (archive, ".zip")):
            self.assertEqual(identify(path, ".bin"), expected)
            self.assertEqual(identify(path.read_bytes(), ".bin"), expected)

    def test_replace_output_is_stable(self):
        # The hashes come from the implementation before the scanning was sped up.
        text = synthetic_file()
        self.assertEqual(len(text), 81225)
        replaced, count = Rules().replace(text)
        self.assertEqual(count, 2193)
        self.assertEqual(hashlib.sha256(replaced.encode()).hexdigest(),
                         "00a60cf0ead5d8303899091355997d9e3804a01bf701e1105692bf011b4d0c92")
        # Paragraph by paragraph with one shared Rules, as the Office formats do. The hash changed
        # when bare surnames became replaceable (14 lines "... také Bílá J." now give the label
        # instead of "Bílá"); every other line, and the whole-text hash above, are unchanged.
        shared = Rules()
        pieces = [shared.replace(line)[0] for line in text.split("\n")]
        self.assertEqual(hashlib.sha256("\n".join(pieces).encode()).hexdigest(),
                         "7b68591eb14723f353db5eb0ab9fcb6952108b8230d496a9c7a22d2add57e636")
        for kind in ("OSOBA", "ADRESA", "ORGANIZACE", "TELEFON", "EMAIL", "ICO", "SPIS", "RC", "UCET", "IBAN",
                     "DIC", "DOKLAD", "NAROZENI", "OSOBNI_UDAJ"):
            self.assertRegex(replaced, rf"\[{kind}-\d+\]")
        self.assertIn("Jméno: [OSOBA-1]", replaced)
        self.assertIn("21 Cdo", replaced)

    def test_fast_scanning_finds_what_the_patterns_find(self):
        plain = {
            "ORGANIZACE": (czech.company_matches, czech.COMPANY),
            "SLOVAK": (lambda text: czech.colon_lines(czech.SLOVAK_FIELD, text), czech.SLOVAK_FIELD),
            "FORM": (lambda text: czech.colon_lines(czech.FORM_FIELD, text), czech.FORM_FIELD),
            "BIRTH": (lambda text: czech.anchored(czech.BIRTH, czech.BIRTH_START, text), czech.BIRTH),
            "BIRTH_TEXT": (lambda text: czech.anchored(czech.BIRTH_TEXT, czech.BIRTH_START, text), czech.BIRTH_TEXT),
            "ADDRESS": (lambda text: czech.anchored(czech.ADDRESS_CUE, czech.ADDRESS_START, text), czech.ADDRESS_CUE),
            "INITIAL_FIRST": (czech.initial_first_matches, czech.INITIAL_FIRST),
            "INITIAL_LAST": (czech.initial_last_matches, czech.INITIAL_LAST),
        }
        for kind, scan in rules_module.SCANNERS.items():
            plain[kind] = (scan, rules_module.COMPILED[kind])
        found = dict.fromkeys(plain, 0)
        for text in token_strings(4000):
            for kind, (scan, pattern) in plain.items():
                expected = [match.regs for match in pattern.finditer(text)]
                self.assertEqual([match.regs for match in scan(text)], expected, (kind, text))
                found[kind] += len(expected)
        # Every scanner was exercised on real matches.
        self.assertTrue(all(found.values()), found)

    def test_anchor_letters_cover_ignorecase(self):
        # The anchors spell out both cases of a letter; IGNORECASE may add more (ſ, İ, ı).
        every = "".join(map(chr, range(0x110000)))
        for letter in "abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž":
            expected = set(re.compile(re.escape(letter), re.IGNORECASE).findall(every))
            self.assertEqual(set(re.compile(czech.cls(letter)).findall(every)), expected, letter)
        for source in (r"[\w.+-]", r"[\w-]", r"\d", r"\w"):
            self.assertEqual(set(re.compile(source, re.IGNORECASE).findall(every)),
                             set(re.compile(source).findall(every)), source)


class SurnameTests(unittest.TestCase):
    """Bare surnames after the first full name (families of surnames)."""

    @staticmethod
    def labels(text, rules=None):
        rules = rules or Rules()
        return rules.replace(text)[0], rules

    def test_surname_family(self):
        groups = [
            ["Novák", "Nováka", "Novákovi", "Nováku", "Novákem", "Nováková", "Novákové", "Novákovou", "Novak", "NOVÁK"],
            ["Malý", "Malého", "Malému", "Malým", "Malá", "Malé", "Malou", "Maly"],
            ["Hájek", "Hájka", "Hájkovi", "Hájkem", "Hájková", "HÁJEK", "Hajek"],
            ["Wolf", "Wolfa", "Wolfovi", "Wolfem", "Wolfová", "Wolfové"],
            ["Kramerius", "Kramerie", "Krameriovi", "Krameriem"],
            ["Svoboda", "Svobody", "Svobodovi", "Svobodu", "Svobodou", "Svobodová"],
            ["Němec", "Němce", "Němcová"],
        ]
        for group in groups:
            self.assertEqual({czech.surname_family(word)[0] for word in group}, {czech.surname_family(group[0])[0]}, group)
        self.assertEqual(len({czech.surname_family(group[0])[0] for group in groups}), len(groups))
        gender = {word: czech.surname_family(word)[1] for word in
                  ("Novák", "Nováka", "Nováková", "Novákovou", "Malý", "Malá", "Malé", "Malou", "Wolfová")}
        self.assertEqual(gender, {"Novák": "m", "Nováka": "m", "Nováková": "f", "Novákovou": "f", "Malý": "m",
                                  "Malá": "f", "Malé": None, "Malou": None, "Wolfová": "f"})
        self.assertEqual(czech.surname_family("Nováková-Svobodová")[1], "f")

    def test_every_occurrence_of_a_surname_is_replaced(self):
        out, rules = self.labels("Obžalovaný Tadeáš Wolf mlčel. Wolf pak řekl, že Wolfa nikdo neviděl "
                                 "a Wolfovi nikdo nevolal.")
        self.assertEqual(out, "Obžalovaný [OSOBA-1] mlčel. [OSOBA-1] pak řekl, že [OSOBA-1] nikdo neviděl "
                              "a [OSOBA-1] nikdo nevolal.")
        self.assertEqual(rules.find(out), [])
        # The same when the surname comes first and in reverse paragraph order.
        out, _ = self.labels("Wolfovi nikdo nevolal.\nWolf mlčel. Obžalovaný Tadeáš Wolf řekl.")
        self.assertEqual(set(re.findall(r"\[OSOBA-\d+\]", out)), {"[OSOBA-1]"})
        self.assertNotIn("Wolf", out)

    def test_surnames_follow_gender(self):
        out, _ = self.labels("Jan Novák a Jana Nováková. Novák odešel, Nováková zůstala.")
        first, second, third, fourth = re.findall(r"\[OSOBA-\d+\]", out)
        self.assertEqual(third, first)
        self.assertEqual(fourth, second)
        self.assertNotEqual(first, second)
        self.assertEqual(out.count("Novák"), 0)
        # A person known only by the surname and a gender-less form share the label.
        out, _ = self.labels("Svědek Wolfová uvedla, že Wolfovou nikdo nevolal.")
        self.assertEqual(set(re.findall(r"\[OSOBA-\d+\]", out)), {"[OSOBA-1]"})

    def test_ambiguous_surname_gets_a_shared_label(self):
        out, _ = self.labels("Jan Novák a Petr Novák. Novák řekl. Nováková přišla.")
        jan, petr, bare_male, bare_female = re.findall(r"\[OSOBA-\d+\]", out)
        self.assertEqual(len({jan, petr, bare_male}), 3)
        self.assertNotIn(bare_female, {jan, petr, bare_male})
        self.assertNotIn("Novák", out)

    def test_two_phase_collect_then_replace(self):
        rules = Rules()
        rules.collect("Svědek Karel Hájek, nar. 1. 2. 1980, bytem Dlouhá 12, Praha.")
        later = "Hájka jsme předvolali. HÁJEK se nedostavil."
        out = rules.replace(later)[0]
        label = rules.replace("Svědek Karel Hájek")[0].split()[-1]
        self.assertEqual(out, f"{label} jsme předvolali. {label} se nedostavil.")
        self.assertIn("[OSOBA-", label)
        # Without the first phase a lone surname stays: nothing says it is a person.
        self.assertEqual(Rules().replace(later)[0], later)
        # collect returns nothing and registers the labels of every kind.
        self.assertIsNone(rules.collect("Telefon: 602 123 456"))
        self.assertIn(("TELEFON", "602123456"), rules.labels)

    def test_pan_and_roles_with_one_word(self):
        out, _ = self.labels("Přišel pan Kramerius. Kramerius pak uvedl, že Krameriovi nikdo nevěřil.")
        self.assertEqual(set(re.findall(r"\[OSOBA-\d+\]", out)), {"[OSOBA-1]"})
        self.assertNotIn("Kramerius", out)
        out, _ = self.labels("Zeptali jsme se paní Wolfové. Žalobce Brázda tvrdí, že svědkyně Wolfová lhala.")
        self.assertNotIn("Wolf", out)
        self.assertNotIn("Brázda", out)
        # Names of institutions and given names after a role are not surnames.
        for text in ("Žalobce Okresní soud rozhodl. Okresní soud věc vrátil.",
                     "Svědek Jan přišel. Jan odešel.", "Pan Hlavní město. Hlavní město Praha.",
                     "Žalobce Česká republika. Česká republika zaplatí."):
            self.assertEqual(Rules().replace(text)[0], text)

    def test_common_surnames_need_context(self):
        out, _ = self.labels("Pavel Malý podal žalobu. Žalobce Malý tvrdí totéž. Malý rozdíl v ceně nehraje roli. "
                             "Řekl to Malému.")
        self.assertEqual(out, "[OSOBA-1] podal žalobu. Žalobce [OSOBA-1] tvrdí totéž. Malý rozdíl v ceně nehraje roli. "
                              "Řekl to [OSOBA-1].")
        # After a title, an initial and before "nar." the sentence start does not matter.
        out, _ = self.labels("Pavel Černý přišel. Černý den nastal. Mgr. Černý řekl. Černý, nar. 1. 2. 1980.\nČerný pes.")
        self.assertEqual(out.count("Černý"), 2)
        self.assertEqual(out.count("[OSOBA-1]"), 3)
        # A line break inside a sentence is not a sentence start.
        out, _ = self.labels("Pavel Černý přišel. Podal žalobu\nČerný ji vzal zpět.")
        self.assertNotIn("Černý ji", out)

    def test_uncommon_surnames_everywhere(self):
        out, _ = self.labels("Jan Novák přišel. NOVÁK mlčel. Novák řekl. Na místě byli Novakovi a Novak.")
        self.assertNotRegex(out, "(?i)nov[aá]k")

    def test_institutions_and_places_stay(self):
        text = ("Žalobce Petr Kolín podal žalobu. Okresní soud v Kolíně rozhodl. Krajský soud ve Kolíně věc vrátil. "
                "V obci Kolín je pošta. Ulice Kolín a město Kolín. Kolín řekl, že Kolínovi nikdo nevěřil.")
        out, _ = self.labels(text)
        for kept in ("Okresní soud v Kolíně", "Krajský soud ve Kolíně", "obci Kolín", "Ulice Kolín", "město Kolín"):
            self.assertIn(kept, out)
        self.assertIn("[OSOBA-1] řekl, že [OSOBA-1] nikdo", out)

    def test_no_person_no_change(self):
        text = ("Soud dále konstatoval, že Okresní soud v Praze rozhodl. Novák nic neřekl. Malý rozdíl. "
                "Svoboda slova je zaručena. Hora a Zima přišly.")
        out, rules = self.labels(text)
        self.assertEqual(out, text)
        self.assertEqual(rules.families, {})

    def test_initials_still_share_the_label(self):
        rules = Rules()
        lines = ["Žalobce Jan Novák podal žalobu.", "Rozsudek převzal J. Novák dne 3. 4.", "Novák souhlasí.",
                 "Žalobce Petr Dvořák.", "Rozsudek převzal P. Dvořák.", "Dvořák J. přišel."]
        self.assertEqual([rules.replace(line)[0] for line in lines], [
            "Žalobce [OSOBA-1] podal žalobu.", "Rozsudek převzal [OSOBA-1] dne 3. 4.", "[OSOBA-1] souhlasí.",
            "Žalobce [OSOBA-2].", "Rozsudek převzal [OSOBA-2].", "[OSOBA-3] přišel."])

    def test_labels_are_not_scanned(self):
        rules = Rules()
        rules.replace("Žalobce Jan Osoba přišel. Osoba odešel.")
        self.assertEqual(rules.find("Dále [OSOBA-1] a [TELEFON-2]."), [])

    def test_hyphenated_and_ocr_forms(self):
        out, _ = self.labels("Svědkyně Eva Nováková-Svobodová přišla. Pak Svobodová řekla totéž. Novakova odešla.")
        self.assertEqual(set(re.findall(r"\[OSOBA-\d+\]", out)), {"[OSOBA-1]"})
        self.assertNotRegex(out, "(?i)(nov[aá]k|svobod)")

    def test_surfaces_stay_in_memory(self):
        rules = Rules()
        rules.replace("Žalobce Tadeáš Wolf, nar. 1. 2. 1980, bytem Dlouhá 12, Praha. Wolfovi nikdo nevolal.")
        rules.cell("OSOBA", "Karel Hájek")
        self.assertIn("Tadeáš Wolf", rules.surfaces["OSOBA"])
        self.assertIn("Wolfovi", rules.surfaces["OSOBA"])
        self.assertIn("Wolf", rules.surfaces["PRIJMENI"])
        self.assertIn("Hájek", rules.surfaces["PRIJMENI"])
        self.assertIn("1. 2. 1980", rules.surfaces["NAROZENI"])
        self.assertNotIn("Wolf", rules.surfaces["NAROZENI"])
        # No label, message or audit field can carry them: the strings are only ever read from memory.
        base = Path(czech.__file__).parent
        for name in ("audit.py", "reasons.py", "gui.py", "preview.py"):
            self.assertNotIn("surfaces", (base / name).read_text(encoding="utf-8"), name)
        from anonymizer.reasons import ADVICE, REASONS
        found = " ".join([text for text in ADVICE.values()] + [str(item) for item in REASONS])
        for kind in rules.surfaces.values():
            for surface in kind:
                self.assertNotIn(surface, found)
        # Labels never contain a found string.
        out = rules.replace("Žalobce Tadeáš Wolf")[0]
        self.assertNotIn("Wolf", out)


class CaseFileTests(unittest.TestCase):
    """Two-phase batch, Markdown release and the incomplete-archive behaviour."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.target, self.audit = (self.root / name for name in ("in", "out", "audit"))
        self.source.mkdir()
        self.target.mkdir()

    def run_case(self, **kwargs):
        summaries = []
        results = list(process_folders(self.source, self.target, audit_root=self.audit,
                                       summary=summaries.append, **kwargs))
        return results, summaries

    def audit_log(self):
        return next(self.audit.glob("*/events.jsonl")).read_text(encoding="utf-8")

    def case_zip(self):
        word = io.BytesIO()
        document = Document()
        document.add_paragraph("Smlouvu uzavřela Karolína Bílá, telefon 777 123 456.")
        document.save(word)
        with pdf.open() as doc:
            doc.new_page().insert_text((72, 90), "Kontakt spravce.spisu@example.test", fontsize=16)
            portable = doc.tobytes()
        with ZipFile(self.source / "tajny_spis_novak.zip", "w") as archive:
            archive.writestr("slozka_novak/smlouva_novak.docx", word.getvalue())
            archive.writestr("slozka_novak/podani_novak.pdf", portable)
            archive.writestr("zasifrovano_novak.txt", b"zasifrovano")
            archive.writestr("neznamy_novak.bin", b"unknown")

    def test_surname_found_in_later_file_is_replaced_in_earlier_one(self):
        (self.source / "a.txt").write_text("Hájka jsme předvolali.", encoding="utf-8")
        (self.source / "b.txt").write_text("Svědek Karel Hájek, nar. 1. 2. 1980.", encoding="utf-8")
        results, summaries = self.run_case(markdown=True, copies=True)
        self.assertEqual(len(results), 3)
        self.assertIsNone(results[0][0])
        markdown_path = results[0][1]
        self.assertEqual(markdown_path, (self.target / "spis_anonymizovano.md").resolve())
        self.assertEqual(summaries[0]["markdown"], markdown_path)
        self.assertEqual((summaries[0]["documents"], summaries[0]["skipped"], summaries[0]["error"]), (2, [], ""))
        for path in [markdown_path, *(target for _, target, _ in results[1:])]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Hájk", text, path.name)
            self.assertNotIn("1980", text)
        copy = (self.target / "a_anonymizovano.txt").read_text(encoding="utf-8")
        self.assertTrue(copy.startswith("[OSOBA-1]"), copy)

    def test_markdown_only_and_collision(self):
        (self.source / "a.txt").write_text("Telefon 777 123 456.", encoding="utf-8")
        for expected in ("spis_anonymizovano.md", "spis_anonymizovano_2.md"):
            results, _ = self.run_case(markdown=True, copies=False)
            self.assertEqual([(source, target.name) for source, target, _ in results], [(None, expected)])
        self.assertEqual(sorted(path.name for path in self.target.iterdir() if path.name not in pseudonyms.INTERNAL_NAMES),
                         ["spis_anonymizovano.md", "spis_anonymizovano_2.md"])

    def test_zip_with_refused_items(self):
        self.case_zip()
        original = ZipFile.infolist

        def marked(archive):
            # The entry is marked as encrypted without a real cipher.
            result = original(archive)
            for info in result:
                if info.filename == "zasifrovano_novak.txt":
                    info.flag_bits |= 1
            return result

        with mock.patch.object(ZipFile, "infolist", marked):
            results, summaries = self.run_case(markdown=True, copies=True)
        self.assertEqual(len(results), 2)
        markdown_path = results[0][1]
        text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("## Nezpracované položky", text)
        self.assertIn("archive_encrypted", text)
        self.assertIn("archive_member_unsupported", text)
        self.assertEqual(sorted(code for _, code in summaries[0]["skipped"]),
                         ["archive_encrypted", "archive_member_unsupported"])
        self.assertEqual(summaries[0]["documents"], 2)
        self.assertNotIn("Karolína", text)
        self.assertNotIn("777 123", text)
        source, output, error = results[1]
        self.assertIsNotNone(output, error)
        with ZipFile(output) as archive:
            # Numbering follows the input order; the gaps are the refused items.
            self.assertEqual(archive.namelist(), ["polozka-0001.docx", "polozka-0002.pdf", "NEZPRACOVANO.txt"])
            note = archive.read("NEZPRACOVANO.txt").decode()
            self.assertIn("Položka 3: archive_encrypted", note)
            self.assertIn("Položka 4: archive_member_unsupported", note)
            self.assertEqual(Document(io.BytesIO(archive.read("polozka-0001.docx"))).paragraphs[0].text,
                             "Smlouvu uzavřela [OSOBA-1], telefon [TELEFON-1].")
        # Neither the names of the inputs nor of the members appear anywhere.
        everything = text + note + self.audit_log()
        for name in ("novak", "tajny", "slozka", "smlouva", "podani", "zasifrovano", "neznamy"):
            self.assertNotIn(name, everything.lower(), name)
        with ZipFile(output) as archive:
            self.assertNotIn("novak", " ".join(archive.namelist()).lower())
        log = self.audit_log()
        self.assertIn('"state": "released"', log)
        self.assertIn('"skipped_members": [[3, "archive_encrypted"], [4, "archive_member_unsupported"]]', log)
        self.assertIn('"skipped": [[3, "archive_encrypted"], [4, "archive_member_unsupported"]]', log)

    def test_archive_stops_only_on_limits(self):
        from anonymizer import formats

        def build(entries):
            data = io.BytesIO()
            with ZipFile(data, "w") as archive:
                for name, payload in entries:
                    archive.writestr(name, payload)
            return data

        nested = build([("a.txt", "Telefon 777 123 456"), ("b.bin", b"x")])
        outer = build([("c.txt", "text"), ("d.zip", nested.getvalue()), ("e.bin", b"y")])
        result = io.BytesIO()
        skipped = formats.redact_archive(io.BytesIO(outer.getvalue()), result, Rules())
        self.assertEqual(skipped, [(2, "archive_member_unsupported"), (3, "archive_member_unsupported")])
        with ZipFile(result) as archive:
            self.assertEqual(archive.namelist(), ["polozka-0001.txt", "polozka-0002.zip", "NEZPRACOVANO.txt"])
            with ZipFile(io.BytesIO(archive.read("polozka-0002.zip"))) as inner:
                self.assertEqual(inner.namelist(), ["polozka-0001.txt", "NEZPRACOVANO.txt"])
                self.assertEqual(inner.read("polozka-0001.txt"), b"Telefon [TELEFON-1]")
                self.assertIn("Položka 2: archive_member_unsupported", inner.read("NEZPRACOVANO.txt").decode())
        # Nothing missing: no note.
        clean = io.BytesIO()
        self.assertEqual(formats.redact_archive(build([("a.txt", "text")]), clean, Rules()), [])
        with ZipFile(clean) as archive:
            self.assertEqual(archive.namelist(), ["polozka-0001.txt"])
        # A member failing during redaction is left out in every form.
        broken = build([("ok.txt", "text"), ("bad.docx", b"PK\x03\x04not an office file")])
        output = io.BytesIO()
        skipped = formats.redact_archive(broken, output, Rules())
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], 2)
        with ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ["polozka-0001.txt", "NEZPRACOVANO.txt"])
        # The limits still stop the whole archive.
        with mock.patch.object(formats, "ARCHIVE_UNPACKED", 3), self.assertRaises(formats.FormatError) as stopped:
            formats.redact_archive(build([("a.txt", "text")]), io.BytesIO(), Rules())
        self.assertEqual(stopped.exception.code, "archive_limit")
        with self.assertRaises(formats.FormatError) as stopped:
            formats.redact_archive(build([("a.txt", "text")]), io.BytesIO(), Rules(), depth=3)
        self.assertEqual(stopped.exception.code, "archive_depth")
        with self.assertRaises(Exception):
            formats.redact_archive(io.BytesIO(b"not an archive"), io.BytesIO(), Rules())

    def test_release_check_blocks_leaking_markdown(self):
        from anonymizer import markdown as markdown_module
        from anonymizer.markdown import Output

        (self.source / "a.txt").write_text("Svědek Karel Hájek, nar. 1. 2. 1980, rodné číslo 800201/1230.",
                                           encoding="utf-8")
        for leaked in ("Svědek Karel Hájek", "Rodné číslo 800201/1230", "Narozen 1. 2. 1980", "Hájek"):
            for path in self.target.glob("spis_anonymizovano*"):
                path.unlink()
            with mock.patch.object(markdown_module, "render", return_value=Output(text=leaked, documents=1)):
                results, summaries = self.run_case(markdown=True, copies=False)
            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0][1], leaked)
            self.assertIn("nebyl vydán", results[0][2])
            self.assertEqual(reason_code(results[0][2]), "markdown_release_check")
            self.assertEqual(advice(results[0][2]), ADVICE["markdown_release_check"])
            self.assertEqual(summaries[0]["error"], results[0][2])
            self.assertIsNone(summaries[0]["markdown"])
            self.assertEqual(list(self.target.glob("spis_anonymizovano*")), [], leaked)
            self.assertEqual([path for path in self.target.iterdir() if path.name not in pseudonyms.INTERNAL_NAMES], [])
            for forbidden in ("Hájek", "800201", "1980"):
                self.assertNotIn(forbidden, results[0][2])
        log = self.audit_log()
        self.assertIn('"state": "failed"', log)
        self.assertIn("markdown_release_check", log)
        self.assertNotIn("Hájek", log)

    def test_release_check_uses_independent_detector(self):
        from anonymizer.batch import check_release, FormatError

        rules = Rules()
        rules.replace("Svědek Karel Hájek, nar. 1. 2. 1980, e-mail karel@example.test, tel. 777 123 456.")
        check_release(["Svědek [O1], nar. [N1], [E1], [T1]."], rules)
        for leaked in ("Hájek", "Karel Hájek", "karel@example.test", "777 123 456", "narozen 1980-02-01"):
            with self.assertRaises(FormatError, msg=leaked) as stopped:
                check_release([f"Text {leaked} dále."], rules)
            self.assertEqual(stopped.exception.code, "markdown_release_check")

    def test_split_mode_writes_folder(self):
        (self.source / "a.txt").write_text("Svědek Karel Hájek uvedl, že byl doma.", encoding="utf-8")
        (self.source / "b.txt").write_text("Hájka jsme předvolali.", encoding="utf-8")
        for expected in ("spis_anonymizovano", "spis_anonymizovano_2"):
            results, summaries = self.run_case(markdown=True, copies=False, split=True)
            folder = results[0][1]
            self.assertEqual(folder.name, expected)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["00_obsah.md", "D0001.md", "D0002.md"])
            for path in folder.iterdir():
                self.assertNotIn("Hájk", path.read_text(encoding="utf-8"))
            self.assertEqual(summaries[-1]["markdown"], folder)

    def test_progress_runs_over_the_whole_batch(self):
        (self.source / "a.txt").write_text("Text jedna.", encoding="utf-8")
        (self.source / "b.txt").write_text("Text dvě.", encoding="utf-8")
        seen = []
        self.run_case(markdown=True, copies=False, progress=lambda items, pages: seen.append((items, pages)))
        self.assertEqual(max(seen)[0], 2)
        self.assertEqual(seen, sorted(seen))

    def test_progress_error_is_not_swallowed(self):
        (self.source / "a.txt").write_text("Text jedna.", encoding="utf-8")

        def stop(items, pages):
            raise KeyError("stop")

        with self.assertRaises(KeyError):
            self.run_case(markdown=True, copies=False, progress=stop)

    def test_unsupported_and_oversized_inputs_are_listed_as_not_processed(self):
        (self.source / "a.txt").write_text("Text.", encoding="utf-8")
        (self.source / "b.bin").write_bytes(b"unknown")
        results, summaries = self.run_case(markdown=True, copies=False)
        self.assertEqual(summaries[0]["skipped"], [(2, "format_unsupported")])
        self.assertIn("položka 2: format_unsupported", results[0][1].read_text(encoding="utf-8"))

    def test_email_attachment_with_refused_member_still_stops_the_mail(self):
        from anonymizer import formats

        archive = io.BytesIO()
        with ZipFile(archive, "w") as zipped:
            zipped.writestr("a.txt", "text")
            zipped.writestr("b.bin", b"unknown")
        with self.assertRaises(formats.FormatError) as stopped:
            formats.redact_bytes(archive.getvalue(), ".zip", Rules(), None)
        self.assertEqual(stopped.exception.code, "attachment_unsupported")


class CaseFileGuiTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        # The settings live in a file of the test; the real user profile is never touched.
        self.ini = str(self.root / "settings.ini")
        patcher = mock.patch("anonymizer.gui.QSettings", lambda *args: QSettings(self.ini, QSettings.Format.IniFormat))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_options_are_saved_and_restored(self):
        from PySide6.QtCore import QSettings
        from anonymizer.gui import Window

        window = Window()
        window.timer.stop()
        self.assertEqual(window.options["copies"].text(), "Vytvořit také anonymizovaný ZIP pro kontrolu")
        self.assertEqual(window.options["split"].text(), "Rozdělit po dokumentech")
        self.assertFalse(window.options["copies"].isChecked())
        self.assertFalse(window.options["split"].isChecked())
        window.options["copies"].setChecked(True)
        window.options["split"].setChecked(True)
        settings = QSettings(self.ini, QSettings.Format.IniFormat)
        self.assertTrue(settings.value("copies", False, bool))
        self.assertTrue(settings.value("split", False, bool))
        window.close()
        again = Window()
        again.timer.stop()
        self.assertTrue(again.options["copies"].isChecked())
        self.assertTrue(again.options["split"].isChecked())
        again.options["split"].setChecked(False)
        self.assertFalse(QSettings(self.ini, QSettings.Format.IniFormat).value("split", True, bool))

    def test_options_reach_process_folders(self):
        from anonymizer.gui import Window

        source, output = self.root / "in", self.root / "out"
        source.mkdir()
        output.mkdir()
        (source / "a.txt").write_text("Text.", encoding="utf-8")
        window = Window()
        window.timer.stop()
        window.source, window.output = str(source), str(output)
        window.options["copies"].setChecked(True)
        with mock.patch("anonymizer.gui.BatchWorker") as worker:
            window.scan()
            window.scan()
        self.assertEqual(worker.call_args.kwargs, dict(copies=True, split=False))
        window.worker = None

    def run_worker(self, results, copies=True, split=True, summary=None):
        from anonymizer.gui import BatchWorker

        source, output = self.root / "in", self.root / "out"
        source.mkdir(exist_ok=True)
        output.mkdir(exist_ok=True)
        (source / "a.txt").write_text("Text.", encoding="utf-8")
        worker = BatchWorker(str(source), str(output), None, None, {source / "a.txt"}, copies=copies, split=split)
        lines, handled = [], []
        worker.result.connect(lines.append)
        worker.handled.connect(handled.append)
        calls = {}

        def fake(*args, **kwargs):
            calls.update(kwargs)
            kwargs["progress"](3, 7)
            if summary:
                kwargs["summary"](summary)
            yield from results

        with mock.patch("anonymizer.gui.process_folders", fake):
            os.environ["SPISREDAKT_AUDIT_DIR"] = str(self.root / "audit")
            self.addCleanup(os.environ.pop, "SPISREDAKT_AUDIT_DIR", None)
            worker.run()
        return calls, lines, handled

    def test_worker_passes_options_and_reports_summary(self):
        output = (self.root / "out")
        output.mkdir()
        markdown_path = output.resolve() / "spis_anonymizovano.md"
        summary = dict(markdown=markdown_path, documents=12, tokens_before=21800, tokens_after=12300,
                       skipped=[(5, "archive_encrypted"), (6, "unknown_code")], error="")
        calls, lines, handled = self.run_worker([(None, markdown_path, "")], copies=False, split=True, summary=summary)
        self.assertTrue(calls["markdown"])
        self.assertFalse(calls["copies"])
        self.assertTrue(calls["split"])
        self.assertTrue(callable(calls["progress"]) and callable(calls["summary"]))
        from anonymizer.reasons import ADVICE, GENERAL_ADVICE
        self.assertIn("Markdown: spis_anonymizovano.md", lines)
        self.assertIn("Dokumentů ve spisu: 12", lines)
        self.assertIn("Odhad tokenů: 12 300 (před úpravami 21 800)", lines)
        self.assertIn("Nezpracovaných položek: 2", lines)
        self.assertIn(f"    Položka 5: {ADVICE['archive_encrypted']}", lines)
        self.assertIn(f"    Položka 6: {GENERAL_ADVICE}", lines)
        self.assertEqual(len(handled), 1)

    def test_worker_reports_release_check_failure(self):
        from anonymizer.reasons import ADVICE
        message = "Kontrola výstupu našla možný osobní údaj, soubor nebyl vydán."
        summary = dict(markdown=None, documents=1, tokens_before=0, tokens_after=0, skipped=[], error=message)
        _, lines, _ = self.run_worker([(None, None, message)], summary=summary)
        self.assertIn(f"Markdown nebyl vydán. Chyba: {message}", lines)
        self.assertIn(f"    Rada: {ADVICE['markdown_release_check']}", lines)

    def test_worker_progress_is_throttled(self):
        from anonymizer.gui import BatchWorker

        worker = BatchWorker(".", ".", None, None, set())
        seen = []
        worker.progress.connect(lambda items, pages: seen.append((items, pages)))
        for count in range(1000):
            worker.report_progress(count, count)
        self.assertLess(len(seen), 5)
        self.assertEqual(seen[0], (0, 0))


class AdviceTests(unittest.TestCase):
    def test_every_reason_has_advice(self):
        from anonymizer.reasons import ADVICE, REASONS
        for _, code in REASONS:
            self.assertIn(code, ADVICE)
        for code, text in ADVICE.items():
            self.assertIsInstance(text, str)
            self.assertTrue(text.strip(), code)

    def test_every_format_error_has_specific_advice(self):
        import re
        from anonymizer.reasons import ADVICE, advice
        package = Path(__file__).resolve().parent.parent / "anonymizer"
        raised = set()
        for module in package.glob("*.py"):
            raised.update(re.findall(r'FormatError\("(\w+)", "([^"]+)"\)', module.read_text(encoding="utf-8")))
        self.assertGreater(len(raised), 10)
        for code, message in raised:
            self.assertEqual(advice(message), ADVICE[code], code)

    def test_advice_for_messages(self):
        from anonymizer.reasons import ADVICE, GENERAL_ADVICE, PERMISSION_ADVICE, advice
        self.assertEqual(advice("Soubor načítá externí data."), ADVICE["office_external_link"])
        self.assertEqual(advice("Vstup překračuje limit velikosti."), ADVICE["size_limit"])
        self.assertEqual(advice("Obsah neodpovídá deklarovanému formátu."), ADVICE["invalid_signature"])
        self.assertEqual(advice("Výstup Office nelze přečíst."), ADVICE["corrupt_output"])
        self.assertEqual(advice("PermissionError"), PERMISSION_ADVICE)
        self.assertEqual(advice("Něco neznámého"), GENERAL_ADVICE)

    def test_gui_worker_emits_advice(self):
        from PySide6.QtWidgets import QApplication
        from anonymizer.gui import BatchWorker
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, output, audit = root / "in", root / "out", root / "audit"
            source.mkdir()
            output.mkdir()
            (source / "broken.docx").write_bytes(b"invalid")
            (source / "ok.txt").write_text("Běžný text bez údajů.", encoding="utf-8")
            # The GUI passes resolved paths; on Windows the temporary folder may be a short 8.3 name.
            source, output = source.resolve(), output.resolve()
            selected = {path.resolve() for path in source.iterdir()}
            worker = BatchWorker(source, output, None, None, selected)
            lines = []
            worker.result.connect(lines.append)
            previous = os.environ.get("SPISREDAKT_AUDIT_DIR")
            os.environ["SPISREDAKT_AUDIT_DIR"] = str(audit)
            try:
                worker.run()
            finally:
                if previous is None:
                    del os.environ["SPISREDAKT_AUDIT_DIR"]
                else:
                    os.environ["SPISREDAKT_AUDIT_DIR"] = previous
        index = next(i for i, line in enumerate(lines) if "Chyba:" in line)
        self.assertTrue(lines[index + 1].startswith("    Rada: "), lines)
        for line in lines:
            if line.startswith("    Rada: "):
                self.assertNotIn("broken", line)


class UnusualSpaceTests(unittest.TestCase):
    """No-break spaces, soft hyphens and zero-width characters must not hide a value (synthetic data)."""

    def test_values_with_unusual_spaces_are_replaced(self):
        cases = {
            "Žalobce\xa0Jan\xa0Novák,\xa0nar.\xa01.\xa02.\xa01980": ("Novák", "1980"),
            "bytem\xa0Dlouhá\xa015,\xa0280\xa002\xa0Kolín": ("Dlouhá",),
            "Tel.\u202f777\u2009123\u2009456": ("777",),
            "RČ 850314\xad2010": ("850314", "2010"),
            "na\xa0účet\xa01019283746/\xad0300": ("1019283746", "0300"),
            "\ufeffkb@exam\u200bple.test": ("exam",),
            "No\xadvák uvedl, že Jan Novák": ("No", "vák"),
        }
        for text, hidden in cases.items():
            output = Rules().replace(text)[0]
            for value in hidden:
                self.assertNotIn(value, output, text)

    def test_spans_map_back_to_the_original_text(self):
        text = "Jan\xa0Novák a\xa0kb@example.test"
        findings = Rules().find(text)
        self.assertEqual([text[item.start:item.end] for item in findings], ["Jan\xa0Novák", "kb@example.test"])

    def test_plain_text_is_unchanged(self):
        text = "Žalobce Jan Novák, nar. 1. 2. 1980, tel. 777 123 456."
        plain = Rules().replace(text)
        spaced = Rules().replace(text.replace(" ", "\xa0"))
        self.assertEqual(spaced[0].replace("\xa0", " "), plain[0])
        self.assertEqual(spaced[1], plain[1])

    def test_column_header_with_no_break_space(self):
        from anonymizer.czech import column_kind
        self.assertEqual(column_kind("Datum\xa0narození"), column_kind("Datum narození"))
        self.assertEqual(column_kind("Rodné\xa0číslo"), "RC")


class OcrAtSignTests(unittest.TestCase):
    """OCR readings of "@" on scans (synthetic e-mails only)."""

    def test_at_sign_variants_are_e_mails(self):
        for text in ("e-mail radoslava.ondrackova\n(A example.test, tel.", "jan.novak (a) firma.cz",
                     "e-mail radoslava.ondrackova Q@example.test, tel.", "radoslava.ondrackova a@example.test",
                     "ilona.kucerova\n© example.test", "radoslava.ondrackova A example.test",
                     "e-mail radoslava.ondrackova Wexample.test, tel.", "e-mail radoslava.ondrackova\xa9Aexample.test"):
            output = Rules().replace(text, ocr=True)[0]
            self.assertIn("[EMAIL-1]", output, text)
            self.assertNotIn("ondrackova", output, text)

    def test_address_after_the_e_mail_cue_is_replaced_whatever_the_at_sign_looks_like(self):
        for text in ("tel. 1, e-mail ilona.kucerova(\xa9Aexample.test, rodné", "e-mail radoslava.ondrackova \xa9 example. test, rodné",
                     "E-mail: radoslava.ondrackova (Y example.test\nrodné", "e\xadmail \"adoslava.ondrackova example test rodné",
                     "tel. 1, e\xadmail 1 ona ondrackova(Dora \xa91957 radné číslo, č. ú."):
            output = Rules().replace(text, ocr=True)[0]
            self.assertIn("[EMAIL-1]", output, ascii(text))
            self.assertNotIn("ondrackova", output, ascii(text))
            self.assertNotIn("kucerova", output, ascii(text))
        for text in ("e-mail dne 12.3.2026 byl odeslán", "E-mail: viz. níže", "Pošlete e-mail. Dotaz bude vyřízen.",
                     "e-mail ze dne 3. 3. 2026"):
            self.assertEqual(Rules().replace(text, ocr=True)[0], text)

    def test_ordinary_text_stays(self):
        for text in ("Smlouva (a příloha č. 2) platí.", "Viz bod (a) výše.", "Jan a Firma.cz",
                     "smlouva.pdf a priloha.pdf", "Copyright © 2020 Firma.cz",
                     "Viz www.justice.cz Ministerstvo spravedlnosti.", "jan.novak Wexample.test bez slova pošta"):
            self.assertEqual(Rules().replace(text, ocr=True)[0], text)


class WrappedValueTests(unittest.TestCase):
    """Values broken by a line end or a manual break (synthetic values only)."""

    def test_values_broken_over_lines_are_replaced_whole(self):
        cases = (("Žalobkyně paní Radoslava Ondráč-\nková, bytem", "Ondráč", "ková"),
                 ("Žalobkyně paní Radoslava Ondráč\xad\nková, bytem", "Ondráč", "ková"),
                 ("Rodné číslo 885809/\n1715.", "885809", "1715"),
                 ("Rodné číslo 885809\n/1715.", "885809", "1715"),
                 ("Účet 35-\n1122334455/0800.", "1122334455", "0800"),
                 ("Účet 4400112233/\n0100.", "4400112233", "0100"),
                 ("Rodné číslo 885809\n1715.", "885809", "1715"))
        for text, *parts in cases:
            output = Rules().replace(text, ocr=False)[0]
            for part in parts:
                self.assertNotIn(part, output, ascii(text))

    def test_text_around_a_break_stays(self):
        for text in ("Strana 1\nStrana 2", "Smlouva o dílo -\nviz příloha", "Ve věci sp. zn. 25 Cdo 1234/2019-\n12 viz"):
            self.assertEqual(Rules().replace(text)[0], text, ascii(text))

    def test_findings_keep_their_positions_in_the_original_text(self):
        text = "Dne 1. 1. 2026\xa0přišla paní Radoslava Ondráč-\nková."
        found = Rules().find(text)
        self.assertEqual([text[item.start:item.end] for item in found if item.kind == "OSOBA"],
                         ["Radoslava Ondráč-\nková"])


class NumericRuleTests(unittest.TestCase):
    """Numbers after a cue word (any text) and long digit groups in OCR text (synthetic values only)."""

    @staticmethod
    def replaced(text, ocr=False):
        return Rules().replace(text, ocr)[0]

    def test_cue_word_number_is_replaced_in_any_text(self):
        for text, pieces in (
            ("tel.: 732 123 1609", ("732", "1609", "123")),
            ("účet č. 12345678901/0800", ("12345678901", "0800", "1234567")),
            ("RČ 800101/12345", ("800101", "12345")),
            ("OP č. AB12345B", ("AB12345B", "12345")),
            ("tel. 732 l23 l69", ("732", "l23", "l69")),
            ("mobil: +420 732 123 4567.", ("732", "4567")),
            ("č.ú. 19-2000123456 / 0100", ("2000123456", "0100")),
            ("rodné číslo: 8001O1/1234", ("8001O1", "1234")),
            ("IČO 1234567 89", ("1234567", "89")),
            ("pas: 12345678", ("12345678",)),
        ):
            for ocr in (False, True):
                with self.subTest(text=text, ocr=ocr):
                    result = self.replaced(text, ocr)
                    self.assertRegex(result, r"\[[A-Z_]+-\d+\]")
                    stripped = re.sub(r"\[[A-Z_]+-\d+\]", "", result)
                    for piece in pieces:
                        self.assertNotIn(piece, stripped)

    def test_kinds_follow_the_cue(self):
        for text, kind in (("tel. 732 123 1609", "TELEFON"), ("fax: 732 123 1609", "TELEFON"),
                           ("bankovní spojení: 12345678901/0800", "UCET"), ("RČ 800101/12345", "RC"),
                           ("doklad č. 12345678", "DOKLAD"), ("IČ: 123456789", "ICO"), ("DIČ 1234567890", "DIC")):
            with self.subTest(kind=kind):
                self.assertIn(f"[{kind}-1]", self.replaced(text))

    def test_birth_date_only_when_the_exact_rule_failed(self):
        self.assertEqual(self.replaced("Datum narození: 12. 5. 1980"), "Datum narození: [NAROZENI-1]")
        self.assertIn("[NAROZENI-1]", self.replaced("nar. 12. 5. 19800"))
        self.assertEqual(self.replaced("nar. 5. 80"), "nar. 5. 80")

    def test_long_group_in_ocr_text_only(self):
        text = "Volejte 732 123 1609 kdykoli"
        self.assertEqual(self.replaced(text, True), "Volejte [CISLO-1] kdykoli")
        self.assertEqual(self.replaced(text, False), text)
        for number in ("800101/12345", "12345678901/0800", "732123160912", "+420 732 123 1609"):
            with self.subTest(size=len(number)):
                self.assertEqual(self.replaced(f"Číslo {number} je v textu.", True), "Číslo [CISLO-1] je v textu.")
                self.assertEqual(self.replaced(f"Číslo {number} je v textu.", False), f"Číslo {number} je v textu.")

    def test_long_group_with_ocr_letters(self):
        for text in ("Volejte 604 22l 33O kdykoli", "Volejte 604 221 33O kdykoli", "Volejte 6O4 221 330 kdykoli",
                     "Volejte I23 456 789O kdykoli"):
            with self.subTest(text=text):
                self.assertEqual(self.replaced(text, True), "Volejte [CISLO-1] kdykoli")
                self.assertEqual(self.replaced(text, False), text)
        for text in ("Sb. 123", "zákon č. 89/2012 Sb.", "OSB 12", "IBAN je číslo"):
            with self.subTest(text=text):
                self.assertEqual(self.replaced(text, True), self.replaced(text, False))
        self.assertEqual(self.replaced("Olomouc 123 456 789 je město", True), "Olomouc [CISLO-1] je město")
        self.assertNotIn("CISLO", self.replaced("Olomouc 123 456 78", True))

    def test_useful_numbers_stay(self):
        for text in ("částka 1 234 567 890 Kč", "1 234 567,- Kč", "cena 123 456 789,50 Kč", "celkem 12 345 678 901 EUR",
                     "sp. zn. 12 C 34/2020", "č. j. 5 T 7/2019-11", "č. j. 12345/2020-11", "zákon č. 89/2012 Sb.",
                     "§ 1234", "§ 123456789", "dne 1. 2. 2020", "od 12. 3. 2020 do 15. 3. 2020 - 2020-01-15",
                     "strana 3 z 12", "srov. 25 Cdo 1234/2019", "podle čl. 1234567890", "vyhl. č. 1234567890 Sb."):
            with self.subTest(text=text):
                result = self.replaced(text, True)
                self.assertNotIn("CISLO", result)
                self.assertNotIn("[NAROZENI", result)
        self.assertEqual(self.replaced("na účet soudu 1 234 567 Kč"), "na účet soudu 1 234 567 Kč")
        self.assertEqual(self.replaced("Tel. Novák volal. Číslo 1234567 je jiné."),
                         "Tel. Novák volal. Číslo 1234567 je jiné.")

    def test_exact_rules_keep_precedence_and_numbering(self):
        text = "tel. 721 345 678, RČ 850314/1237, účet 123456789/0800, IČO 12345678, sp. zn. 12 C 34/2020"
        expected = "tel. [TELEFON-1], RČ [RC-1], účet [UCET-1], IČO [ICO-1], sp. zn. [SPIS-1]"
        self.assertEqual(self.replaced(text), expected)
        self.assertEqual(self.replaced(text, True), expected)
        self.assertEqual(Rules().replace("tel. 732 123 1609 a 721 345 678")[0], "tel. [TELEFON-2] a [TELEFON-1]")

    def test_label_is_shared_by_equal_numbers(self):
        rules = Rules()
        first = rules.replace("tel. 732 123 1609", True)[0]
        again = rules.replace("tel. 7321231609", True)[0]
        self.assertEqual((first, again), ("tel. [TELEFON-1]", "tel. [TELEFON-1]"))

    def test_markdown_marks_long_numbers_of_scans_only(self):
        from anonymizer import extract, markdown

        def document(ocr):
            block = extract.Block("p", text="Volejte 732 123 1609 kdykoli", ocr=ocr)
            return extract.Document(1, "PDF", [extract.Page(1, [block])], [], [])

        for ocr, expected in ((True, "Volejte [CI1] kdykoli"), (False, "Volejte 732 123 1609 kdykoli")):
            with self.subTest(ocr=ocr):
                rules, documents = Rules(), [document(ocr)]
                markdown.collect(documents, rules)
                output = markdown.render(documents, rules)
                self.assertIn(expected, output.text)
                self.assertEqual("[CI…] dlouhé číslo ze skenu, možný údaj" in output.text, ocr)


class OfficeConversionTests(unittest.TestCase):
    def setUp(self):
        from anonymizer import legacy

        self.legacy = legacy
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.addCleanup(setattr, legacy, "USE_OFFICE", legacy.USE_OFFICE)

    def make_legacy(self, suffix):
        import subprocess

        if self.legacy.find_office() is None:
            self.skipTest("LibreOffice není nainstalovaný.")
        if suffix == ".doc":
            document = Document()
            document.add_paragraph("Smlouva s Pan Jan Novák, tel. 777 123 456")
            document.save(self.root / "zdroj.docx")
            source = "zdroj.docx"
        else:
            from openpyxl import Workbook

            book = Workbook()
            book.active.append(["Jméno", "Telefon"])
            book.active.append(["Pan Jan Novák", "777123456"])
            book.save(self.root / "zdroj.xlsx")
            source = "zdroj.xlsx"
        subprocess.run([self.legacy.find_office(), "-env:UserInstallation=" + (self.root / "p").as_uri(),
                        "--headless", "--convert-to", suffix[1:], "--outdir", str(self.root / "o"),
                        str(self.root / source)], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return (self.root / "o" / ("zdroj" + suffix)).read_bytes()

    def test_converts_doc_and_xls_through_office(self):
        from anonymizer.formats import redact_file

        self.legacy.USE_OFFICE = True
        for suffix, output in ((".doc", ".docx"), (".xls", ".xlsx")):
            data = self.make_legacy(suffix)
            converted = self.legacy.office_convert(data, suffix)
            self.assertEqual(converted[:2], b"PK")
            source, target = self.root / ("a" + suffix), self.root / ("a_out" + output)
            source.write_bytes(data)
            redact_file(source, target, suffix, Rules(), None)
            with ZipFile(target) as archive:
                body = b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".xml"))
            self.assertNotIn("Novák".encode(), body)
            self.assertNotIn(b"777123456", body)

    def test_temporary_folder_is_removed(self):
        seen = []
        original = tempfile.TemporaryDirectory

        def tracking(*args, **kwargs):
            folder = original(*args, **kwargs)
            seen.append(Path(folder.name))
            return folder

        data = self.make_legacy(".doc")
        with mock.patch("anonymizer.legacy.tempfile.TemporaryDirectory", tracking):
            self.assertIsNotNone(self.legacy.office_convert(data, ".doc"))
        self.assertTrue(seen)
        self.assertFalse(any(path.exists() for path in seen))

    def test_falls_back_without_office_on_error_and_timeout(self):
        import subprocess

        self.legacy.USE_OFFICE = True
        cases = (("missing", None, None),
                 ("timeout", "/usr/bin/soffice", subprocess.TimeoutExpired("x", 1)),
                 ("error", "/usr/bin/soffice", subprocess.CalledProcessError(1, "x")))
        for name, program, failure in cases:
            with self.subTest(name), \
                    mock.patch("anonymizer.legacy.find_office", return_value=program), \
                    mock.patch("anonymizer.legacy.subprocess.run", side_effect=failure), \
                    mock.patch("anonymizer.legacy.doc_to_docx", return_value=b"builtin"):
                self.assertEqual(self.legacy.to_modern(b"x", ".doc"), b"builtin")

    def test_office_is_not_used_when_disabled_and_path_is_not_searched(self):
        self.legacy.USE_OFFICE = False
        with mock.patch("anonymizer.legacy.office_convert") as convert, \
                mock.patch("anonymizer.legacy.doc_to_docx", return_value=b"builtin"):
            self.assertEqual(self.legacy.to_modern(b"x", ".doc"), b"builtin")
        convert.assert_not_called()
        with mock.patch("os.path.isfile", return_value=False), mock.patch("shutil.which") as which:
            self.assertIsNone(self.legacy.find_office())
        which.assert_not_called()


class PreviewTests(unittest.TestCase):
    """The in-memory preview: catalog, locators, rendering, fixed messages, nothing written."""

    TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.target, self.audit = (self.root / name for name in ("in", "out", "audit"))
        self.source.mkdir()
        self.target.mkdir()

    @staticmethod
    def pdf_bytes(*pages):
        with pdf.open() as document:
            for text in pages:
                document.new_page().insert_text((72, 100), text, fontsize=14)
            return document.tobytes()

    def build_zip(self):
        word = io.BytesIO()
        document = Document()
        document.add_paragraph("Smlouvu uzavřela Karolína Bílá, telefon 777 123 456.")
        document.save(word)
        inner = io.BytesIO()
        self.inner_pdf = self.pdf_bytes("Vnořený dokument Jan Novotný, telefon 602 111 222, bytem Praha.")
        with ZipFile(inner, "w") as archive:
            archive.writestr("vnorene_novotny.pdf", self.inner_pdf)
        message = EmailMessage()
        message["From"] = "jan.novotny@example.test"
        message["To"] = "archiv@example.test"
        message["Subject"] = "Doklady"
        message.set_content("Posílám přílohu, telefon 602 333 444.")
        message.add_attachment(self.pdf_bytes("Příloha e-mailu Karel Dvořák, telefon 602 555 666."),
                               maintype="application", subtype="pdf", filename="priloha_dvorak.pdf")
        self.outer_pdf = self.pdf_bytes("Žalobce Jan Novotný, telefon 777 123 456, bytem Praha.",
                                        "Druhá strana, Jan Novotný znovu.")
        with ZipFile(self.source / "spis_novotny.zip", "w") as archive:
            archive.writestr("slozka_novotny/", b"")
            archive.writestr("sifra_novotny.txt", b"zasifrovano")
            archive.writestr("neznamy_novotny.bin", b"unknown")
            archive.writestr("smlouva_novotny.pdf", self.outer_pdf)
            archive.writestr("vnorene_novotny.zip", inner.getvalue())
            archive.writestr("text_novotny.docx", word.getvalue())
            archive.writestr("posta_novotny.eml", message.as_bytes())

    @staticmethod
    def marked_infolist():
        original = ZipFile.infolist

        def marked(archive):
            result = original(archive)
            for info in result:
                if info.filename == "sifra_novotny.txt":
                    info.flag_bits |= 1
            return result

        return mock.patch.object(ZipFile, "infolist", marked)

    def run_batch(self, **kwargs):
        from anonymizer.preview import Catalog

        catalog = Catalog()
        options = dict(markdown=True, copies=True)
        options.update(kwargs)
        with self.marked_infolist():
            results = list(process_folders(self.source, self.target, audit_root=self.audit,
                                           catalog=catalog.update, **options))
        return catalog, results

    def render_direct(self, data, number=1, zoom=1.0, suffix=".pdf"):
        from anonymizer import preview

        return preview.render(data, suffix, number, zoom)[0]

    def test_catalog_locators_and_rendering(self):
        from anonymizer import preview

        self.build_zip()
        catalog, results = self.run_batch()
        facts = catalog.documents
        self.assertEqual(sorted(facts), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual((facts[1]["code"], facts[2]["code"]), ("archive_encrypted", "archive_member_unsupported"))
        # Directories are not counted; encrypted and rejected members are.
        self.assertEqual(facts[3]["locator"], (1, "3"))
        self.assertEqual(facts[4]["locator"], (1, "4/1"))
        self.assertEqual(facts[5]["locator"], (1, "5"))
        self.assertEqual(facts[6]["locator"], (1, "6"))
        self.assertIsNone(facts[7]["locator"])
        self.assertEqual(facts[7]["parent"], 6)
        self.assertEqual((facts[3]["format"], facts[3]["pages"]), ("PDF", 2))
        self.assertEqual(catalog.markdown, results[0][1])
        self.assertEqual(list(catalog.copies), [1])
        model = preview.Model(catalog)
        self.assertEqual(model.label(3), "D3 (PDF, 2 s.)")
        self.assertEqual(model.label(1), "D1 – nezpracováno")
        self.assertEqual(model.label(7), "D7 (PDF, 1 s., příloha D6)")
        # The page of a nested member equals rendering it directly.
        self.assertEqual(model.original(3, 2, 1.0)[0], self.render_direct(self.outer_pdf, 2))
        image, count = model.original(4, 1, 1.0)
        self.assertEqual(image, self.render_direct(self.inner_pdf))
        self.assertEqual(count, 1)
        self.assertGreater(model.original(3, 1, 2.0)[0].width(), model.original(3, 1, 1.0)[0].width())
        # Fixed refusals.
        cases = ((model.original, (1, 1, 1.0), "preview_unprocessed"),
                 (model.original, (5, 1, 1.0), "preview_not_page"),
                 (model.original, (6, 1, 1.0), "preview_not_page"),
                 (model.original, (7, 1, 1.0), "preview_no_original"),
                 (model.original, (3, 3, 1.0), "preview_no_page"),
                 (model.copy, (7, 1, 1.0), "preview_no_copy"),
                 (model.copy, (1, 1, 1.0), "preview_no_copy"))
        for call, arguments, code in cases:
            with self.subTest(code=code, arguments=arguments), self.assertRaises(preview.PreviewError) as raised:
                call(*arguments)
            self.assertEqual(raised.exception.code, code)
        # The output section is the released one: labels instead of values, one section only.
        section = model.text(3)
        self.assertTrue(section.startswith("## D3 (PDF, 2 s.)"))
        self.assertIn("[s. 2]", section)
        self.assertRegex(section, r"\[O\d+\]")
        self.assertNotIn("Novotný", section)
        self.assertNotIn("## D4", section)
        self.assertIn(ADVICE["archive_encrypted"], model.text(1))
        # The control copy shows the page of the released copy, also from a nested archive.
        self.assertTrue(model.has_copy(3) and model.has_copy(4))
        self.assertFalse(any(model.has_copy(index) for index in (1, 2, 5, 6, 7)))
        for index in (3, 4):
            image, _ = model.copy(index, 1, 1.0)
            self.assertFalse(image.isNull())
        with ZipFile(catalog.copies[1]) as archive:
            released = archive.read("polozka-0003.pdf")
        self.assertEqual(model.copy(3, 1, 1.0)[0], self.render_direct(released))

    def test_dialog_shows_no_names_and_fixed_texts(self):
        from anonymizer import preview

        self.build_zip()
        catalog, _ = self.run_batch()
        dialog = preview.PreviewDialog(catalog)
        self.addCleanup(dialog.release)
        self.assertEqual(dialog.windowTitle(), "SpisRedakt – náhled")
        self.assertEqual(dialog.original_title.text(), "Originál – obsahuje osobní údaje")
        seen = []
        for row in range(dialog.items.count()):
            dialog.items.setCurrentRow(row)
            seen += [dialog.items.item(row).text(), dialog.original_image.text(), dialog.copy_image.text(),
                     dialog.text.toPlainText(), dialog.output_title.text(), dialog.count.text()]
            if row + 1 in (3, 4):
                self.assertFalse(dialog.original_image.pixmap().isNull())
                self.assertTrue(dialog.copy_toggle.isEnabled())
                dialog.copy_toggle.setChecked(True)
                self.assertFalse(dialog.copy_image.pixmap().isNull())
                self.assertEqual(dialog.output_title.text(), "Kontrolní kopie")
                dialog.copy_toggle.setChecked(False)
            else:
                self.assertFalse(dialog.copy_toggle.isEnabled())
        everything = "\n".join(seen)
        self.assertRegex(everything, r"\[O\d+\]")
        for forbidden in ("novotny", "novotný", "dvorak", "dvořák", "smlouva", "sifra", "neznamy", "posta",
                          str(self.root).lower()):
            self.assertNotIn(forbidden, everything.lower(), forbidden)
        # Paging, zoom and the shift of the output to [s. N].
        dialog.items.setCurrentRow(2)
        self.assertEqual((dialog.number.maximum(), dialog.count.text()), (2, "/ 2"))
        self.assertFalse(dialog.previous.isEnabled())
        dialog.following.click()
        self.assertEqual(dialog.number.value(), 2)
        self.assertTrue(dialog.text.textCursor().selectedText().startswith("[s. 2]"))
        self.assertFalse(dialog.following.isEnabled())
        before = dialog.original_image.pixmap().width()
        dialog.larger.click()
        self.assertGreater(dialog.original_image.pixmap().width(), before)
        # A label is highlighted in the output.
        dialog.text.setPlainText("Text [O1] a [RC2].")
        dialog.highlighter.rehighlight()
        formats = dialog.text.document().firstBlock().layout().formats()
        self.assertEqual(len(formats), 2)

    def test_nothing_is_written_and_results_are_unchanged(self):
        import builtins
        import tempfile as tempfile_module
        from PySide6.QtCore import QSettings
        from anonymizer.gui import Window

        self.build_zip()
        catalog, _ = self.run_batch()
        ini = str(self.root / "settings.ini")

        def listing():
            return sorted((str(path.relative_to(self.root)), path.stat().st_size)
                          for path in self.root.rglob("*") if path.is_file())

        def state():
            return listing(), next(self.audit.glob("*/events.jsonl")).read_text()

        def forbidden(*args, **kwargs):
            raise AssertionError("preview wrote to the disk")

        real_open, real_io_open = builtins.open, io.open

        def guard(real):
            def guarded(file, mode="r", *args, **kwargs):
                if set(mode) & set("wax+"):
                    raise AssertionError("preview opened a file for writing")
                return real(file, mode, *args, **kwargs)
            return guarded

        with mock.patch("anonymizer.gui.QSettings", lambda *args: QSettings(ini, QSettings.Format.IniFormat)):
            window = Window()
        window.timer.stop()
        window.catalog = catalog
        window.preview_button.setEnabled(True)
        before = state()
        with mock.patch.object(builtins, "open", guard(real_open)), mock.patch.object(io, "open", guard(real_io_open)), \
                mock.patch.object(Path, "write_text", forbidden), mock.patch.object(Path, "write_bytes", forbidden), \
                mock.patch.object(Path, "mkdir", forbidden), mock.patch.object(Path, "touch", forbidden), \
                mock.patch.object(tempfile_module, "mkstemp", forbidden), \
                mock.patch.object(tempfile_module, "mkdtemp", forbidden), \
                mock.patch.object(tempfile_module, "NamedTemporaryFile", forbidden), \
                mock.patch.object(tempfile_module, "TemporaryFile", forbidden), \
                mock.patch.object(tempfile_module, "TemporaryDirectory", forbidden), \
                mock.patch.object(tempfile_module, "SpooledTemporaryFile", forbidden), \
                mock.patch.object(QSettings, "setValue", forbidden), mock.patch.object(QSettings, "sync", forbidden):
            window.open_preview()
            dialog = window.preview
            self.assertIsNotNone(dialog)
            for row in range(dialog.items.count()):
                dialog.items.setCurrentRow(row)
                if dialog.copy_toggle.isEnabled():
                    dialog.copy_toggle.setChecked(True)
                dialog.following.click()
                dialog.larger.click()
            dialog.close()
        self.assertIsNone(window.preview)
        self.assertEqual(before, state())
        self.assertEqual(dialog.model.cache, {})

    def test_outputs_are_identical_with_and_without_catalog(self):
        import json

        self.build_zip()
        runs = []
        for name, wanted in (("with", True), ("without", False)):
            target = self.root / name
            target.mkdir()
            audit = self.root / f"audit-{name}"
            catalog = []
            with self.marked_infolist():
                results = list(process_folders(self.source, target, audit_root=audit, markdown=True, copies=True,
                                               catalog=catalog.append if wanted else None))
            self.assertEqual(bool(catalog), wanted)
            markdown = results[0][1].read_text(encoding="utf-8")
            with ZipFile(results[1][1]) as archive:
                names = archive.namelist()
                note = archive.read("NEZPRACOVANO.txt")
            log = [{key: value for key, value in json.loads(line).items() if key not in ("run_id", "utc")}
                   for line in next(audit.glob("*/events.jsonl")).read_text().splitlines()]
            runs.append((markdown, names, note, log))
        self.assertEqual(runs[0], runs[1])

    def test_changed_missing_and_unissued_inputs_give_fixed_messages(self):
        from anonymizer import preview

        self.build_zip()
        catalog, _ = self.run_batch()
        model = preview.Model(catalog)
        self.assertFalse(model.original(3, 1, 1.0)[0].isNull())
        archive_path = self.source / "spis_novotny.zip"
        archive_path.write_bytes(archive_path.read_bytes() + b"x")
        with self.assertRaises(preview.PreviewError) as raised:
            model.original(3, 1, 1.0)
        self.assertEqual(raised.exception.code, "preview_input_changed")
        # The output is still there.
        self.assertIn("[s. 2]", model.text(3))
        archive_path.unlink()
        with self.assertRaises(preview.PreviewError) as raised:
            model.original(4, 1, 1.0)
        self.assertEqual(raised.exception.code, "preview_input_changed")
        # Markdown not released: the original is still shown, the output is a fixed message.
        catalog.markdown = None
        with self.assertRaises(preview.PreviewError) as raised:
            model.text(3)
        self.assertEqual(raised.exception.code, "preview_markdown_missing")
        dialog = preview.PreviewDialog(catalog)
        self.addCleanup(dialog.release)
        dialog.items.setCurrentRow(2)
        self.assertEqual(dialog.text.toPlainText(), preview.PREVIEW_MESSAGES["preview_markdown_missing"])
        self.assertEqual(dialog.original_image.text(), preview.PREVIEW_MESSAGES["preview_input_changed"])

    def test_split_markdown_and_plain_inputs(self):
        from anonymizer import preview

        (self.source / "sken.pdf").write_bytes(self.pdf_bytes("Žalobce Jan Novotný, telefon 777 123 456."))
        word = Document()
        word.add_paragraph("Text bez údajů.")
        word.save(self.source / "rozsudek.docx")
        catalog, results = self.run_batch(split=True)
        self.assertTrue(catalog.split)
        self.assertTrue(catalog.markdown.is_dir())
        model = preview.Model(catalog)
        by_format = {facts["format"]: index for index, facts in catalog.documents.items()}
        self.assertTrue(model.text(by_format["PDF"]).startswith(f"## D{by_format['PDF']} (PDF"))
        self.assertEqual(model.original(by_format["PDF"], 1, 1.0)[0],
                         self.render_direct((self.source / "sken.pdf").read_bytes()))
        self.assertFalse(model.copy(by_format["PDF"], 1, 1.0)[0].isNull())
        with self.assertRaises(preview.PreviewError) as raised:
            model.original(by_format["DOCX"], 1, 1.0)
        self.assertEqual(raised.exception.code, "preview_not_page")

    def test_failures_show_only_fixed_texts(self):
        from anonymizer import preview

        self.build_zip()
        catalog, _ = self.run_batch()
        dialog = preview.PreviewDialog(catalog)
        self.addCleanup(dialog.release)
        with mock.patch.object(preview, "render", side_effect=RuntimeError("Jan Novotný 777 123 456")):
            dialog.items.setCurrentRow(2)
            dialog.larger.click()
        self.assertEqual(dialog.original_image.text(), preview.PREVIEW_MESSAGES["preview_failed"])
        with mock.patch.object(preview, "MAX_PIXELS", 1000):
            dialog.larger.click()
        self.assertEqual(dialog.original_image.text(), preview.PREVIEW_MESSAGES["preview_page_too_large"])
        with pdf.open() as document:
            document.new_page()
            locked = document.tobytes(encryption=pdf.PDF_ENCRYPT_AES_256, user_pw="a", owner_pw="b")
        for data, code in ((locked, "preview_encrypted"), (b"not a pdf", "preview_not_page"),
                           (b"%PDF-1.4 broken", "preview_encrypted")):
            with self.subTest(code=code), self.assertRaises(preview.PreviewError) as raised:
                preview.render(data, ".pdf", 1, 1.0)
            self.assertEqual(raised.exception.code, code)

    def test_window_buttons_and_scan_while_preview_is_open(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QSettings
        from anonymizer import preview
        from anonymizer.gui import Window

        self.build_zip()
        catalog, _ = self.run_batch()
        ini = str(self.root / "settings.ini")
        with mock.patch("anonymizer.gui.QSettings", lambda *args: QSettings(ini, QSettings.Format.IniFormat)):
            window = Window()
        window.timer.stop()
        self.assertFalse(window.preview_button.isEnabled())
        # Enabled only after a batch that finished in this run and had items.
        for complete, shown, enabled in ((False, catalog, False), (True, preview.Catalog(), False),
                                         (True, catalog, True)):
            window.worker = SimpleNamespace(complete=complete, catalog=shown, deleteLater=lambda: None)
            window.finished()
            self.assertEqual(window.preview_button.isEnabled(), enabled)
        # A running batch blocks it.
        window.worker = object()
        window.open_preview()
        self.assertIsNone(window.preview)
        window.worker = None
        window.source, window.output = str(self.root / "pin"), str(self.root / "pout")
        (self.root / "pin").mkdir()
        (self.root / "pout").mkdir()
        (self.root / "pin" / "a.txt").write_text("Text.", encoding="utf-8")
        window.open_preview()
        self.assertIsNotNone(window.preview)
        with mock.patch("anonymizer.gui.BatchWorker") as worker:
            window.scan()
            window.scan()
            worker.assert_not_called()
            window.preview.close()
            self.assertIsNone(window.preview)
            window.scan()
            window.scan()
            worker.assert_called_once()
        # Starting a batch and changing a folder forget the old one.
        self.assertIsNone(window.catalog)
        self.assertFalse(window.preview_button.isEnabled())
        window.worker = None
        window.catalog = catalog
        with mock.patch("anonymizer.gui.QFileDialog.getExistingDirectory", return_value=str(self.root / "pin")):
            window.choose("source", "Vstupní složka")
        self.assertIsNone(window.catalog)

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_scans_and_images_in_nested_archives_match_direct_rendering(self):
        from anonymizer import preview

        with pdf.open() as document:
            page = document.new_page(width=400, height=120)
            page.insert_text((20, 60), "Jan Novák bydlí v Praze", fontsize=22)
            pixmap = page.get_pixmap(dpi=200, alpha=False, colorspace=pdf.csGRAY)
        blank = pdf.Pixmap(pixmap.colorspace, pixmap.irect, False)
        blank.clear_with(255)
        tiff = self.root / "sken.tif"
        ProcessingTests.write_tiff(tiff, [pixmap, blank])
        png = pdf.Pixmap(pdf.csRGB, pixmap).tobytes("png")
        inner = io.BytesIO()
        with ZipFile(inner, "w") as archive:
            archive.writestr("a/", b"")
            archive.writestr("sken.tif", tiff.read_bytes())
        with ZipFile(self.source / "spis.zip", "w") as archive:
            archive.writestr("obrazek.png", png)
            archive.writestr("vnorene.zip", inner.getvalue())
        catalog = preview.Catalog()
        results = list(process_folders(self.source, self.target, tessdata=self.TESSDATA, markdown=True,
                                       copies=True, catalog=catalog.update))
        self.assertEqual([error for _, _, error in results], ["", ""])
        model = preview.Model(catalog)
        by_format = {facts["format"]: index for index, facts in catalog.documents.items()}
        self.assertEqual(catalog.documents[by_format["TIFF"]]["locator"], (1, "2/1"))
        self.assertEqual(model.original(by_format["PNG"], 1, 1.0)[0], self.render_direct(png, suffix=".png"))
        image, count = model.original(by_format["TIFF"], 2, 1.0)
        self.assertEqual(count, 2)
        self.assertEqual(image, self.render_direct(tiff.read_bytes(), 2, suffix=".tif"))
        self.assertFalse(model.copy(by_format["TIFF"], 1, 1.0)[0].isNull())
        self.assertIn("[s. 1]", model.text(by_format["TIFF"]))

    def test_messages_are_fixed_czech_texts_used_by_the_module(self):
        from anonymizer.reasons import PREVIEW_MESSAGES

        source = (Path(rules_module.__file__).parent / "preview.py").read_text(encoding="utf-8")
        used = set(re.findall(r'(?:PreviewError|message)\("(preview_[a-z_]+)"', source))
        self.assertTrue(used)
        self.assertLessEqual(used, set(PREVIEW_MESSAGES))
        for code, text in PREVIEW_MESSAGES.items():
            self.assertTrue(text.endswith("."), code)
            self.assertNotIn("{", text)
        for forbidden in ("tempfile", "QSettings", "write_text", "write_bytes", "logging", "audit"):
            self.assertNotIn(forbidden, source)


class ManualAreaTests(unittest.TestCase):
    """Manual redaction areas (14b): only added blackout, in the Markdown and in the control copy alike."""

    TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
    HIDDEN, SHOWN = "ZEBRAKADABRA", "PLANETARIUM"

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.target, self.audit = (self.root / name for name in ("in", "out", "audit"))
        for folder in (self.source, self.target):
            folder.mkdir()

    # -- builders ------------------------------------------------------------------------------------------

    @classmethod
    def text_pdf(cls, pages=1, rotation=0, crop=None, tag=""):
        """Pages whose displayed text is upright, also with /Rotate (the text is turned the other way)."""
        lines = (f"{cls.HIDDEN} tajne", f"{cls.SHOWN} verejne {tag}")
        points = {0: [(40, 100), (40, 200)], 90: [(110, 260), (210, 260)], 270: [(110, 40), (210, 40)]}[rotation]
        with pdf.open() as document:
            for _ in range(pages):
                page = document.new_page(width=420, height=300)
                for point, line in zip(points, lines):
                    page.insert_text(point, line, fontsize=18, rotate=rotation)
                page.set_rotation(rotation)
                if crop:
                    page.set_cropbox(pdf.Rect(*crop))
            return document.tobytes()

    @staticmethod
    def fold(text):
        import unicodedata

        return "".join(c for c in unicodedata.normalize("NFD", text.upper()) if not unicodedata.combining(c))

    @staticmethod
    def area_of(data, word, number=1):
        """Normalized rectangle of ``word`` on the displayed page ``number`` of a PDF."""
        with pdf.open(stream=data, filetype="pdf") as document:
            page = document[number - 1]
            found = page.search_for(word)[0] * page.rotation_matrix
            full = page.rect
            return ((found.x0 - full.x0) / full.width, (found.y0 - full.y0) / full.height,
                    (found.x1 - full.x0) / full.width, (found.y1 - full.y0) / full.height)

    def process(self, areas=None, markdown=True, copies=True):
        from anonymizer.preview import Catalog

        catalog = Catalog()
        results = list(process_folders(self.source, self.target, self.TESSDATA, audit_root=self.audit,
                                       markdown=markdown, copies=copies, catalog=catalog.update,
                                       manual_areas=areas))
        return catalog, results

    @staticmethod
    def add(areas, catalog, index, rectangle, page=1):
        locator = catalog.documents[index]["locator"]
        areas.add(locator, catalog.inputs[locator[0]][1], catalog.inputs[locator[0]][0], page, rectangle)
        return areas

    def draw(self, catalog, index, rectangle, page=1):
        from anonymizer.manual import Areas

        return self.add(Areas(), catalog, index, rectangle, page)

    def copy_text(self, path, number=1):
        with pdf.open(path) as document:
            pixmap = document[number - 1].get_pixmap(dpi=200, alpha=False)
        return recognize_image(pixmap, self.TESSDATA)[0]

    @staticmethod
    def black_inside(pixmap, rectangle):
        """True when the area of the page image, shrunk by 1 px, is entirely black."""
        x0, y0, x1, y1 = rectangle
        clip = pdf.IRect(round(x0 * pixmap.width) + 1, round(y0 * pixmap.height) + 1,
                         round(x1 * pixmap.width) - 1, round(y1 * pixmap.height) - 1)
        return set(pixmap.color_count(clip=clip, colors=True)) == {b"\x00\x00\x00"}

    def copy_black(self, path, rectangle, number=1):
        with pdf.open(path) as document:
            return self.black_inside(document[number - 1].get_pixmap(dpi=100, alpha=False), rectangle)

    @staticmethod
    def content(path):
        """Bytes of an image, the rendered pages of a PDF (its identifier differs between runs)."""
        if path.suffix != ".pdf":
            return path.read_bytes()
        with pdf.open(path) as document:
            return [page.get_pixmap(dpi=100, alpha=False).samples for page in document]

    @staticmethod
    def released(results):
        return {source.name: target for source, target, error in results if source is not None and target}

    @staticmethod
    def markdown(catalog):
        return Path(catalog.markdown).read_text(encoding="utf-8")

    @staticmethod
    def section(catalog, index):
        text = Path(catalog.markdown).read_text(encoding="utf-8")
        return text.split(f"## D{index} (")[1].split("\n## ")[0]

    # -- pages: PDF ----------------------------------------------------------------------------------------

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_area_over_a_word_the_rules_do_not_find(self):
        for label, kwargs in (("plain", {}), ("rotated 90", dict(rotation=90)), ("rotated 270", dict(rotation=270)),
                              ("shifted cropbox", dict(crop=(30, 40, 400, 280)))):
            with self.subTest(label):
                for path in self.source.glob("*"):
                    path.unlink()
                data = self.text_pdf(**kwargs)
                (self.source / "a.pdf").write_bytes(data)
                baseline, _ = self.process()
                self.assertIn(self.HIDDEN, self.markdown(baseline))
                area = self.area_of(data, self.HIDDEN)
                with pdf.open(stream=data, filetype="pdf") as document:
                    shown = document[0].get_pixmap(dpi=100, alpha=False)
                self.assertFalse(self.black_inside(shown, area))
                self.assertNotEqual(set(shown.color_count(clip=pdf.IRect(
                    round(area[0] * shown.width), round(area[1] * shown.height),
                    round(area[2] * shown.width), round(area[3] * shown.height)), colors=True)), {b"\xff\xff\xff"})
                catalog, results = self.process(self.draw(baseline, 1, area))
                self.assertEqual([error for _, _, error in results], ["", ""])
                text = self.fold(self.markdown(catalog))
                self.assertNotIn("ZEBRA", text)
                self.assertIn("PLANETARIUM", text)
                copy = self.released(results)["a.pdf"]
                # The copy passed validate_pdf with the area expected; its pixels are black, the word is gone.
                self.assertTrue(self.copy_black(copy, area))
                text = self.fold(self.copy_text(copy))
                self.assertNotIn("ZEBRA", text)
                self.assertIn("PLANETARIUM", text)


    # -- pages: scans, images, archives ---------------------------------------------------------------------

    @staticmethod
    def max_inside(pixmap, rectangle, shrink=3):
        x0, y0, x1, y1 = rectangle
        top, bottom = round(y0 * pixmap.height) + shrink, round(y1 * pixmap.height) - shrink
        left, right = round(x0 * pixmap.width) + shrink, round(x1 * pixmap.width) - shrink
        view = pixmap.samples_mv
        return max(max(view[row * pixmap.stride + left * pixmap.n:row * pixmap.stride + right * pixmap.n])
                   for row in range(top, bottom))

    def images(self):
        """Synthetic scan (2 pages), PNG and JPEG of the text page; returns (pdf data, rgb pixmap, tiff, png, jpeg)."""
        data = self.text_pdf(pages=2)
        with pdf.open(stream=data, filetype="pdf") as document:
            gray = [page.get_pixmap(dpi=200, alpha=False, colorspace=pdf.csGRAY) for page in document]
        with pdf.open(stream=self.text_pdf(tag="png"), filetype="pdf") as document:
            rgb = pdf.Pixmap(pdf.csRGB, document[0].get_pixmap(dpi=200, alpha=False))
        with pdf.open(stream=self.text_pdf(tag="jpg"), filetype="pdf") as document:
            other = pdf.Pixmap(pdf.csRGB, document[0].get_pixmap(dpi=200, alpha=False))
        ProcessingTests.write_tiff(self.root / "sken.tif", gray)
        return (data, rgb, (self.root / "sken.tif").read_bytes(), rgb.tobytes("png"),
                other.tobytes("jpeg", jpg_quality=95))

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_area_on_a_later_tiff_page_png_and_jpeg(self):
        data, rgb, tiff, png, jpeg = self.images()
        for name, content in (("sken.tif", tiff), ("obraz.png", png), ("foto.jpg", jpeg)):
            (self.source / name).write_bytes(content)
        baseline, _ = self.process()
        by_format = {facts["format"]: index for index, facts in baseline.documents.items()}
        self.assertEqual(sorted(by_format), ["JPEG", "PNG", "TIFF"])
        self.assertEqual(self.fold(self.section(baseline, by_format["TIFF"])).count("ZEBRA"), 2)
        area, later = self.area_of(data, self.HIDDEN, 1), self.area_of(data, self.HIDDEN, 2)
        areas = self.draw(baseline, by_format["TIFF"], later, 2)
        self.add(areas, baseline, by_format["PNG"], area)
        self.add(areas, baseline, by_format["JPEG"], area)
        catalog, results = self.process(areas)
        self.assertEqual([error for _, _, error in results], ["", "", "", ""])
        # The scan loses the word on page 2 only; the images lose it altogether.
        self.assertEqual(self.fold(self.section(catalog, by_format["TIFF"])).count("ZEBRA"), 1)
        for name in ("PNG", "JPEG"):
            section = self.fold(self.section(catalog, by_format[name]))
            self.assertNotIn("ZEBRA", section)
            self.assertIn("PLANETARIUM", section)
        copies = self.released(results)
        scan = copies["sken.tif"]
        self.assertTrue(self.copy_black(scan, later, 2))
        self.assertIn("ZEBRA", self.fold(self.copy_text(scan, 1)))
        self.assertNotIn("ZEBRA", self.fold(self.copy_text(scan, 2)))
        for name, tolerance in (("obraz.png", 0), ("foto.jpg", 40)):
            pixmap = pdf.Pixmap(copies[name].read_bytes())
            self.assertLessEqual(self.max_inside(pixmap, area), tolerance, name)
            found = self.fold(recognize_image(pixmap, self.TESSDATA)[0])
            self.assertNotIn("ZEBRA", found, name)
            self.assertIn("PLANETARIUM", found, name)

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_areas_in_a_nested_archive(self):
        data, rgb, tiff, png, jpeg = self.images()
        inner = io.BytesIO()
        with ZipFile(inner, "w") as archive:
            archive.writestr("a/", b"")
            archive.writestr("sken.tif", tiff)
        with ZipFile(self.source / "spis.zip", "w") as archive:
            archive.writestr("obraz.png", png)
            archive.writestr("vnorene.zip", inner.getvalue())
        baseline, _ = self.process()
        by_format = {facts["format"]: index for index, facts in baseline.documents.items()}
        self.assertEqual(baseline.documents[by_format["TIFF"]]["locator"], (1, "2/1"))
        area, later = self.area_of(data, self.HIDDEN, 1), self.area_of(data, self.HIDDEN, 2)
        areas = self.draw(baseline, by_format["PNG"], area)
        self.add(areas, baseline, by_format["TIFF"], later, 2)
        catalog, results = self.process(areas)
        self.assertEqual([error for _, _, error in results], ["", ""])
        self.assertNotIn("ZEBRA", self.fold(self.section(catalog, by_format["PNG"])))
        self.assertEqual(self.fold(self.section(catalog, by_format["TIFF"])).count("ZEBRA"), 1)
        with ZipFile(catalog.copies[1]) as archive:
            self.assertNotIn("NEZPRACOVANO.txt", archive.namelist())
            image = pdf.Pixmap(archive.read("polozka-0001.png"))
            nested = ZipFile(io.BytesIO(archive.read("polozka-0002.zip")))
            scan = nested.read("polozka-0001.pdf")
        self.assertEqual(self.max_inside(image, area), 0)
        self.assertNotIn("ZEBRA", self.fold(recognize_image(image, self.TESSDATA)[0]))
        with pdf.open(stream=scan, filetype="pdf") as document:
            self.assertTrue(self.black_inside(document[1].get_pixmap(dpi=100, alpha=False), later))
            self.assertFalse(self.black_inside(document[0].get_pixmap(dpi=100, alpha=False), area))

    def test_black_check_of_an_encoded_image_has_a_tolerance_for_jpeg(self):
        from anonymizer.documents import black_manual, verify_black

        pixmap = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 300), False)
        pixmap.clear_with(255)
        filled = black_manual(pixmap, [(0.25, 0.25, 0.75, 0.75)])
        for extension, tolerance in (("png", 0), ("jpeg", 40)):
            data = pixmap.tobytes(extension, jpg_quality=100) if extension == "jpeg" else pixmap.tobytes(extension)
            verify_black(data, extension, filled, tolerance)
        light = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 400, 300), False)
        light.clear_with(255)
        light.set_rect(light.irect, (90, 90, 90))
        for extension in ("png", "jpeg"):
            with self.assertRaises(ValueError):
                verify_black(light.tobytes(extension), extension, filled, 40)

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_image_turned_by_exif_is_refused(self):
        import struct

        data, rgb, tiff, png, jpeg = self.images()
        exif = b"Exif\0\0MM\0*\0\0\0\x08\0\x01\x01\x12\0\x03\0\0\0\x01\0\x06\0\0\0\0\0\0"
        turned = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
        (self.source / "foto.jpg").write_bytes(turned)
        (self.source / "obraz.png").write_bytes(png)
        baseline, _ = self.process()
        by_format = {facts["format"]: index for index, facts in baseline.documents.items()}
        area = self.area_of(data, self.HIDDEN)
        areas = self.draw(baseline, by_format["JPEG"], area)
        self.add(areas, baseline, by_format["PNG"], area)
        catalog, results = self.process(areas)
        self.assertIn(f"položka {by_format['JPEG']}: manual_area_mismatch", self.markdown(catalog))
        outcome = {source.name: (target, error) for source, target, error in results if source is not None}
        self.assertIsNone(outcome["foto.jpg"][0])
        self.assertEqual(outcome["foto.jpg"][1], MANUAL_MISMATCH)
        self.assertIsNotNone(outcome["obraz.png"][0])
        self.assertNotIn("ZEBRA", self.fold(self.section(catalog, by_format["PNG"])))

    # -- the collection of persons, mismatches, nothing without areas -------------------------------------------

    @staticmethod
    def case_pdf(text):
        with pdf.open() as document:
            document.new_page(width=420, height=300).insert_text((30, 100), text, fontsize=16)
            return document.tobytes()

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_name_under_an_area_still_teaches_the_surname_elsewhere(self):
        first = self.case_pdf("Zalobce Jan Prochazka, bytem Praha.")
        (self.source / "a.pdf").write_bytes(first)
        (self.source / "b.pdf").write_bytes(self.case_pdf("Svedek Prochazka uvedl, ze byl pritomen."))
        baseline, _ = self.process()
        self.assertNotIn("Prochazka", self.markdown(baseline))
        area = self.area_of(first, "Jan Prochazka")
        catalog, results = self.process(self.draw(baseline, 1, area))
        self.assertEqual([error for _, _, error in results], ["", "", ""])
        text = self.markdown(catalog)
        # The full name is under the area (and nowhere in the output); the bare surname elsewhere is replaced.
        self.assertNotIn("Prochazka", text)
        self.assertRegex(self.section(catalog, 2), r"\[O\d+\]")
        copy = self.released(results)["b.pdf"]
        self.assertNotIn("PROCHAZKA", self.fold(self.copy_text(copy)))
        self.assertTrue(self.released(results)["a.pdf"].is_file())

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_mismatch_is_refused_and_the_rest_carries_on(self):
        from anonymizer.manual import Areas

        (self.source / "a.pdf").write_bytes(self.text_pdf())
        (self.source / "b.pdf").write_bytes(self.text_pdf())
        (self.source / "c.txt").write_text("Obycejny text bez udaju.", encoding="utf-8")
        baseline, _ = self.process()
        area = (0.1, 0.2, 0.4, 0.3)
        cases = {}
        # a file that changed after the areas were drawn
        areas = self.draw(baseline, 1, area)
        (self.source / "a.pdf").write_bytes(self.text_pdf(pages=2))
        cases["changed input"] = (areas, "a.pdf")
        # a page the document does not have
        cases["missing page"] = (self.draw(baseline, 2, area, page=4), "b.pdf")
        # an item without pages
        cases["not a page format"] = (self.draw(baseline, 3, area), "c.txt")
        # an item that does not exist
        absent = Areas()
        absent.add((2, "7"), baseline.inputs[2][1], baseline.inputs[2][0], 1, area)
        cases["absent item"] = (absent, "b.pdf")
        for label, (areas, refused) in cases.items():
            with self.subTest(label):
                catalog, results = self.process(areas)
                outcome = {source.name: (target, error) for source, target, error in results if source is not None}
                for name, (target, error) in outcome.items():
                    if name == refused:
                        self.assertIsNone(target)
                        self.assertEqual(error, MANUAL_MISMATCH)
                    else:
                        self.assertIsNotNone(target, name)
                        self.assertEqual(error, "")
                markdown = self.markdown(catalog)
                self.assertRegex(markdown, r"položka \d+: manual_area_mismatch")
                self.assertEqual(markdown.count("manual_area_mismatch"), 1)
                self.assertIn("PLANETARIUM", markdown)
                self.assertEqual(set(catalog.copies), {1, 2, 3} - {{"a.pdf": 1, "b.pdf": 2, "c.txt": 3}[refused]})
        # Nothing but the code and the count reached the audit.
        events = [json.loads(line) for path in self.audit.rglob("events.jsonl")
                  for line in path.read_text(encoding="utf-8").splitlines()]
        failures = [event for event in events if event["status"] == "failed"]
        self.assertTrue(failures)
        self.assertEqual({event["reason"] for event in failures}, {"manual_area_mismatch"})
        self.assertTrue(all(event["manual_areas"] == 1 for event in failures))

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_without_areas_the_outputs_are_identical(self):
        from anonymizer.manual import Areas

        (self.source / "a.pdf").write_bytes(self.text_pdf())
        (self.source / "b.pdf").write_bytes(self.case_pdf("Zalobce Jan Prochazka, tel. 777 123 456."))
        data, rgb, tiff, png, jpeg = self.images()
        (self.source / "obraz.png").write_bytes(png)
        runs = []
        for areas in (None, Areas()):
            target = self.root / f"out{len(runs)}"
            target.mkdir()
            self.target = target
            catalog, results = self.process(areas)
            runs.append((self.markdown(catalog),
                         {source.name: error for source, _, error in results if source is not None},
                         {path.name: self.content(path) for path in sorted(target.iterdir())
                          if path.suffix in {".png", ".pdf"}}))
        self.assertEqual(runs[0][0], runs[1][0])
        self.assertEqual(runs[0][1], runs[1][1])
        self.assertEqual(runs[0][2], runs[1][2])
        self.assertTrue(runs[0][2])

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_coordinates_are_nowhere_on_the_disk(self):
        (self.source / "a.pdf").write_bytes(self.text_pdf())
        baseline, _ = self.process()
        area = self.area_of(self.text_pdf(), self.HIDDEN)
        catalog, results = self.process(self.draw(baseline, 1, area))
        events = [json.loads(line) for path in self.audit.rglob("events.jsonl")
                  for line in path.read_text(encoding="utf-8").splitlines()]
        counted = [event for event in events if "manual_areas" in event]
        self.assertEqual({(event["status"], event["manual_areas"]) for event in counted},
                         {("extracted", 1), ("processing", 1), ("released", 1)})
        everything = b"".join(path.read_bytes() for path in self.root.rglob("*")
                              if path.is_file() and path.suffix in {".jsonl", ".txt", ".md", ".json"})
        for value in area:
            self.assertNotIn(f"{value:.4f}".encode(), everything)
        for event in events:
            self.assertFalse(any(isinstance(item, float) for item in event.values()), event)
        # The module of the areas has no way to write anything.
        from anonymizer import manual

        source = Path(manual.__file__).read_text(encoding="utf-8")
        for forbidden in ("open(", "write", "QSettings", "import logging", "tempfile", "json"):
            self.assertNotIn(forbidden, source)

    # -- window and drawing -----------------------------------------------------------------------------------

    def drag(self, dialog, start, end):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        label = dialog.original_image
        QTest.mousePress(label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*start))
        QTest.mouseMove(label, QPoint(*end))
        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*end))

    def test_drawing_gives_normalized_areas_at_every_zoom(self):
        from anonymizer import preview
        from anonymizer.manual import FIXED_NOTE

        (self.source / "a.pdf").write_bytes(self.text_pdf(pages=2))
        (self.source / "c.txt").write_text("Text.", encoding="utf-8")
        catalog, _ = self.process(copies=False)
        dialog = preview.PreviewDialog(catalog)
        self.addCleanup(dialog.release)
        dialog.show()
        self.assertEqual(dialog.note.text(), FIXED_NOTE)
        self.assertEqual(dialog.note.text(), "Ruční plocha platí jen pro toto místo, stejný údaj jinde ve spisu neodstraní.")
        dialog.items.setCurrentRow(0)
        self.assertTrue(dialog.draw_toggle.isEnabled())
        self.assertFalse(any(button.isEnabled() for button in (dialog.undo, dialog.reset, dialog.use)))
        # Without the drawing mode a drag draws nothing.
        self.drag(dialog, (10, 10), (60, 60))
        self.assertEqual(len(dialog.areas), 0)
        dialog.draw_toggle.setChecked(True)
        locator = catalog.documents[1]["locator"]
        seen = []
        for zoom_steps in (0, 2, -2):
            dialog.zoom = preview.ZOOMS.index(1.0)
            dialog.rezoom(zoom_steps)
            width, height = dialog.original_image.pixmap().width(), dialog.original_image.pixmap().height()
            self.drag(dialog, (round(0.1 * width), round(0.2 * height)), (round(0.5 * width), round(0.4 * height)))
            seen.append(dialog.areas.rectangles(locator, 1)[-1])
        for rectangle in seen:
            for value, wanted in zip(rectangle, (0.1, 0.2, 0.5, 0.4)):
                self.assertAlmostEqual(value, wanted, delta=0.01)
        # A drag against the direction and over the edge is clipped; under 2x2 pixels is ignored.
        self.drag(dialog, (width + 40, height + 40), (width - 50, height - 50))
        self.assertTrue(all(0 <= value <= 1 for value in dialog.areas.rectangles(locator, 1)[-1]))
        before = len(dialog.areas)
        self.drag(dialog, (30, 30), (31, 31))
        self.assertEqual(len(dialog.areas), before)
        # Another page and undo / clear.
        dialog.number.setValue(2)
        self.drag(dialog, (20, 20), (80, 80))
        self.assertEqual(len(dialog.areas.rectangles(locator, 2)), 1)
        self.assertEqual(len(dialog.areas.rectangles(locator, 1)), before)
        dialog.undo.click()
        self.assertEqual((len(dialog.areas.rectangles(locator, 2)), len(dialog.areas)), (0, before))
        dialog.reset.click()
        self.assertEqual(len(dialog.areas), 0)
        self.assertFalse(dialog.use.isEnabled())
        # A text item cannot be drawn on.
        dialog.items.setCurrentRow(1)
        self.assertFalse(dialog.draw_toggle.isEnabled())
        self.assertFalse(dialog.original_image.drawing)
        self.drag(dialog, (10, 10), (60, 60))
        self.assertEqual(len(dialog.areas), 0)
        # "Použít" closes the window with the request.
        dialog.items.setCurrentRow(0)
        dialog.draw_toggle.setChecked(True)
        self.drag(dialog, (10, 10), (60, 60))
        self.assertTrue(dialog.use.isEnabled())
        dialog.use.click()
        self.assertTrue(dialog.apply_requested)
        self.assertFalse(dialog.isVisible())

    def test_window_repeats_the_batch_with_areas_and_drops_them_with_the_catalog(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QSettings
        from anonymizer import gui, preview
        from anonymizer.manual import Areas, DISCARD_NOTE, REPEAT_NOTE

        (self.source / "a.pdf").write_bytes(self.text_pdf())
        catalog, _ = self.process(copies=False)
        ini = str(self.root / "settings.ini")
        with mock.patch("anonymizer.gui.QSettings", lambda *args: QSettings(ini, QSettings.Format.IniFormat)):
            window = gui.Window()
        window.timer.stop()
        window.source, window.output = str(self.source), str(self.target)
        window.catalog = catalog
        window.areas = self.draw(catalog, 1, (0.1, 0.1, 0.4, 0.2))
        window.last_run = (self.source, self.target, [self.source / "a.pdf"])
        with mock.patch("anonymizer.gui.BatchWorker") as worker:
            window.rerun()
            worker.assert_called_once()
            self.assertIs(worker.call_args.kwargs["manual_areas"], window.areas)
            self.assertEqual(worker.call_args.args[4], [self.source / "a.pdf"])
        self.assertIs(window.worker, worker.return_value)
        window.worker = None
        # Another batch, a changed folder or "forget" drop the areas and say so with one fixed sentence.
        window.areas = self.draw(catalog, 1, (0.1, 0.1, 0.4, 0.2))
        window.forget_catalog()
        self.assertIsNone(window.areas)
        self.assertIn(DISCARD_NOTE, window.log.toPlainText())
        # The worker announces the repeated processing with a fixed sentence.
        messages = []
        worker = gui.BatchWorker(str(self.source), str(self.target), None, None, [], copies=False,
                                 manual_areas=self.draw(catalog, 1, (0.1, 0.1, 0.4, 0.2)))
        worker.result.connect(messages.append)
        with mock.patch("anonymizer.gui.process_folders", return_value=iter(())):
            worker.run()
        self.assertEqual(messages[:1], [REPEAT_NOTE])
        self.assertEqual(REPEAT_NOTE, "Ruční plochy platí jen pro toto zpracování. "
                                      "Starší výstup bez nich zůstal ve výstupní složce, nesdílejte ho.")

    def test_areas_follow_their_files_when_the_selection_shifts(self):
        for name, word in (("a.pdf", "Alfa"), ("b.pdf", "Zelvak"), ("c.pdf", "Kvetak")):
            with pdf.open() as document:
                document.new_page().insert_text((72, 100), word, fontsize=20)
                document.save(self.source / name)
        for name in ("b.pdf", "c.pdf"):
            os.utime(self.source / name, ns=(10 ** 18, 10 ** 18))
        selected = [self.source / name for name in ("a.pdf", "b.pdf", "c.pdf")]
        from anonymizer.preview import Catalog

        catalog = Catalog()
        list(process_folders(self.source, self.target, self.TESSDATA, selected=selected, markdown=True,
                             copies=False, catalog=catalog.update))
        areas = self.draw(catalog, 2, (0.0, 0.0, 1.0, 0.3))
        (self.source / "a.pdf").unlink()
        before = set(self.target.rglob("*"))
        with self.assertRaises(ValueError) as raised:
            list(process_folders(self.source, self.target, self.TESSDATA, selected=selected, markdown=True,
                                 copies=True, manual_areas=areas))
        self.assertEqual(str(raised.exception), MANUAL_MISMATCH)
        self.assertEqual(set(self.target.rglob("*")) - before, set())

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_input_changed_before_the_copy_is_refused(self):
        from anonymizer import batch

        (self.source / "a.pdf").write_bytes(self.text_pdf())
        baseline, _ = self.process()
        areas = self.draw(baseline, 1, (0.1, 0.1, 0.4, 0.2))
        real, calls = batch.input_signature, []

        def changing(source):
            calls.append(source)
            value = real(source)
            return value if len(calls) == 1 else (value[0] + 1, value[1])

        with mock.patch("anonymizer.batch.input_signature", changing):
            catalog, results = self.process(areas)
        outcome = [(target, error) for source, target, error in results if source is not None]
        self.assertEqual(outcome, [(None, MANUAL_MISMATCH)])
        self.assertIn("PLANETARIUM", self.markdown(catalog))

    @unittest.skipUnless(TESSDATA, "chybí SPISREDAKT_TEST_TESSDATA")
    def test_real_format_decides_the_exif_check_in_an_archive(self):
        import struct

        data, rgb, tiff, png, jpeg = self.images()
        exif = b"Exif\0\0MM\0*\0\0\0\x08\0\x01\x01\x12\0\x03\0\0\0\x01\0\x06\0\0\0\0\0\0"
        turned = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
        with ZipFile(self.source / "spis.zip", "w") as archive:
            archive.writestr("foto.bmp", turned)
        baseline, _ = self.process()
        areas = self.draw(baseline, 1, self.area_of(data, self.HIDDEN))
        catalog, results = self.process(areas)
        self.assertIn("manual_area_mismatch", self.markdown(catalog))

    def test_mismatch_message_is_a_fixed_text_with_a_code(self):
        from anonymizer.reasons import PREVIEW_MESSAGES

        self.assertEqual(reason_code(ValueError(MANUAL_MISMATCH)), "manual_area_mismatch")
        self.assertIn("manual_area_mismatch", ADVICE)
        self.assertEqual(advice(MANUAL_MISMATCH), ADVICE["manual_area_mismatch"])
        self.assertTrue(MANUAL_MISMATCH.endswith("."))
        self.assertTrue(set(PREVIEW_MESSAGES))


if __name__ == "__main__":
    unittest.main()
