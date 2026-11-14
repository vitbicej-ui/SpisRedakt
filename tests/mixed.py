"""A mixed synthetic batch and its verification, usable from tests and against the built app.

    python tests/mixed.py build  <input folder>
    python tests/mixed.py verify <input folder> <output folder> [tessdata]
"""
import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile

import pymupdf as pdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import corpus                                            # noqa: E402
from anonymizer.czech import fold                        # noqa: E402

SCALE = pdf.Matrix(300 / 72, 300 / 72)
NEUTRAL = {"muž", "Slovenská republika", "<QR>"}
MAIL = "kratochvil@example.test"
SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main"
TEXTS = {
    "texty/dopis.txt": "Vážený pane, pan Bohumil Kratochvíl, nar. 29. 2. 1984, dluží 125 000 Kč. Kontakt: " + MAIL,
    "texty/tabulka.csv": "Jméno;Kontakt;Částka\nBohumil Kratochvíl;" + MAIL + ";125 000 Kč\n",
    "texty/data.json": json.dumps({"osoba": "Bohumil Kratochvíl", "email": MAIL, "castka": "125 000 Kč"}, ensure_ascii=False),
    "texty/data.xml": '<spis><osoba email="' + MAIL + '">Bohumil Kratochvíl</osoba><castka>125 000 Kč</castka></spis>',
}
FRAGMENTS = ["Kratochv", "Bohumil", "kratochvil@", "1984"]


def squeeze(text):
    return re.sub(r"\s+", "", fold(text))


def crop(pixmap, area):
    part = pdf.Pixmap(pixmap.colorspace, area, False)
    part.copy(pixmap, area)
    return part.samples


def check_pdf(source, target, truth, labels_kept=True, tessdata=None):
    """Problems found in a redacted PDF: secret areas not black, kept areas damaged, text read by OCR."""
    problems = []
    with pdf.open(source) as original, pdf.open(target) as result:
        if len(original) != len(result):
            return ["jiný počet stran"]
        for number, page in enumerate(result):
            if page.get_text().strip() or len(page.get_images()) != 1:
                problems.append(f"strana {number + 1}: neočekávaná vrstva")
                continue
            image = pdf.Pixmap(result, page.get_images()[0][0])
            reference = original[number].get_pixmap(matrix=SCALE, annots=False, alpha=False)
            if (image.width, image.height) != (reference.width, reference.height):
                problems.append(f"strana {number + 1}: jiný rozměr")
                continue
            for where, rect, text in truth.secret:
                if where != number:
                    continue
                colors = image.color_count(clip=(rect * SCALE).irect, colors=True)
                total = sum(colors.values())
                ink = sum(count for color, count in colors.items() if color != b"\0\0\0" and min(color) < 200)
                if colors.get(b"\0\0\0", 0) / total < 0.6 or ink / total > 0.002:
                    problems.append(f"únik: {text!r} na straně {number + 1}")
            for where, rect, text in truth.keep if labels_kept else ():
                if where != number:
                    continue
                area = (pdf.Rect(rect.x0 + 1.5, rect.y0 + 0.36 * rect.height, rect.x1 - 1.5,
                                 rect.y1 - 0.28 * rect.height) * SCALE).irect
                ours, theirs = crop(image, area), crop(reference, area)
                changed = sum(ours[i:i + 3] != theirs[i:i + 3] for i in range(0, len(ours), 3))
                if changed / (len(ours) / 3) > 0.03:
                    problems.append(f"poškozeno: {text!r} na straně {number + 1}")
            if tessdata:
                from anonymizer.documents import recognize_image
                seen = squeeze(recognize_image(image, tessdata)[0])
                for text in truth.secret_texts:
                    if text not in NEUTRAL and squeeze(text) in seen:
                        problems.append(f"OCR přečetlo {text!r}")
    return problems


def office_part(suffix):
    name = "Bohumil Kratochvíl"
    if suffix == ".xlsx":
        return ("xl/workbook.xml", "xl/worksheets/sheet1.xml",
                f'<worksheet xmlns="{SHEET}"><sheetData><row><c r="A1" t="inlineStr"><is><t>{name}</t></is></c>'
                f'<c r="B1" t="inlineStr"><is><t>{MAIL}</t></is></c><c r="C1" t="inlineStr"><is><t>125 000 Kč</t></is></c>'
                f'</row></sheetData></worksheet>')
    return ("ppt/presentation.xml", "ppt/slides/slide1.xml",
            f'<slide xmlns:a="{DRAW}"><a:p><a:r><a:t>{name}, {MAIL}</a:t></a:r></a:p>'
            f'<a:p><a:r><a:t>125 000 Kč</a:t></a:r></a:p></slide>')


def build(folder):
    """Write the batch. Returns {relative name: expectation}; expectation is 'released',
    'withheld', 'failed' or 'skipped'."""
    folder = Path(folder)
    expected = {}

    def save(name, document, verdict="released"):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        document.save(path)
        document.close()
        expected[name] = verdict

    save("slovensko/vypis.pdf", corpus.slovak_register()[0])
    save("slovensko/vypis-pod-popisky.pdf", corpus.slovak_register(layout="below", order="columns")[0])
    save("slovensko/vypis-sken.pdf", corpus.as_scan(corpus.slovak_register()[0]))
    save("slovensko/neuplny-formular.pdf", corpus.slovak_register(fields=corpus.SK_FIELDS[:5])[0], "withheld")
    save("cesko/usneseni.pdf", corpus.czech_order_pdf()[0])
    save("cesko/usneseni-sken.pdf", corpus.as_scan(corpus.czech_order_pdf()[0], dpi=300))
    (folder / "cesko").mkdir(parents=True, exist_ok=True)
    corpus.czech_order_docx(folder / "cesko" / "usneseni.docx")
    expected["cesko/usneseni.docx"] = "released"
    for name, text in TEXTS.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
        expected[name] = "released"
    for suffix in (".xlsx", ".pptx"):
        main, part, xml = office_part(suffix)
        with ZipFile(folder / f"kancelar{suffix}", "w") as archive:
            archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            archive.writestr(main, "<root/>")
            archive.writestr(part, xml)
        expected[f"kancelar{suffix}"] = "released"
    with ZipFile(folder / "archiv.zip", "w") as archive:
        archive.writestr("dopis.txt", TEXTS["texty/dopis.txt"])
        archive.writestr("vnitrni/data.json", TEXTS["texty/data.json"])
    expected["archiv.zip"] = "released"
    with pdf.open() as sample:
        page = sample.new_page(width=430, height=90)
        page.insert_text((40, 50), "osoba@example.test", fontsize=22)
        page.get_pixmap(dpi=240).save(folder / "obrazek.png")
    expected["obrazek.png"] = "released"
    (folder / "poskozeny.docx").write_bytes(b"toto neni docx")
    expected["poskozeny.docx"] = "failed"
    (folder / "neznamy.bin").write_bytes(b"\x00\x01neznamy")
    expected["neznamy.bin"] = "skipped"
    return expected


def verify(source, output, tessdata=None):
    """Compare an output folder with what build() promised. Returns a list of problems."""
    source, output = Path(source), Path(output)
    problems = []
    truths = {
        "slovensko/vypis.pdf": (corpus.slovak_register()[1], True),
        "slovensko/vypis-pod-popisky.pdf": (corpus.slovak_register(layout="below", order="columns")[1], True),
        "slovensko/vypis-sken.pdf": (corpus.slovak_register()[1], True),
        "cesko/usneseni.pdf": (corpus.czech_order_pdf()[1], True),
        "cesko/usneseni-sken.pdf": (corpus.czech_order_pdf()[1], True),
    }
    scratch = output.parent / "_overeni"
    scratch.mkdir(exist_ok=True)
    expected = build(scratch)
    produced = {str(path.relative_to(output)).replace("\\", "/") for path in output.rglob("*") if path.is_file()}
    for name, verdict in expected.items():
        path = Path(name)
        target = output / path.with_name(f"{path.stem}_anonymizovano{path.suffix}")
        if verdict != "released":
            if target.exists():
                problems.append(f"{name}: neměl být vydán")
            continue
        if not target.exists():
            problems.append(f"{name}: nebyl vydán")
            continue
        if name in truths:
            truth, labels = truths[name]
            problems += [f"{name}: {problem}" for problem in check_pdf(source / name, target, truth, labels, tessdata)]
        elif path.suffix == ".png":
            colors = pdf.Pixmap(target.read_bytes()).color_count(colors=True)
            if colors.get(b"\0\0\0", 0) < 7000:
                problems.append(f"{name}: obrázek není začerněn")
        else:
            members = {name: target.read_bytes()}
            if path.suffix in {".docx", ".xlsx", ".pptx", ".zip"}:
                with ZipFile(target) as archive:
                    members = {member: archive.read(member) for member in archive.namelist()}
                    if path.suffix == ".zip" and any("dopis" in member or "vnitrni" in member for member in members):
                        problems.append(f"{name}: názvy položek archivu nebyly nahrazeny")
            everything = ""
            for member, data in members.items():
                plain = squeeze(re.sub(r"<[^>]*>", "", data.decode("utf-8", "ignore")))
                everything += plain
                for fragment in (corpus.CZ_FRAGMENTS if path.suffix == ".docx" else FRAGMENTS):
                    if squeeze(fragment) in plain:
                        problems.append(f"{name}: únik {fragment!r} v {member}")
            if squeeze("125 000 Kč") not in everything:
                problems.append(f"{name}: částka 125 000 Kč nezůstala zachována")
    stray = {name for name in produced if "_anonymizovano" not in name}
    if stray:
        problems.append(f"neočekávané soubory ve výstupu: {sorted(stray)}")
    for name in expected:
        if not (source / name).exists():
            problems.append(f"{name}: originál zmizel")
    return problems


if __name__ == "__main__":
    if sys.argv[1] == "build":
        Path(sys.argv[2]).mkdir(parents=True, exist_ok=True)
        print(json.dumps(build(sys.argv[2]), ensure_ascii=False, indent=1))
    else:
        found = verify(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        print("\n".join(found) or "BEZ PROBLÉMŮ")
        sys.exit(1 if found else 0)
