from pathlib import Path
import sys
import tempfile
import os

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .batch import process_folders
from .watch import Watcher


class BatchWorker(QThread):
    result = Signal(str)
    handled = Signal(str, str)

    def __init__(self, source, output, tessdata, parent, selected):
        super().__init__(parent)
        self.arguments = source, output, tessdata
        self.selected = selected

    def run(self):
        try:
            audit_root = os.environ.get("SPISREDAKT_AUDIT_DIR") or application_folder() / "audit"
            def audit_ready(path, fallback):
                prefix = "Náhradní složka auditu" if fallback else "Audit dávky"
                self.result.emit(f"{prefix}: {path}")
            remaining = set(self.selected)
            for source, target, error in process_folders(
                *self.arguments, audit_root=audit_root, audit_ready=audit_ready, selected=self.selected
            ):
                source_root, output_root, _ = self.arguments
                message = str(target.relative_to(Path(output_root).resolve())) if target else f"Chyba: {error}"
                self.result.emit(f"{source.relative_to(Path(source_root).resolve())} → {message}")
                remaining.discard(source)
                self.handled.emit(str(source), "" if target else error)
            for source in sorted(remaining):
                self.result.emit(f"{source.name} → přeskočeno")
                self.handled.emit(str(source), "")
        except ValueError as error:
            self.result.emit(str(error))
        except Exception as error:
            self.result.emit(type(error).__name__)


def application_folder():
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent


def default_tessdata():
    return Path(os.environ.get("SPISREDAKT_TESSDATA") or application_folder() / "tessdata")


def run_once(source, output):
    """Process a folder once without the window: `SpisRedakt --once <input> <output>`.
    Exit code 0 when the batch ran to its end (individual files may still be withheld)."""
    audit_root = os.environ.get("SPISREDAKT_AUDIT_DIR") or application_folder() / "audit"
    try:
        for _ in process_folders(source, output, default_tessdata(), audit_root=audit_root):
            pass
    except ValueError:
        return 2
    return 0


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SpisRedakt")
        self.resize(760, 510)
        self.worker = None
        override = os.environ.get("SPISREDAKT_SETTINGS")      # isolated settings file for tests
        self.settings = (QSettings(override, QSettings.Format.IniFormat) if override
                         else QSettings("SpisRedakt", "SpisRedakt"))
        self.source = self.settings.value("input", "", str)
        self.output = self.settings.value("output", "", str)
        self.watcher = Watcher(self.settings.value("handled", {}) or {})
        self.pending = {}
        self.tessdata = default_tessdata()

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
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        retry = QPushButton("Zpracovat složku znovu")
        retry.clicked.connect(self.retry)
        self.buttons.append(retry)
        layout.addWidget(retry)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.document().setMaximumBlockCount(500)
        layout.addWidget(self.log, 1)
        self.refresh_paths()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self.timer.start(2500)

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
            self.watcher.forget_observations()
            self.refresh_paths()

    def retry(self):
        if self.source and self.output:
            self.watcher.reset()
            self.settings.setValue("handled", self.watcher.handled)
            self.scan()

    def scan(self):
        if self.worker or not self.source or not self.output:
            return
        try:
            source, output = (Path(path).resolve(strict=True) for path in (self.source, self.output))
            pending = self.watcher.scan(source, output)
        except ValueError as error:
            self.status.setText(str(error))
            return
        except OSError:
            self.status.setText("Některá složka je nedostupná. Sledování se obnoví po jejím připojení.")
            return
        if not pending:
            self.refresh_paths()
            return
        self.pending = pending
        for button in self.buttons:
            button.setEnabled(False)
        self.status.setText(f"Zpracovávám soubory: {len(pending)}")
        self.worker = BatchWorker(str(source), str(output), self.tessdata, self, list(pending))
        self.worker.result.connect(self.log.appendPlainText)
        self.worker.handled.connect(self.mark_handled)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def mark_handled(self, source, error):
        key, signature = self.pending[Path(source)]
        if self.watcher.result(key, signature, error):
            self.settings.setValue("handled", self.watcher.handled)
            self.settings.sync()

    def finished(self):
        for button in self.buttons:
            button.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        self.refresh_paths()

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("Probíhá zpracování. Okno lze zavřít po dokončení dávky.")
            event.ignore()
        else:
            super().closeEvent(event)



def main():
    app = QApplication(sys.argv)
    if "--once" in sys.argv:
        position = sys.argv.index("--once")
        if len(sys.argv) < position + 3:
            return 2
        return run_once(sys.argv[position + 1], sys.argv[position + 2])
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
