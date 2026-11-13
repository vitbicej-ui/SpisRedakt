"""Folder watching: the pure decision logic and the whole application loop (headless)."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from anonymizer.watch import MAX_RETRIES, Watcher

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
MAIL = "osoba@example.test"


class Folders(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.output = self.root / "vstup", self.root / "výstup"
        self.source.mkdir()
        self.output.mkdir()

    def write(self, name, text=MAIL):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path.resolve()


class WatcherLogic(Folders):
    def ready(self, watcher):
        return set(watcher.scan(self.source, self.output))

    def test_file_must_be_stable_for_two_scans(self):
        watcher = Watcher()
        path = self.write("a.txt", "první část")
        self.assertEqual(self.ready(watcher), set())
        with path.open("a", encoding="utf-8") as stream:      # still being copied
            stream.write(" druhá část")
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000))
        self.assertEqual(self.ready(watcher), set())
        self.assertEqual(self.ready(watcher), {path})

    def test_handled_survives_restart_and_changed_file_returns(self):
        watcher = Watcher()
        path = self.write("a.txt")
        self.ready(watcher)
        (key, signature), = watcher.scan(self.source, self.output).values()
        self.assertTrue(watcher.result(key, signature))
        restarted = Watcher(dict(watcher.handled))             # what the app stores in its settings
        self.assertEqual(self.ready(restarted), set())
        self.assertEqual(self.ready(restarted), set())
        path.write_text(MAIL + " změna", encoding="utf-8")
        os.utime(path, ns=(time.time_ns(), time.time_ns() + 9_000_000))
        self.ready(restarted)
        self.assertEqual(self.ready(restarted), {path})

    def test_rename_is_new_file_and_deleted_output_is_not_redone(self):
        watcher = Watcher()
        path = self.write("a.txt")
        self.ready(watcher)
        for key, signature in watcher.scan(self.source, self.output).values():
            watcher.result(key, signature)
        (self.output / "a_anonymizovano.txt").write_text("x", encoding="utf-8")
        (self.output / "a_anonymizovano.txt").unlink()         # user deletes the output
        self.assertEqual(self.ready(watcher), set())
        self.assertEqual(self.ready(watcher), set())
        renamed = path.rename(path.with_name("b.txt"))
        self.ready(watcher)
        self.assertEqual(self.ready(watcher), {renamed})
        watcher.reset()                                        # "Zpracovat složku znovu"
        self.assertEqual(self.ready(watcher), {renamed})

    def test_locked_file_waits_and_transient_errors_retry(self):
        watcher = Watcher()
        path = self.write("a.txt")
        self.ready(watcher)
        with mock.patch("anonymizer.watch.readable", return_value=False):
            self.assertEqual(self.ready(watcher), set())
        (key, signature), = watcher.scan(self.source, self.output).values()
        for _ in range(MAX_RETRIES - 1):
            self.assertFalse(watcher.result(key, signature, "PermissionError"))
            self.assertEqual(self.ready(watcher), {path})
        self.assertTrue(watcher.result(key, signature, "PermissionError"))
        self.assertEqual(self.ready(watcher), set())
        other = self.write("b.txt")
        self.ready(watcher)
        (key, signature), = watcher.scan(self.source, self.output).values()
        self.assertTrue(watcher.result(key, signature, "Formulář nebyl vydán"))    # a verdict is final
        self.assertNotIn(other, self.ready(watcher))

    def test_folders_must_exist_and_be_separate(self):
        watcher = Watcher()
        with self.assertRaises(ValueError):
            watcher.scan(self.source, self.source)
        with self.assertRaises(ValueError):
            watcher.scan(self.source, self.source / "..")
        with self.assertRaises(OSError):
            watcher.scan(self.source, self.root / "odpojeno")
        self.write("~$zamek.docx")
        self.write("pod/slozka/a.txt")
        watcher.scan(self.source, self.output)
        self.assertEqual({path.name for path in watcher.scan(self.source, self.output)}, {"a.txt"})


class ApplicationLoop(Folders):
    """The real window and worker thread, driven without a display."""

    def setUp(self):
        super().setUp()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 není k dispozici.")
        self.app = QApplication.instance() or QApplication([])
        patch = mock.patch.dict(os.environ, {"SPISREDAKT_SETTINGS": str(self.root / "nastaveni.ini"),
                                             "SPISREDAKT_AUDIT_DIR": str(self.root / "audit")})
        patch.start()
        self.addCleanup(patch.stop)

    def window(self):
        from anonymizer.gui import Window
        window = Window()
        window.timer.stop()                                    # the test drives the scans itself
        self.addCleanup(window.deleteLater)
        return window

    def cycle(self, window, scans=2):
        for _ in range(scans):
            window.scan()
            deadline = time.monotonic() + 60
            while window.worker is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
            self.assertIsNone(window.worker, "zpracování neskončilo")
        self.app.processEvents()

    def outputs(self):
        return sorted(str(path.relative_to(self.output)).replace("\\", "/") for path in self.output.rglob("*") if path.is_file())

    def test_watch_restart_collisions_and_failures(self):
        self.write("a.txt")
        self.write("pod/b.txt", "Kontakt: " + MAIL)
        (self.source / "spatny.docx").write_bytes(b"neni docx")
        first = self.window()
        self.assertIn("Nejdřív nastavte", first.status.text())
        first.source, first.output = str(self.source), str(self.output)
        first.settings.setValue("input", first.source)
        first.settings.setValue("output", first.output)
        self.cycle(first)
        self.assertEqual(self.outputs(), ["a_anonymizovano.txt", "pod/b_anonymizovano.txt"])
        self.assertEqual((self.output / "pod" / "b_anonymizovano.txt").read_text(encoding="utf-8"), "Kontakt: [EMAIL-1]")
        self.assertEqual((self.source / "a.txt").read_text(encoding="utf-8"), MAIL)     # originals untouched
        self.assertIn("Chyba", first.log.toPlainText())
        self.cycle(first)                                       # nothing new: nothing is produced again
        self.assertEqual(len(self.outputs()), 2)
        first.close()

        second = self.window()                                  # application restarted
        self.assertEqual(second.source, str(self.source))       # folders are remembered
        self.cycle(second)
        self.assertEqual(len(self.outputs()), 2)
        (self.output / "a_anonymizovano.txt").unlink()          # user removes an output
        self.cycle(second)
        self.assertEqual(self.outputs(), ["pod/b_anonymizovano.txt"])
        self.write("c.txt")                                     # a new file arrives while running
        self.cycle(second)
        self.assertEqual(self.outputs(), ["c_anonymizovano.txt", "pod/b_anonymizovano.txt"])
        second.retry()                                          # explicit repeat: free numbered names
        self.cycle(second)
        self.assertEqual(self.outputs(), ["a_anonymizovano.txt", "c_anonymizovano.txt", "c_anonymizovano_2.txt",
                                          "pod/b_anonymizovano.txt", "pod/b_anonymizovano_2.txt"])
        second.output = str(self.root / "odpojeno")             # output drive disappears
        second.scan()
        self.assertIn("nedostupná", second.status.text())
        self.assertIsNone(second.worker)
        second.output = str(self.output)                        # and comes back
        self.write("d.txt")
        self.cycle(second)
        self.assertIn("d_anonymizovano.txt", self.outputs())
        self.assertFalse(any(part.startswith(".spisredakt-") for name in self.outputs() for part in name.split("/")))


if __name__ == "__main__":
    unittest.main()
