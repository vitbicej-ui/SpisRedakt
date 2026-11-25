from pathlib import Path
import sys
import tempfile
import os
import hashlib
import time

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import legacy
from .batch import process_folders
from .preview import Catalog, PreviewDialog
from .reasons import ADVICE, GENERAL_ADVICE, advice


PROGRESS_INTERVAL = 0.25
REMINDER = ("Před nahráním zkontrolujte nezpracované položky a namátkou porovnejte výstup s originálem.")


def number(value):
    return f"{value:,}".replace(",", " ")


class BatchWorker(QThread):
    result = Signal(str)
    handled = Signal(str)
    progress = Signal(int, int)

    def __init__(self, source, output, tessdata, parent, selected, copies=True, split=False):
        super().__init__(parent)
        self.arguments = source, output, tessdata
        self.selected = selected
        self.copies = copies
        self.split = split
        self.last_progress = 0.0
        # What the preview may know (memory only); read by the window after the batch finished.
        self.catalog = Catalog()
        self.complete = False

    def report_progress(self, items, pages):
        # The batch runs in this thread; the interface is updated at most a few times per second.
        now = time.monotonic()
        if now - self.last_progress >= PROGRESS_INTERVAL:
            self.last_progress = now
            self.progress.emit(items, pages)

    def report_summary(self, summary):
        """Describe the Markdown result; only counts, codes and fixed advice, never document content."""
        output_root = Path(self.arguments[1]).resolve()
        if summary["error"]:
            self.result.emit(f"Markdown nebyl vydán. Chyba: {summary['error']}")
            self.result.emit(f"    Rada: {advice(summary['error'])}")
        elif summary["markdown"]:
            self.result.emit(f"Markdown: {Path(summary['markdown']).relative_to(output_root)}")
        self.result.emit(f"Dokumentů ve spisu: {number(summary['documents'])}")
        if summary["tokens_after"]:
            self.result.emit(f"Odhad tokenů: {number(summary['tokens_after'])} "
                             f"(před úpravami {number(summary['tokens_before'])})")
        skipped = summary["skipped"]
        self.result.emit(f"Nezpracovaných položek: {len(skipped)}")
        for order, code in skipped:
            self.result.emit(f"    Položka {order}: {ADVICE.get(code, GENERAL_ADVICE)}")
        self.result.emit(REMINDER)

    def run(self):
        try:
            audit_root = os.environ.get("SPISREDAKT_AUDIT_DIR") or Path(sys.argv[0]).resolve().parent / "audit"
            def audit_ready(path, fallback):
                prefix = "Náhradní složka auditu" if fallback else "Audit dávky"
                self.result.emit(f"{prefix}: {path}")
            remaining = set(self.selected)
            for source, target, error in process_folders(
                *self.arguments, audit_root=audit_root, audit_ready=audit_ready, selected=self.selected,
                markdown=True, copies=self.copies, split=self.split,
                progress=self.report_progress, summary=self.report_summary, catalog=self.catalog.update,
            ):
                if source is None:
                    continue
                source_root, output_root, _ = self.arguments
                message = str(target.relative_to(Path(output_root).resolve())) if target else f"Chyba: {error}"
                self.result.emit(f"{source.relative_to(Path(source_root).resolve())} → {message}")
                if not target:
                    self.result.emit(f"    Rada: {advice(error)}")
                remaining.discard(source)
                self.handled.emit(str(source))
            for source in sorted(remaining):
                if self.copies:
                    self.result.emit(f"{source.name} → přeskočeno")
                self.handled.emit(str(source))
            self.complete = True
        except ValueError as error:
            self.result.emit(str(error))
        except Exception as error:
            self.result.emit(type(error).__name__)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpisRedakt")
        self.resize(760, 510)
        self.worker = None
        self.preview = None
        self.catalog = None
        self.settings = QSettings("SpisRedakt", "SpisRedakt")
        self.source = self.settings.value("input", "", str)
        self.output = self.settings.value("output", "", str)
        self.observed = {}
        self.handled = self.settings.value("handled", {}) or {}
        self.pending = {}
        base = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        self.tessdata = base / "tessdata"

        self.setStyleSheet("""
            QWidget { background: #f4f7fa; color: #192e42; font-size: 14px; }
            QLabel#title { font-size: 29px; font-weight: 700; }
            QLabel#status { color: #205b57; font-weight: 600; padding: 8px 0; }
            QPushButton { background: #245b73; color: white; border: 0;
                          border-radius: 6px; padding: 9px 15px; }
            QPushButton:hover { background: #174457; }
            QPushButton:disabled { background: #647886; }
            QPlainTextEdit { background: white; border: 1px solid #cedae4;
                             border-radius: 8px; padding: 8px; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(12)
        title = QLabel("SpisRedakt")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("Automatické zpracování dokumentů"))
        self.buttons = []
        self.paths = {}
        for setting, title in (("source", "Vstupní složka"), ("output", "Výstupní složka")):
            row = QHBoxLayout()
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            self.paths[setting] = label
            button = QPushButton("Vybrat složku")
            button.clicked.connect(lambda checked=False, key=setting, name=title: self.choose(key, name))
            row.addWidget(label, 1)
            row.addWidget(button)
            layout.addLayout(row)
            self.buttons.append(button)
        self.options = {}
        for key, title in (("copies", "Vytvořit také anonymizovaný ZIP pro kontrolu"),
                           ("split", "Rozdělit po dokumentech"),
                           ("office", "Převádět DOC/XLS přes Word nebo LibreOffice")):
            box = QCheckBox(title)
            box.setChecked(self.settings.value(key, False, bool))
            box.toggled.connect(lambda checked, name=key: self.save_option(name, checked))
            self.options[key] = box
            if key == "office":
                legacy.USE_OFFICE = box.isChecked()
            self.buttons.append(box)
            layout.addWidget(box)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        retry = QPushButton("Zpracovat složku znovu")
        retry.clicked.connect(self.retry)
        self.buttons.append(retry)
        layout.addWidget(retry)
        self.preview_button = QPushButton("Náhled")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.open_preview)
        layout.addWidget(self.preview_button)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.document().setMaximumBlockCount(500)
        layout.addWidget(self.log, 1)
        self.refresh_paths()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self.timer.start(2500)

    def save_option(self, key, checked):
        if key == "office":
            legacy.USE_OFFICE = bool(checked)
        self.settings.setValue(key, bool(checked))
        self.settings.sync()

    def show_progress(self, items, pages):
        self.status.setText(f"Zpracováno položek: {number(items)}, stran: {number(pages)}")

    def refresh_paths(self):
        for key, name in (("source", "Vstup"), ("output", "Výstup")):
            self.paths[key].setText(f"{name}  ·  {getattr(self, key) or 'Vyberte složku'}")
        self.status.setText("Sledování je zapnuté, dokud je aplikace otevřená."
                            if self.source and self.output else "Nejdřív nastavte obě složky.")

    def choose(self, key, title):
        path = QFileDialog.getExistingDirectory(self, title, getattr(self, key))
        if path:
            setattr(self, key, path)
            self.settings.setValue("input" if key == "source" else "output", path)
            self.observed.clear()
            self.forget_catalog()
            self.refresh_paths()

    def forget_catalog(self):
        self.catalog = None
        self.preview_button.setEnabled(False)

    def open_preview(self):
        """Open the preview of the last finished batch; everything it shows stays in memory."""
        if self.preview is not None or self.worker is not None or not self.catalog:
            return
        self.preview = PreviewDialog(self.catalog, self)
        self.preview.finished.connect(self.preview_closed)
        self.preview.open()

    def preview_closed(self):
        dialog, self.preview = self.preview, None
        if dialog is not None:
            dialog.release()
            dialog.deleteLater()

    def retry(self):
        if self.source and self.output:
            self.handled.clear()
            self.settings.setValue("handled", self.handled)
            self.scan()

    def scan(self):
        if self.worker or self.preview is not None or not self.source or not self.output:
            return
        try:
            source, output = (Path(path).resolve(strict=True) for path in (self.source, self.output))
            if not source.is_dir() or not output.is_dir() or source.is_relative_to(output) or output.is_relative_to(source):
                self.status.setText("Vyberte dvě oddělené složky.")
                return
            current = {}
            pending = {}
            for path in source.rglob("*"):
                if path.name.startswith("~$") or path.is_symlink() or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                key = hashlib.sha256(os.fsencode(str(output) + "\0" + str(path))).hexdigest()
                signature = f"{stat.st_size}:{stat.st_mtime_ns}"
                current[key] = signature
                if self.observed.get(key) == signature and self.handled.get(key) != signature:
                    pending[path] = (key, signature)
            self.observed = current
        except OSError:
            self.status.setText("Některá složka je nedostupná. Sledování se obnoví po jejím připojení.")
            return
        if not pending:
            self.refresh_paths()
            return
        self.pending = pending
        self.forget_catalog()
        for button in self.buttons:
            button.setEnabled(False)
        self.status.setText(f"Zpracovávám soubory: {len(pending)}")
        self.worker = BatchWorker(str(source), str(output), self.tessdata, self, list(pending),
                                   copies=self.options["copies"].isChecked(),
                                   split=self.options["split"].isChecked())
        self.worker.progress.connect(self.show_progress)
        self.worker.result.connect(self.log.appendPlainText)
        self.worker.handled.connect(self.mark_handled)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def mark_handled(self, source):
        key, signature = self.pending[Path(source)]
        self.handled[key] = signature
        self.settings.setValue("handled", self.handled)
        self.settings.sync()

    def finished(self):
        for button in self.buttons:
            button.setEnabled(True)
        if self.worker.complete and self.worker.catalog.documents:
            self.catalog = self.worker.catalog
            self.preview_button.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        self.refresh_paths()

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("Probíhá zpracování. Okno lze zavřít po dokončení dávky.")
            event.ignore()
        else:
            if self.preview is not None:
                self.preview.close()
            super().closeEvent(event)



def main():
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    if "--smoke-test" in sys.argv:
        window.timer.stop()
        if getattr(sys, "frozen", False):
            smoke_test_package(window.tessdata)
        QTimer.singleShot(0, app.quit)
    return app.exec()


def smoke_test_package(tessdata):
    import pymupdf

    with tempfile.TemporaryDirectory() as scratch, pymupdf.open() as text, pymupdf.open() as scan:
        source = Path(scratch) / "input"
        target = Path(scratch) / "output"
        source.mkdir()
        target.mkdir()
        page = text.new_page()
        page.insert_text((72, 90), "osoba@example.test", fontsize=22)
        sheet = scan.new_page()
        sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=240))
        scan.save(source / "test.pdf")
        _, result, error = next(process_folders(source, target, tessdata))
        if result is None:
            raise RuntimeError(error)
        with pymupdf.open(result) as output:
            image = pymupdf.Pixmap(output, output[0].get_images()[0][0])
            if image.color_count(colors=True).get(bytes(3), 0) < 10_000:
                raise RuntimeError("Packaged OCR failed to redact the synthetic sample.")
