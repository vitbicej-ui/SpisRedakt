"""The two reference scenarios, checked against ground truth that detectors never see."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock
from zipfile import ZipFile

from docx import Document
import pymupdf as pdf

sys.path.insert(0, str(Path(__file__).parent))
import corpus                                                    # noqa: E402
from anonymizer import documents, office                          # noqa: E402
from anonymizer.batch import process_folders                      # noqa: E402
from anonymizer.czech import fold                                 # noqa: E402
from anonymizer.rules import Rules                                # noqa: E402

TESSDATA = os.environ.get("SPISREDAKT_TEST_TESSDATA")
SCALE = pdf.Matrix(300 / 72, 300 / 72)
NEUTRAL = {"muž", "Slovenská republika", "<QR>"}


def squeeze(text):
    return re.sub(r"\s+", "", fold(text))


def crop(pixmap, area):
    part = pdf.Pixmap(pixmap.colorspace, area, False)
    part.copy(pixmap, area)
    return part.samples


class Scenario(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.output, self.audit = (self.root / name for name in ("input", "output", "audit"))
        self.source.mkdir()
        self.output.mkdir()

    def run_one(self, name, document=None):
        path = self.source / name
        if document is not None:
            document.save(path)
            document.close()
        results = list(process_folders(self.source, self.output, TESSDATA, self.audit))
        self.assertEqual(len(results), 1)
        events = [json.loads(line) for folder in sorted(self.audit.iterdir(), key=lambda item: item.stat().st_mtime)
                  for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        return path, results[0][1], results[0][2], events

    def assert_pdf(self, source, target, truth, labels_kept=True):
        """Secret rectangles are black, kept rectangles are pixel-identical to the source."""
        with pdf.open(source) as original, pdf.open(target) as result:
            self.assertEqual(len(original), len(result))
            for number, page in enumerate(result):
                self.assertEqual(page.get_text().strip(), "")
                image = pdf.Pixmap(result, page.get_images()[0][0])
                reference = original[number].get_pixmap(matrix=SCALE, annots=False, alpha=False)
                self.assertEqual((image.width, image.height), (reference.width, reference.height))
                for where, rect, text in truth.secret:
                    if where != number:
                        continue
                    colors = image.color_count(clip=(rect * SCALE).irect, colors=True)
                    total = sum(colors.values())
                    ink = sum(count for color, count in colors.items() if color != b"\0\0\0" and min(color) < 200)
                    self.assertGreaterEqual(colors.get(b"\0\0\0", 0) / total, 0.6, f"unik: {text!r} na strane {number + 1}")
                    self.assertLessEqual(ink / total, 0.002, f"unik: zbytek pisma {text!r} na strane {number + 1}")
                for where, rect, text in truth.keep if labels_kept else ():
                    if where != number:
                        continue
                    area = (pdf.Rect(rect.x0 + 1.5, rect.y0 + 0.36 * rect.height, rect.x1 - 1.5, rect.y1 - 0.28 * rect.height) * SCALE).irect
                    ours, theirs = crop(image, area), crop(reference, area)
                    changed = sum(ours[i:i + 3] != theirs[i:i + 3] for i in range(0, len(ours), 3))
                    self.assertLessEqual(changed / (len(ours) / 3), 0.03, f"poskozeno: {text!r}")
                if TESSDATA:
                    seen = squeeze(documents.recognize_image(image, TESSDATA)[0])
                    for text in truth.secret_texts:
                        if text not in NEUTRAL:
                            self.assertNotIn(squeeze(text), seen, f"OCR precetlo {text!r}")


@unittest.skipUnless(TESSDATA and corpus.FONT, "Chybí data OCR nebo písmo s diakritikou.")
class SlovakRegister(Scenario):
    def release(self, name, scan=False, damage=None, **options):
        document, truth = corpus.slovak_register(**options)
        if scan:
            document = corpus.as_scan(document, damage=damage)
        source, target, reason, events = self.run_one(name, document)
        self.assertIsNotNone(target, reason)
        return source, target, truth, events

    def test_text_pdf_variants(self):
        for layout in ("beside", "below"):
            for order in ("rows", "values_first", "columns"):
                with self.subTest(layout=layout, order=order):
                    self.setUp()
                    source, target, truth, events = self.release("vypis.pdf", layout=layout, order=order)
                    self.assert_pdf(source, target, truth)
                    released = [event for event in events if event["status"] == "released"][0]
                    self.assertTrue(released["checks"]["form"])
                    self.assertEqual(released["checks"]["output_reread"], "ocr")
                    self.assertFalse(released["checks"]["form_extended"])

    def test_template_without_title(self):
        source, target, truth, _ = self.release("bez-nadpisu.pdf", title=False)
        self.assert_pdf(source, target, truth)

    def test_scans(self):
        for layout in ("beside", "below"):
            with self.subTest(layout=layout):
                self.setUp()
                source, target, truth, _ = self.release("sken.pdf", scan=True, layout=layout)
                self.assert_pdf(source, target, truth)

    def test_scan_with_damaged_labels_never_leaks(self):
        document, truth = corpus.slovak_register()
        labels = [rect for _, rect, text in truth.keep if text.rstrip(":") in ("Pohlavie", "Kód výpisu", "Miesto narodenia")]
        source, target, reason, events = self.run_one("poskozeny.pdf", corpus.as_scan(document, damage={0: labels}))
        if target is None:
            self.assertEqual(events[-2]["status"], "withheld", reason)
        else:
            self.assert_pdf(source, target, truth, labels_kept=False)

    def test_missing_fields(self):
        source, target, reason, events = self.run_one("kratky.pdf", corpus.slovak_register(fields=corpus.SK_FIELDS[:5])[0])
        self.assertIsNone(target)
        self.assertIn("form_incomplete", [event.get("code") for event in events])
        self.assertFalse(any(self.output.rglob("*.pdf")))
        self.setUp()
        partial = corpus.SK_FIELDS[:5] + corpus.SK_FIELDS[6:10]
        document, truth = corpus.slovak_register(fields=partial)
        source, target, reason, events = self.run_one("neuplny.pdf", document)
        self.assertIsNotNone(target, reason)
        self.assert_pdf(source, target, truth, labels_kept=False)
        self.assertTrue([event for event in events if event["status"] == "released"][0]["checks"]["form_extended"])

    def test_second_page_with_name_only(self):
        document, truth = corpus.slovak_register()
        page = document.new_page()
        corpus.put(page, truth, 60, 80, "Register trestov – pokračovanie", False)
        x = corpus.put(page, truth, 60, 110, "Osoba ", None)
        corpus.put(page, truth, x, 110, corpus.SK_NAME, True)
        corpus.put(page, truth, 60, 140, "nemá ďalšie záznamy.", False)
        source, target, reason, _ = self.run_one("dve-strany.pdf", document)
        self.assertIsNotNone(target, reason)
        self.assert_pdf(source, target, truth)

    def test_gate_catches_field_the_detector_never_reported(self):
        """Break detection on purpose: the release check must notice the visible value."""
        document, truth = corpus.slovak_register()
        victim = next(rect for _, rect, text in truth.secret if text == "K7Q9-XW42-TT01") * SCALE
        genuine = documents.form_areas

        def broken(labels, width, extend):
            return [area for area in genuine(labels, width, extend) if not area.intersects(victim)]

        with mock.patch.object(documents, "form_areas", broken), \
                mock.patch.object(Rules, "remember_literal", lambda *args, **kwargs: None):
            source, target, reason, events = self.run_one("porucha.pdf", document)
        self.assertIsNone(target)
        self.assertIn("form_field_unredacted", [event.get("code") for event in events])
        self.assertFalse(any(self.output.rglob("*.pdf")))


@unittest.skipUnless(TESSDATA and corpus.FONT, "Chybí data OCR nebo písmo s diakritikou.")
class MixedBatch(Scenario):
    def test_every_format_twice(self):
        import mixed
        expected = mixed.build(self.source)
        results = list(process_folders(self.source, self.output, TESSDATA, self.audit))
        self.assertEqual(mixed.verify(self.source, self.output, TESSDATA), [])
        events = [json.loads(line) for line in next(self.audit.glob("*/events.jsonl")).read_text(encoding="utf-8").splitlines()]
        counts = events[-1]["counts"]
        verdicts = list(expected.values())
        self.assertEqual(counts, {key: verdicts.count(key) for key in ("released", "withheld", "failed", "skipped")})
        self.assertNotIn("Kratochv", json.dumps(events, ensure_ascii=False))
        self.assertTrue(all("checks" in event for event in events if event["status"] == "released"))
        self.assertEqual(len(results), len(expected) - 1)         # the unknown binary is skipped silently
        # second run of the same batch: originals stay, results get free numbered names
        again = list(process_folders(self.source, self.output, TESSDATA, self.audit))
        self.assertEqual(sum(target is not None for _, target, _ in again), verdicts.count("released"))
        self.assertTrue(all(target is None or "_anonymizovano_2" in target.name for _, target, _ in again))


class CzechOrder(Scenario):
    def test_docx_output(self):
        path = self.source / "usneseni.docx"
        corpus.czech_order_docx(path)
        original = path.read_bytes()
        source, target, reason, events = self.run_one("usneseni.docx")
        self.assertIsNotNone(target, reason)
        self.assertEqual(path.read_bytes(), original)
        with ZipFile(target) as archive:
            for name in archive.namelist():
                raw = archive.read(name)
                plain = squeeze(re.sub(r"<[^>]*>", "", raw.decode("utf-8", "ignore")))
                for fragment in corpus.CZ_FRAGMENTS:
                    self.assertNotIn(squeeze(fragment), plain, f"unik {fragment!r} v {name}")
        result = Document(target)
        text = "\n".join(paragraph.text for paragraph in result.paragraphs)
        for value in corpus.CZ_KEEP:
            self.assertIn(value, text)
        self.assertEqual(result.paragraphs[0].style.name, "Heading 1")
        self.assertEqual(len(result.paragraphs), len(corpus.CZ_LINES))
        self.assertTrue(any(run.underline and run.text == "125 000 Kč" for p in result.paragraphs for run in p.runs))
        self.assertTrue(any(run.bold and run.text.startswith("[") for p in result.paragraphs for run in p.runs))
        self.assertEqual(result.tables[0].cell(1, 1).text, "125 000 Kč")
        # one person keeps one label through every case form, and people do not merge
        people = set(re.findall(r"\[OSOBA-\d+\]", text))
        self.assertEqual(len(people), 4, text)
        accused = re.search(r"žalovanému: (\[OSOBA-\d+\])", text)[1]
        for cue in (r"I\. Žalovaný ", r"S ", r"Výzva byla ", r"že pan ", r"\ns "):
            self.assertRegex(text, cue + re.escape(accused))
        self.assertEqual(result.tables[0].cell(0, 1).text, accused)
        self.assertEqual(result.sections[0].header.paragraphs[0].text, f"Věc: {accused} – zaplacení 125 000 Kč")
        self.assertNotIn("Kratochv", result.core_properties.author + result.core_properties.title)
        self.assertEqual(len(set(re.findall(r"\[ORGANIZACE-\d+\]", text))), 1)
        released = [event for event in events if event["status"] == "released"][0]
        self.assertEqual(released["checks"]["output_reread"], "package")

    @unittest.skipUnless(corpus.FONT, "Chybí písmo s diakritikou.")
    def test_pdf_output(self):
        document, truth = corpus.czech_order_pdf()
        source, target, reason, _ = self.run_one("usneseni.pdf", document)
        self.assertIsNotNone(target, reason)
        self.assert_pdf(source, target, truth)

    @unittest.skipUnless(TESSDATA and corpus.FONT, "Chybí data OCR nebo písmo s diakritikou.")
    def test_scanned_pdf_output(self):
        document, truth = corpus.czech_order_pdf()
        source, target, reason, _ = self.run_one("usneseni-sken.pdf", corpus.as_scan(document, dpi=300))
        self.assertIsNotNone(target, reason)
        self.assert_pdf(source, target, truth)

    def test_broken_text_redaction_is_caught_by_release_check(self):
        path = self.source / "usneseni.docx"
        corpus.czech_order_docx(path)
        with mock.patch.object(office, "redact_nodes", lambda nodes, rules: None):
            _, target, reason, events = self.run_one("usneseni.docx")
        self.assertIsNone(target)
        self.assertIn("residual_data", [event.get("code") for event in events])
        self.assertFalse(any(self.output.rglob("*.docx")))

    def test_similar_names_stay_distinct(self):
        rules = Rules()
        text = ("Žalobce Jan Novák a žalovaná Jana Nováková. Svědek Jan Nováček. Pan Petr Novák. "
                "Janu Novákovi, Janě Novákové, Janem Nováčkem, Petra Nováka.")
        output = rules.replace(text)[0]
        labels = re.findall(r"\[OSOBA-\d+\]", output)
        self.assertEqual(len(labels), 8, output)
        self.assertEqual(len(set(labels[:4])), 4, output)
        self.assertEqual(labels[4:], labels[:4], output)

    def test_hard_cases(self):
        from challenge import CASES
        for text, secrets, keeps in CASES:
            with self.subTest(text=text[:40]):
                output = Rules().replace(text)[0]
                for value in secrets:
                    self.assertNotIn(value, output)
                for value in keeps:
                    self.assertIn(value, output)

    def test_random_documents_have_no_known_leak(self):
        import measure
        totals = measure.measure(1000, 40, verbose=False)
        self.assertEqual((totals["leaks"], totals["false_hits"]), (0, 0), dict(totals))

    def test_findings_from_real_office_files(self):
        """Cases met in files written by Word, Excel and PowerPoint themselves."""
        rules = Rules()
        self.assertEqual(rules.find("Účastník\n\nKontakt\n\nČástka", generic=False), [])     # separate cells
        self.assertEqual(rules.find("microsoft.com:LAMBDA_WF", generic=False), [])          # feature flags
        self.assertEqual(rules.find("Times New Roman", generic=False), [])                  # font names
        from anonymizer.office import DRAW, redact_office
        source, target = self.root / "deck.pptx", self.root / "deck-out.pptx"
        with ZipFile(source, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<root/>")
            archive.writestr("ppt/slides/slide1.xml", f'<s xmlns:a="{DRAW}"><a:p><a:r><a:t>Věc Jan Novák</a:t></a:r></a:p></s>')
            archive.writestr("docProps/app.xml", '<Properties xmlns="urn:x" xmlns:vt="urn:v"><Company>Novák s.r.o.</Company>'
                             '<TitlesOfParts><vt:lpstr>Věc Jan Novák</vt:lpstr></TitlesOfParts><Slides>1</Slides></Properties>')
        redact_office(source, target, Rules())
        with ZipFile(target) as archive:
            properties = archive.read("docProps/app.xml").decode()
            self.assertNotIn("Novák", properties)
            self.assertIn("<Slides>1</Slides>", properties)
            self.assertNotIn("Novák", archive.read("ppt/slides/slide1.xml").decode())

    def test_values_kept(self):
        rules = Rules()
        for text in ("o zaplacení 725 000 000 Kč", "částku 250 000 000,- Kč", "dne 7. 5. 2026 v 9:00 hodin",
                     "podle § 142 odst. 1 zákona č. 99/1963 Sb.", "ve lhůtě 15 dnů", "úrok 8,05 % ročně"):
            self.assertEqual(rules.replace(text)[0], text)


if __name__ == "__main__":
    unittest.main()
