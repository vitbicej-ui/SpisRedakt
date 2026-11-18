"""Simulation of the Windows text layer of a PDF (font Arial) for tests that run anywhere.

With Arial the PDF text layer reports U+00A0 where a space is drawn and U+00AD where a hyphen is drawn
(the font maps both to one glyph). Linux fonts (DejaVu) do not, so local tests missed leaks of values such
as "Jan\\xa0Novák" or "850314\\xad2010". ``windows_text_layer()`` makes ``pymupdf.Page.get_text`` return
exactly that, for every output shape (string, list, tuple, dict and "rawdict").

Synthetic data only; nothing here reads a real document.
"""
import contextlib

import pymupdf

SPACE = " "
HYPHEN = "­"
# Shapes whose strings are markup or JSON: hyphens and spaces there are syntax, not text.
MARKUP = {"html", "xhtml", "xml", "json", "rawjson"}
# Keys of the dict shapes ("dict", "rawdict", "json" parsed) that hold drawn text.
TEXT_KEYS = {"c", "text"}


def windows_text(text):
    """The text as the Arial text layer reports it."""
    return text.replace(" ", SPACE).replace("-", HYPHEN)


def convert(value, key=None):
    """Convert the text inside any ``Page.get_text`` result (str, list, tuple, dict) recursively."""
    if isinstance(value, str):
        # Strings inside a dict are text only under "c" and "text" (not font names or other labels);
        # strings in lists and tuples (blocks, words) are text, except in a dict under another key.
        return windows_text(value) if key is None or key in TEXT_KEYS else value
    if isinstance(value, dict):
        return {name: convert(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [convert(item, key) for item in value]
    if isinstance(value, tuple):
        return tuple(convert(item, key) for item in value)
    return value


@contextlib.contextmanager
def windows_text_layer():
    """Temporarily make ``Page.get_text`` return U+00A0 for spaces and U+00AD for hyphens."""
    original = pymupdf.Page.get_text

    def get_text(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        option = args[0] if args else kwargs.get("option", "text")
        return result if option in MARKUP else convert(result)

    pymupdf.Page.get_text = get_text
    try:
        yield
    finally:
        pymupdf.Page.get_text = original
