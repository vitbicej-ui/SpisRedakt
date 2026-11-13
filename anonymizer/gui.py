from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QLabel, QListWidget, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .batch import process_batch


class BatchWorker(QThread):
    result = Signal(int, str)
    failed = Signal(str)

    def __init__(self, sources, output, aliases, tessdata, force_ocr, parent):
        super().__init__(parent)
        self.arguments = sources, output, aliases, tessdata, force_ocr

    def run(self):
        try:
            for index, (_, target, reason) in enumerate(process_batch(*self.arguments), 1):
                message = f"Ke kontrole: {target.parent.name}/{target.name}" if target else f"Chyba: {reason}"
                self.result.emit(index, message)
        except ValueError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(type(error).__name__)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpisRedakt")
        self.resize(690, 620)
        self.sources = []
        self.worker = None
        base = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        bundled = base / "tessdata"
        self.tessdata = bundled if (bundled / "ces.traineddata").is_file() else None
        layout = QVBoxLayout(self)
        self.select = QPushButton("Vybrat dokumenty DOCX a PDF…")
        self.select.clicked.connect(self.choose_sources)
        layout.addWidget(self.select)
        self.files = QListWidget()
        layout.addWidget(self.files)
        layout.addWidget(QLabel("Vlastní výrazy, jeden údaj na řádek. Tvary téže osoby oddělte znakem |."))
        self.aliases = QPlainTextEdit()
        self.aliases.setPlaceholderText("Např. Testovací Osoba | Testovací Osoby")
        self.aliases.setMaximumHeight(100)
        layout.addWidget(self.aliases)
        self.ocr = QPushButton("Vybrat složku s ces.traineddata…")
        self.ocr.clicked.connect(self.choose_ocr)
        layout.addWidget(self.ocr)
        self.ocr_status = QLabel("Čeština pro OCR je k dispozici." if self.tessdata else "Čeština pro OCR zatím není nastavena.")
        layout.addWidget(self.ocr_status)
        self.force_ocr = QCheckBox("Spustit OCR na všech stránkách PDF")
        layout.addWidget(self.force_ocr)
        self.start = QPushButton("Vybrat výstupní složku a zpracovat…")
        self.start.clicked.connect(self.run_batch)
        layout.addWidget(self.start)
        self.status = QLabel("Výstupy před sdílením porovnejte s originály. Dávka může některé údaje přehlédnout.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.log)
        self.controls = [self.select, self.aliases, self.ocr, self.force_ocr, self.start]
        self.start.setEnabled(False)

    def choose_sources(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Dokumenty spisu", "", "Dokumenty (*.docx *.pdf)")
        if paths:
            self.sources = sorted({Path(path).resolve() for path in paths})
            self.files.clear()
            self.files.addItems([f"{index}. {path}" for index, path in enumerate(self.sources, 1)])
            self.start.setEnabled(True)

    def choose_ocr(self):
        folder = QFileDialog.getExistingDirectory(self, "Složka s ces.traineddata")
        if folder:
            if not (Path(folder) / "ces.traineddata").is_file():
                QMessageBox.warning(self, "Chybí jazyková data", "Ve složce chybí soubor ces.traineddata.")
                return
            self.tessdata = Path(folder)
            self.ocr_status.setText("Čeština pro OCR je k dispozici.")

    def run_batch(self):
        output = QFileDialog.getExistingDirectory(self, "Výstupní složka")
        if not output:
            return
        self.log.clear()
        for control in self.controls:
            control.setEnabled(False)
        self.status.setText(f"Zpracovávám dávku. Výstupní složka: {output}")
        self.worker = BatchWorker(self.sources, output, self.aliases.toPlainText().splitlines(),
                                  self.tessdata, self.force_ocr.isChecked(), self)
        self.worker.result.connect(lambda index, message: self.log.appendPlainText(f"{index}. {message}"))
        self.worker.failed.connect(self.log.appendPlainText)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        for control in self.controls:
            control.setEnabled(True)
        self.log.appendPlainText("Běh ukončen. Zkontrolujte stav všech souborů a celý obsah výstupů.")
        self.worker.deleteLater()
        self.worker = None

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = Window()
    if "--smoke-test" in sys.argv:
        assert window.windowTitle() == "SpisRedakt"
        if getattr(sys, "frozen", False):
            smoke_test_package(window.tessdata)
        return 0
    window.show()
    return app.exec()


def smoke_test_package(tessdata):
    import pymupdf

    if tessdata is None:
        raise RuntimeError("Bundled OCR data is missing.")
    with tempfile.TemporaryDirectory() as scratch, pymupdf.open() as text, pymupdf.open() as scan:
        page = text.new_page()
        page.insert_text((72, 90), "osoba@example.test", fontsize=22)
        sheet = scan.new_page()
        sheet.insert_image(sheet.rect, pixmap=page.get_pixmap(dpi=240))
        source = Path(scratch) / "test.pdf"
        scan.save(source)
        _, result, error = next(process_batch([source], scratch, tessdata=tessdata))
        if result is None:
            raise RuntimeError(error)
        with pymupdf.open(result) as output:
            image = pymupdf.Pixmap(output, output[0].get_images()[0][0])
            if image.color_count(colors=True).get(bytes(3), 0) < 10_000:
                raise RuntimeError("Packaged OCR failed to redact the synthetic sample.")
