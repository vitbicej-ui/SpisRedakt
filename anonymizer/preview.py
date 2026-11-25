"""In-memory preview: the released Markdown section of a document next to the rendered original.

Nothing here writes to the disk (no temporary files, cache, settings or log): inputs, ZIP members and
copies are read into memory, rendered one page at a time and thrown away when the window closes.
Every refusal is a fixed text of ``reasons.PREVIEW_MESSAGES``; no text of this module carries a name,
path or content of a document.
"""
import io
import re
from pathlib import Path
from zipfile import ZipFile

import pymupdf as pdf
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout,
)

from .batch import input_signature
from .documents import MAX_PIXELS
from .extract import MAX_ZIP_DEPTH, _Limited, _pixmap
from .formats import ARCHIVE_BYTES, SCANS, identify, size_limit
from .manual import Areas, FIXED_NOTE, normalize
from .reasons import ADVICE, GENERAL_ADVICE, PREVIEW_MESSAGES

ORIGINAL_TITLE = "Originál – obsahuje osobní údaje"
OUTPUT_TITLE = "Výstup"
COPY_TITLE = "Kontrolní kopie"
PAGE_FORMATS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", *SCANS}
ZOOMS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
BASE_DPI = 100
DISPLAY_PIXELS = 12_000_000
LABEL = re.compile(r"\[[A-Z]{1,3}\d+\]")
SECTION_END = re.compile(r"## (?:D\d+ \(|Nezpracované položky)")


class PreviewError(Exception):
    """A refusal with a code of ``PREVIEW_MESSAGES``."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def message(code, extra=None):
    text = PREVIEW_MESSAGES[code]
    return f"{text} {extra}" if extra else text


class Catalog:
    """What a finished batch tells the preview (memory only); filled through ``update``."""

    def __init__(self):
        self.documents, self.inputs, self.copies = {}, {}, {}
        self.markdown, self.split = None, False

    def update(self, event):
        if "documents" in event:
            self.documents.update(event["documents"])
            self.inputs.update(event["inputs"])
        if "markdown" in event:
            self.markdown, self.split = event["markdown"], event["split"]
        if "copy" in event:
            self.copies[event["copy"][0]] = event["copy"][1]


# -- reading the original and the copy ---------------------------------------------------------------

def _read(stream):
    return _Limited(stream, ARCHIVE_BYTES).read()


def _chain(locator):
    return [int(part) for part in locator[1].split("/")] if locator[1] else []


def original_bytes(source, locator):
    """Bytes and suffix hint of the input or of the ZIP member the locator points at (memory only)."""
    source = Path(source)
    orders = _chain(locator)
    if not orders:
        with source.open("rb") as stream:
            return _read(stream), source.suffix.lower()
    if len(orders) > MAX_ZIP_DEPTH:
        raise PreviewError("preview_failed")
    container, data, hint = source, None, ""
    for position, order in enumerate(orders):
        with ZipFile(container) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir()]
            if not 1 <= order <= len(members):
                raise PreviewError("preview_failed")
            entry = members[order - 1]
            if entry.flag_bits & 1 or entry.file_size > ARCHIVE_BYTES:
                raise PreviewError("preview_failed")
            with archive.open(entry) as stream:
                data = _read(stream)
            hint = Path(entry.filename).suffix.lower()
        container = io.BytesIO(data)
    return data, hint


def copy_bytes(target, locator):
    """Bytes and suffix of the released copy of an item (``polozka-0002.pdf`` in a copy of a ZIP)."""
    target = Path(target)
    orders = _chain(locator)
    if not orders:
        with target.open("rb") as stream:
            return _read(stream), target.suffix.lower()
    container, data, suffix = target, None, ""
    for order in orders:
        with ZipFile(container) as archive:
            prefix = f"polozka-{order:04d}."
            name = next((name for name in archive.namelist() if name.startswith(prefix)), None)
            if name is None:
                raise PreviewError("preview_no_copy")
            info = archive.getinfo(name)
            if info.file_size > ARCHIVE_BYTES:
                raise PreviewError("preview_failed")
            with archive.open(info) as stream:
                data = _read(stream)
            suffix = Path(name).suffix.lower()
        container = io.BytesIO(data)
    return data, suffix


# -- rendering ---------------------------------------------------------------------------------------------

def _qimage(pix):
    if pix.n != 3 or pix.alpha:
        raise PreviewError("preview_failed")
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


def _fit(area, wanted):
    """Display scale limited to DISPLAY_PIXELS; the hard MAX_PIXELS limit is the one of the processing."""
    return min(wanted, (DISPLAY_PIXELS / max(area, 1)) ** 0.5)


def _render_page(page, zoom):
    rect = page.rect
    area = max(rect.width * rect.height, 1)
    if 72 * (MAX_PIXELS / area) ** 0.5 < 50:
        raise PreviewError("preview_page_too_large")
    scale = _fit(area, BASE_DPI / 72 * zoom)
    pix = page.get_pixmap(matrix=pdf.Matrix(scale, scale), annots=False, alpha=False)
    return _qimage(pix)


def render(data, suffix, number, zoom):
    """Render page ``number`` (from 1) of image bytes; returns ``(QImage, page_count)``."""
    kind = identify(data, suffix)
    if kind not in PAGE_FORMATS:
        raise PreviewError("preview_not_page")
    if len(data) > size_limit(kind):
        raise PreviewError("preview_failed")
    if kind == ".pdf" or kind in SCANS:
        try:
            document = pdf.open(stream=data, filetype="pdf" if kind == ".pdf" else "tiff")
        except Exception:
            raise PreviewError("preview_encrypted" if kind == ".pdf" else "preview_failed") from None
        with document:
            if document.needs_pass:
                raise PreviewError("preview_encrypted")
            count = document.page_count
            if not count or count > 500:
                raise PreviewError("preview_encrypted" if kind == ".pdf" else "preview_failed")
            if not 1 <= number <= count:
                raise PreviewError("preview_no_page")
            return _render_page(document.load_page(number - 1), zoom), count
    if number != 1:
        raise PreviewError("preview_no_page")
    pix = _pixmap(data)
    if pix is None:
        raise PreviewError("preview_failed")
    if pix.width * pix.height > MAX_PIXELS:
        raise PreviewError("preview_page_too_large")
    image = _qimage(pix)
    scale = _fit(pix.width * pix.height, zoom)
    if scale != 1:
        image = image.scaled(max(1, round(image.width() * scale)), max(1, round(image.height() * scale)),
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return image, 1


# -- the Markdown section ------------------------------------------------------------------------------------

def markdown_section(catalog, index):
    path = catalog.markdown
    if path is None:
        raise PreviewError("preview_markdown_missing")
    path = Path(path)
    try:
        if catalog.split:
            return (path / f"D{index:04d}.md").read_text(encoding="utf-8")
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        raise PreviewError("preview_markdown_missing") from None
    start = next((n for n, line in enumerate(lines) if line.startswith(f"## D{index} (")), None)
    if start is None:
        raise PreviewError("preview_section_missing")
    end = next((n for n in range(start + 1, len(lines)) if SECTION_END.match(lines[n])), len(lines))
    return "\n".join(lines[start:end]).strip()


class Model:
    """The preview without widgets: what to show for an item, page and zoom. Holds only the current item."""

    def __init__(self, catalog):
        self.catalog = catalog
        self.cache = {}

    def clear(self):
        self.cache.clear()

    def label(self, index):
        facts = self.catalog.documents[index]
        if facts["code"] is not None:
            return f"D{index} – nezpracováno"
        parts = [facts["format"]] + ([f"{facts['pages']} s."] if facts["pages"] else [])
        if facts["parent"] is not None:
            parts.append(f"příloha D{facts['parent']}")
        return f"D{index} ({', '.join(parts)})"

    def _cached(self, key, load):
        if key not in self.cache:
            self.cache[key] = load()
        return self.cache[key]

    def original(self, index, number, zoom):
        facts = self.catalog.documents[index]
        if facts["code"] is not None:
            raise PreviewError("preview_unprocessed")
        locator = facts["locator"]
        if locator is None:
            raise PreviewError("preview_no_original")
        if facts["format"] not in {"PDF", "TIFF", "PNG", "JPEG", "BMP"}:
            raise PreviewError("preview_not_page")
        source, signature = self.catalog.inputs.get(locator[0], (None, None))
        if source is None or signature is None or input_signature(source) != signature:
            raise PreviewError("preview_input_changed")
        data, hint = self._cached(("original", index), lambda: original_bytes(source, locator))
        return render(data, hint, number, zoom)

    def area_target(self, index):
        """``(locator, signature of the input)`` an area on this item refers to; refuses what is not a page."""
        facts = self.catalog.documents[index]
        if facts["code"] is not None:
            raise PreviewError("preview_unprocessed")
        locator = facts["locator"]
        if locator is None:
            raise PreviewError("preview_no_original")
        if facts["format"] not in {"PDF", "TIFF", "PNG", "JPEG", "BMP"}:
            raise PreviewError("preview_not_page")
        signature = self.catalog.inputs.get(locator[0], (None, None))[1]
        if signature is None:
            raise PreviewError("preview_input_changed")
        return locator, signature, self.catalog.inputs[locator[0]][0]

    def _copy(self, index):
        facts = self.catalog.documents[index]
        locator = facts["locator"]
        if facts["code"] is not None or locator is None or locator[0] not in self.catalog.copies:
            raise PreviewError("preview_no_copy")
        return self._cached(("copy", index), lambda: copy_bytes(self.catalog.copies[locator[0]], locator))

    def has_copy(self, index):
        """True when the released control copy of the item exists and can be shown as pages."""
        try:
            return self._copy(index)[1] in PAGE_FORMATS
        except Exception:
            return False

    def copy(self, index, number, zoom):
        data, suffix = self._copy(index)
        return render(data, suffix, number, zoom)

    def text(self, index):
        facts = self.catalog.documents[index]
        if facts["code"] is not None:
            return message("preview_unprocessed", ADVICE.get(facts["code"], GENERAL_ADVICE))
        return markdown_section(self.catalog, index)


# -- the window ----------------------------------------------------------------------------------------------------

class Highlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.format = QTextCharFormat()
        self.format.setBackground(QColor("#ffe27a"))

    def highlightBlock(self, text):
        for found in LABEL.finditer(text):
            self.setFormat(found.start(), found.end() - found.start(), self.format)


def safe_text(error):
    """Fixed text of a failure; an unexpected exception shows nothing but the generic text."""
    return message(error.code) if isinstance(error, PreviewError) else message("preview_failed")


class AreaLabel(QLabel):
    """The original page; in drawing mode a drag adds a rectangle (kept by the dialog, drawn black with a border)."""

    def __init__(self, drawn, shown):
        super().__init__()
        self.drawn, self.shown = drawn, shown
        self.drawing = False
        self.start = self.current = None

    def mousePressEvent(self, event):
        if self.drawing and event.button() == Qt.MouseButton.LeftButton and not self.pixmap().isNull():
            self.start = self.current = event.position().toPoint()
            self.update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.current = event.position().toPoint()
            self.update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            start, end = self.start, event.position().toPoint()
            self.start = self.current = None
            self.update()
            self.drawn(start.x(), start.y(), end.x(), end.y(), self.pixmap().width(), self.pixmap().height())
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#ffcc00"), 2))
        painter.setBrush(QColor("#000000"))
        width, height = pixmap.width(), pixmap.height()
        for x0, y0, x1, y1 in self.shown():
            painter.drawRect(QRect(QPoint(round(x0 * width), round(y0 * height)),
                                   QPoint(round(x1 * width), round(y1 * height))))
        if self.start is not None and self.current is not None:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ff3030"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRect(self.start, self.current).normalized())
        painter.end()


class PreviewDialog(QDialog):
    """Modal window: list of items, original page, output section or control copy.

    It changes no file; with "Doplnit začernění" it only collects ``Areas`` (memory) that the window
    hands to the next processing."""

    def __init__(self, catalog, parent=None, areas=None):
        super().__init__(parent)
        self.areas = areas if areas is not None else Areas()
        self.apply_requested = False
        self.target = None
        self.setWindowTitle("SpisRedakt – náhled")
        self.resize(1280, 780)
        self.model = Model(catalog)
        self.zoom = ZOOMS.index(1.0)
        self.index = None
        self.pages = 1

        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        self.items = QListWidget()
        self.items.setMaximumWidth(210)
        for index in sorted(catalog.documents):
            entry = QListWidgetItem(self.model.label(index))
            entry.setData(Qt.ItemDataRole.UserRole, index)
            self.items.addItem(entry)
        body.addWidget(self.items)

        middle = QVBoxLayout()
        self.original_title = QLabel(ORIGINAL_TITLE)
        self.original_title.setTextFormat(Qt.TextFormat.PlainText)
        self.original_image = AreaLabel(self.add_area, self.page_areas)
        self.original_image.setTextFormat(Qt.TextFormat.PlainText)
        self.original_image.setWordWrap(True)
        self.original_image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        middle_scroll = QScrollArea()
        middle_scroll.setWidget(self.original_image)
        middle_scroll.setWidgetResizable(True)
        middle.addWidget(self.original_title)
        middle.addWidget(middle_scroll, 1)
        body.addLayout(middle, 1)

        right = QVBoxLayout()
        head = QHBoxLayout()
        self.output_title = QLabel(OUTPUT_TITLE)
        self.copy_toggle = QPushButton(COPY_TITLE)
        self.copy_toggle.setCheckable(True)
        head.addWidget(self.output_title, 1)
        head.addWidget(self.copy_toggle)
        right.addLayout(head)
        self.stack = QStackedWidget()
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setUndoRedoEnabled(False)
        self.highlighter = Highlighter(self.text.document())
        self.copy_image = QLabel()
        self.copy_image.setTextFormat(Qt.TextFormat.PlainText)
        self.copy_image.setWordWrap(True)
        self.copy_image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        right_scroll = QScrollArea()
        right_scroll.setWidget(self.copy_image)
        right_scroll.setWidgetResizable(True)
        self.stack.addWidget(self.text)
        self.stack.addWidget(right_scroll)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)

        controls = QHBoxLayout()
        self.previous = QPushButton("Předchozí strana")
        self.following = QPushButton("Další strana")
        self.number = QSpinBox()
        self.number.setMinimum(1)
        self.count = QLabel("/ 1")
        self.smaller = QPushButton("Zmenšit")
        self.larger = QPushButton("Přiblížit")
        for widget in (self.previous, self.number, self.count, self.following, self.smaller, self.larger):
            controls.addWidget(widget)
        controls.addStretch(1)
        outer.addLayout(controls)

        manual = QHBoxLayout()
        self.draw_toggle = QPushButton("Doplnit začernění")
        self.draw_toggle.setCheckable(True)
        self.undo = QPushButton("Smazat poslední")
        self.reset = QPushButton("Zrušit vše")
        self.use = QPushButton("Použít")
        self.note = QLabel(FIXED_NOTE)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        for widget in (self.draw_toggle, self.undo, self.reset, self.use):
            manual.addWidget(widget)
        manual.addWidget(self.note, 1)
        outer.addLayout(manual)
        self.draw_toggle.toggled.connect(self.set_drawing)
        self.undo.clicked.connect(self.remove_last)
        self.reset.clicked.connect(self.clear_areas)
        self.use.clicked.connect(self.apply)
        self.refresh_buttons()

        self.items.currentRowChanged.connect(self.select)
        self.previous.clicked.connect(lambda: self.number.setValue(self.number.value() - 1))
        self.following.clicked.connect(lambda: self.number.setValue(self.number.value() + 1))
        self.number.valueChanged.connect(self.show_page)
        self.smaller.clicked.connect(lambda: self.rezoom(-1))
        self.larger.clicked.connect(lambda: self.rezoom(1))
        self.copy_toggle.toggled.connect(lambda checked: self.show_page())
        if catalog.documents:
            self.items.setCurrentRow(0)

    def select(self, row):
        self.model.clear()
        self.index = self.items.item(row).data(Qt.ItemDataRole.UserRole) if row >= 0 else None
        self.copy_toggle.blockSignals(True)
        self.copy_toggle.setChecked(False)
        self.copy_toggle.blockSignals(False)
        self.copy_toggle.setEnabled(self.index is not None and self.model.has_copy(self.index))
        self.target = None
        if self.index is not None:
            try:
                self.target = self.model.area_target(self.index)
            except Exception:
                pass
        self.refresh_buttons()
        self.number.blockSignals(True)
        self.number.setValue(1)
        self.number.blockSignals(False)
        self.pages = 1
        self.number.setMaximum(1)
        self.count.setText("/ 1")
        self.load_text()
        self.show_page()

    # -- manual areas -------------------------------------------------------------------------------------------

    def set_drawing(self, checked):
        self.original_image.drawing = bool(checked) and self.target is not None
        self.original_image.setCursor(Qt.CursorShape.CrossCursor if self.original_image.drawing
                                      else Qt.CursorShape.ArrowCursor)

    def refresh_buttons(self):
        self.draw_toggle.setEnabled(self.target is not None)
        if self.target is None and self.draw_toggle.isChecked():
            self.draw_toggle.setChecked(False)
        self.set_drawing(self.draw_toggle.isChecked())
        self.undo.setEnabled(len(self.areas) > 0)
        self.reset.setEnabled(len(self.areas) > 0)
        self.use.setEnabled(len(self.areas) > 0)
        self.note.setVisible(True)
        self.original_image.update()

    def page_areas(self):
        if self.target is None:
            return []
        return self.areas.rectangles(self.target[0], self.number.value())

    def add_area(self, x0, y0, x1, y1, width, height):
        """A drag on the original, in pixels of the shown page; stored normalized, areas under 2x2 pixels dropped."""
        if self.target is None:
            return
        rectangle = normalize(x0, y0, x1, y1, width, height)
        if rectangle is not None:
            self.areas.add(self.target[0], self.target[1], self.target[2], self.number.value(), rectangle)
        self.refresh_buttons()

    def remove_last(self):
        self.areas.remove_last()
        self.refresh_buttons()

    def clear_areas(self):
        self.areas.clear()
        self.refresh_buttons()

    def apply(self):
        """Close the window and ask for the same batch to be processed again with the areas."""
        if len(self.areas):
            self.apply_requested = True
            self.accept()

    def rezoom(self, step):
        self.zoom = min(max(self.zoom + step, 0), len(ZOOMS) - 1)
        self.show_page()

    def show_page(self, *_):
        if self.index is None:
            return
        number = self.number.value()
        zoom = ZOOMS[self.zoom]
        self.show_image(self.original_image, lambda: self.model.original(self.index, number, zoom), True)
        copy = self.copy_toggle.isChecked()
        self.output_title.setText(COPY_TITLE if copy else OUTPUT_TITLE)
        self.stack.setCurrentIndex(1 if copy else 0)
        if copy:
            self.show_image(self.copy_image, lambda: self.model.copy(self.index, number, zoom), False)
        else:
            self.scroll_text(number)
        self.previous.setEnabled(number > 1)
        self.following.setEnabled(number < self.pages)

    def show_image(self, label, make, counts):
        try:
            image, pages = make()
        except Exception as error:
            label.setPixmap(QPixmap())
            label.setText(safe_text(error))
            return
        label.setText("")
        label.setPixmap(QPixmap.fromImage(image))
        if counts and pages != self.pages:
            self.pages = pages
            self.number.blockSignals(True)
            self.number.setMaximum(pages)
            self.number.blockSignals(False)
            self.count.setText(f"/ {pages}")

    def load_text(self):
        try:
            self.text.setPlainText(self.model.text(self.index))
        except Exception as error:
            self.text.setPlainText(safe_text(error))

    def scroll_text(self, number):
        cursor = self.text.document().find(f"[s. {number}]")
        if not cursor.isNull():
            self.text.setTextCursor(cursor)
            self.text.centerCursor()
        else:
            self.text.moveCursor(QTextCursor.MoveOperation.Start)

    def release(self):
        """Throw everything away: bytes, images and texts."""
        self.model.clear()
        self.original_image.setPixmap(QPixmap())
        self.copy_image.setPixmap(QPixmap())
        self.text.clear()

    def done(self, result):
        self.release()
        super().done(result)
