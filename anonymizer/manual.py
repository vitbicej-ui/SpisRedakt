"""Manual redaction areas drawn in the preview: memory only, valid for one processing.

An area is a rectangle on a displayed page (after rotation and crop box; for an image the decoded image),
stored normalized to 0..1, so the same area fits the rendering of the preview, of the Markdown extraction and
of the control copy. Areas only add blackout. Nothing here touches the disk, settings, audit or log; no text
of this module carries a name, path or content of a document.
"""

MANUAL_MISMATCH = "Ruční plocha neodpovídá zpracovávanému souboru."
# Formats displayed as pages: the only ones a manual area may refer to.
PAGE_SUFFIXES = frozenset({".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"})
MIN_PIXELS = 2
# Relative difference of the aspect ratio of one page between the Markdown and the control copy.
ASPECT_TOLERANCE = 0.005
FIXED_NOTE = "Ruční plocha platí jen pro toto místo, stejný údaj jinde ve spisu neodstraní."
REPEAT_NOTE = "Ruční plochy platí jen pro toto zpracování. Starší výstup bez nich zůstal ve výstupní složce, nesdílejte ho."
DISCARD_NOTE = "Ruční plochy byly zahozeny."


def key_of(chain):
    return "/".join(str(part) for part in chain)


def normalize(x0, y0, x1, y1, width, height):
    """Rectangle in pixels of a displayed page to 0..1 (clipped to the page), or None when under 2x2 pixels."""
    if width <= 0 or height <= 0:
        return None
    left, right = sorted((min(max(x0, 0), width), min(max(x1, 0), width)))
    top, bottom = sorted((min(max(y0, 0), height), min(max(y1, 0), height)))
    if right - left < MIN_PIXELS or bottom - top < MIN_PIXELS:
        return None
    return left / width, top / height, right / width, bottom / height


class Areas:
    """The areas of the user: ``{(input_id, chain): (signature, {page: [rectangle]})}`` in the order drawn."""

    def __init__(self):
        self.items = {}
        self.paths = {}     # input_id -> path of the input (memory only, never shown or stored)
        self.order = []

    def __len__(self):
        return len(self.order)

    def add(self, locator, signature, path, page, rectangle):
        if self.paths.setdefault(locator[0], path) != path:
            raise ValueError(MANUAL_MISMATCH)
        key = (locator[0], locator[1])
        entry = self.items.setdefault(key, (signature, {}))
        if entry[0] != signature:
            raise ValueError(MANUAL_MISMATCH)
        entry[1].setdefault(page, []).append(tuple(rectangle))
        self.order.append((key, page))

    def remove_last(self):
        if not self.order:
            return False
        key, page = self.order.pop()
        pages = self.items[key][1]
        pages[page].pop()
        if not pages[page]:
            del pages[page]
        if not pages:
            del self.items[key]
        if not any(item[0] == key[0] for item in self.items):
            self.paths.pop(key[0], None)
        return True

    def clear(self):
        self.items.clear()
        self.paths.clear()
        self.order.clear()

    def rectangles(self, locator, page):
        entry = self.items.get((locator[0], locator[1]))
        return list(entry[1].get(page, ())) if entry else []

    def views(self):
        """``{input_id: InputAreas}`` copied for one processing (later drawing does not change it)."""
        result = {}
        for (input_id, chain), (signature, pages) in self.items.items():
            view = result.setdefault(input_id, InputAreas(signature, self.paths[input_id]))
            if view.signature != signature:
                raise ValueError(MANUAL_MISMATCH)
            view.items[chain] = {page: tuple(rects) for page, rects in pages.items()}
        for view in result.values():
            view.count = sum(len(rects) for pages in view.items.values() for rects in pages.values())
        return result


class InputAreas:
    """The areas of one input for one processing, with what the phases learned about its pages."""

    def __init__(self, signature, path=None):
        self.signature, self.path = signature, path
        self.items = {}         # chain key -> {page: rectangles}
        self.count = 0
        self.used = set()
        self.dimensions = {}    # (chain key, page) -> (width, height) seen by the Markdown extraction
        self.phase = "extract"
        self.refused = False

    def begin(self, phase):
        self.phase = phase
        self.used = set()

    def item(self, chain):
        key = key_of(chain)
        self.used.add(key)
        return ItemAreas(self, key, tuple(chain), self.items.get(key, {}))

    def unused(self):
        """Keys of areas whose item was never reached: the area has nothing to apply to."""
        return set(self.items) - self.used


class ItemAreas:
    """The areas of one item (input or member of an archive), by page number from 1."""

    def __init__(self, owner, key, chain, pages):
        self.owner, self.key, self.chain, self.pages = owner, key, chain, pages

    def __bool__(self):
        return bool(self.pages)

    def child(self, order):
        return self.owner.item((*self.chain, order))

    def rects(self, page):
        return self.pages.get(page, ())

    def validate(self, count):
        """Refuse an area on a page the document does not have."""
        if any(not 1 <= page <= count for page in self.pages):
            raise ValueError(MANUAL_MISMATCH)

    def dimension(self, page, width, height):
        """The Markdown extraction records the size of a page with areas; the copy must see the same shape."""
        if width <= 0 or height <= 0:
            raise ValueError(MANUAL_MISMATCH)
        slot = (self.key, page)
        if self.owner.phase == "extract":
            self.owner.dimensions[slot] = (width, height)
            return
        seen = self.owner.dimensions.get(slot)
        if seen is None or abs(seen[0] / seen[1] - width / height) > ASPECT_TOLERANCE * width / height:
            raise ValueError(MANUAL_MISMATCH)
