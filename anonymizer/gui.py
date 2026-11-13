from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .batch import process_folders


class BatchWorker(QThread):
    result = Signal(str)

    def __init__(self, source, output, tessdata, parent):
        super().__init__(parent)
        self.arguments = source, output, tessdata

    def run(self):
        try:
            for source, target, error in process_folders(*self.arguments):
                source_root, output_root, _ = self.arguments
                message = str(target.relative_to(Path(output_root).resolve())) if target else f"Chyba: {error}"
                self.result.emit(f"{source.relative_to(Path(source_root).resolve())} → {message}")
        except ValueError as error:
            self.result.emit(str(error))
        except Exception as error:
            self.result.emit(type(error).__name__)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpisRedakt")
        self.resize(660, 470)
        self.worker = None
        self.source = None
        base = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        self.tessdata = base / "tessdata"
        layout = QVBoxLayout(self)
        self.input_button = QPushButton("1. Vybrat vstupní složku…")
        self.input_button.clicked.connect(self.choose_source)
        layout.addWidget(self.input_button)
        self.source_label = QLabel("Vyberte složku s původními DOCX, XLSX, PPTX a PDF.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.output_button = QPushButton("2. Vybrat výstupní složku a zpracovat…")
        self.output_button.clicked.connect(self.run_batch)
        self.output_button.setEnabled(False)
        layout.addWidget(self.output_button)
        self.status = QLabel("Výstupy před sdílením porovnejte s originály.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.log)

    def choose_source(self):
        source = QFileDialog.getExistingDirectory(self, "Vstupní složka")
        if source:
            self.source = source
            self.source_label.setText(source)
            self.output_button.setEnabled(True)

    def run_batch(self):
        output = QFileDialog.getExistingDirectory(self, "Výstupní složka")
        if not output:
            return
        self.log.clear()
        self.input_button.setEnabled(False)
        self.output_button.setEnabled(False)
        self.status.setText(f"Zpracovávám dávku do {output}")
        self.worker = BatchWorker(self.source, output, self.tessdata, self)
        self.worker.result.connect(self.log.appendPlainText)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.input_button.setEnabled(True)
        self.output_button.setEnabled(True)
        self.status.setText("Zpracování skončilo. Zkontrolujte stav souborů a obsah výstupů.")
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
    window.show()
    if "--smoke-test" in sys.argv:
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
