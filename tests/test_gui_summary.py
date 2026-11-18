import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from anonymizer.gui import REMINDER, BatchWorker
from anonymizer.reasons import ADVICE, GENERAL_ADVICE, REASONS

PACKAGE = Path(__file__).resolve().parent.parent / "anonymizer"
CODE = r'"([a-z]+(?:_[a-z0-9]+)+)"'


def literal_codes(module, patterns):
    text = (PACKAGE / module).read_text(encoding="utf-8")
    found = set()
    for pattern in patterns:
        found.update(re.findall(pattern, text))
    return found


class AdviceCoverageTests(unittest.TestCase):
    def assert_specific(self, codes):
        self.assertTrue(codes)
        for code in sorted(codes):
            self.assertIn(code, ADVICE, code)
            self.assertNotEqual(ADVICE[code], GENERAL_ADVICE, code)
            self.assertGreater(len(ADVICE[code]), 20, code)

    def test_extract_codes_have_specific_advice(self):
        codes = literal_codes("extract.py", [r"_Skip\(" + CODE, r"FormatError\(" + CODE,
                                               r"Skipped\([^)]*?" + CODE, r"_item\([^)]*?" + CODE,
                                               r"skipped\([^)]*?" + CODE])
        codes |= {"attachment_unsupported", "processing_error", "format_unsupported"}
        self.assertTrue({"archive_depth", "ocr_data_missing", "pdf_encrypted_or_empty", "size_limit"} <= codes)
        self.assert_specific(codes)

    def test_archive_and_batch_codes_have_specific_advice(self):
        codes = literal_codes("formats.py", [r"FormatError\(" + CODE]) | literal_codes("batch.py", [
            r"FormatError\(" + CODE, r"Skipped\([^)]*?" + CODE])
        codes |= {"archive_depth", "archive_limit", "archive_encrypted", "archive_member_unsupported",
                  "markdown_release_check", "processing_error", "format_unsupported"}
        self.assert_specific(codes)

    def test_every_reason_code_has_specific_advice(self):
        self.assert_specific({code for _, code in REASONS})


class SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_summary_has_reminder_and_no_file_names(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            source, output = root / "in", root / "out"
            source.mkdir()
            output.mkdir()
            names = ["Novak_Jan_880101-1234.txt", "Rozsudek_Kovarova.txt"]
            for name in names:
                (source / name).write_text("Text.", encoding="utf-8")
            markdown = output.resolve() / "spis_anonymizovano.md"
            summary = dict(markdown=markdown, documents=2, tokens_before=900, tokens_after=500,
                           skipped=[(3, "archive_encrypted"), (4, "format_unsupported")], error="")
            worker = BatchWorker(str(source), str(output), None, None, {source / n for n in names}, copies=False)
            lines = []
            worker.result.connect(lines.append)

            def fake(*args, **kwargs):
                kwargs["summary"](summary)
                yield None, markdown, ""

            with mock.patch("anonymizer.gui.process_folders", fake):
                os.environ["SPISREDAKT_AUDIT_DIR"] = str(root / "audit")
                self.addCleanup(os.environ.pop, "SPISREDAKT_AUDIT_DIR", None)
                worker.run()
        self.assertIn(REMINDER, lines)
        self.assertEqual(REMINDER, "Před nahráním zkontrolujte nezpracované položky a namátkou porovnejte "
                                   "výstup s originálem.")
        self.assertTrue(any(line.startswith("Odhad tokenů") for line in lines))
        joined = "\n".join(lines)
        for name in names:
            self.assertNotIn(name, joined)
            self.assertNotIn(Path(name).stem, joined)
        self.assertIn(f"    Položka 3: {ADVICE['archive_encrypted']}", lines)
        self.assertIn(f"    Položka 4: {ADVICE['format_unsupported']}", lines)


if __name__ == "__main__":
    unittest.main()
