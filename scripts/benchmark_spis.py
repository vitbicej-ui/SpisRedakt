"""Measure time and memory of the whole path on a large synthetic case file.

Builds one synthetic ZIP (scanned PDFs and TIFFs, DOCX, XLSX with thousands of rows,
e-mails with attachments, a nested ZIP and one big uncompressed item) from the invented
data of tests/synthetic_spis.py, runs ``process_folders`` once for the Markdown only and
once with the control ZIP, and prints sizes, times, OCR time per page, peak memory of the
process, the estimate of tokens and the count of leaks of the inserted values by kind
(the goal is 0). It never prints a value or any text of a document and never touches real
documents.

Usage:
    python scripts/benchmark_spis.py [--pages 300] [--mb 200] [--rows 5000] [--folder DIR]
                                     [--tessdata DIR] [--profile] [--no-leaks]

A quick run: ``--pages 20 --mb 30``. Peak memory is read from ``ru_maxrss`` on Linux and
macOS; on Windows only the time is measured. Every run is done in a child process, so its
peak is its own.
"""
import argparse
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pymupdf  # noqa: E402

from anonymizer.leaks import Value, report, scan_tree  # noqa: E402
from anonymizer.names import FEMALE, MALE  # noqa: E402
import synthetic_spis as spis  # noqa: E402

try:
    import resource
except ImportError:  # Windows
    resource = None

STEMS = ("Dvořák Kratochvíl Marek Svoboda Pokorný Horák Procházka Šebesta Vlček Urban Kolář Havel Zima Bureš "
         "Doležal Fiala Čermák Němec Sedláček Moravec Kopecký Veselý Jelínek Růžička Beran Navrátil Mach Kos "
         "Pešek Soukup Holub Kadlec Štěpánek Hrubý Skála Vaněk Tichý Bartoš Chalupa Pavlík Kubát Šimek "
         "Koubek Hanzlík Jirásek Krejčí Menšík Odehnal Pecha Sýkora Trnka Vrána Zelenka Žák Bednář Cibulka "
         "Drábek Hruška").split()
FIRSTS = [(name, False) for name in MALE.split()] + [(name, True) for name in FEMALE.split()]
STREETS = ("Lipová Zahradní Školní Nádražní Polní Krátká Dlouhá Mlýnská Luční Sadová Jarní Zámecká").split()
TOWNS = ("280 02 Kolín", "110 00 Praha 1", "282 01 Český Brod", "284 01 Kutná Hora", "290 01 Poděbrady",
         "281 51 Velký Osek", "250 01 Brandýs nad Labem", "252 63 Roztoky")
KINDS_PER_PAGE = 4
D = datetime.date


def ascii_fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()


def make_person(i):
    """The i-th invented person: the same i always gives the same person."""
    first, female = FIRSTS[i * 7 % len(FIRSTS)]
    last = STEMS[(i * 13 + i // len(FIRSTS)) % len(STEMS)]
    last = last[:-1] + "á" if female and last.endswith("ý") else last + "ová" if female else last
    return spis.Person(first, last, D(1940 + i % 60, 1 + i % 12, 1 + i % 28),
                       f"{STREETS[i % len(STREETS)]} {1 + i % 97}", TOWNS[i % len(TOWNS)],
                       f"7{i % 10}{(i // 10) % 10} {100 + i % 900} {100 + (i * 3) % 900}",
                       f"{ascii_fold(first)}.{ascii_fold(last)}{i}@example.test",
                       f"{100000 + i * 37 % 899999}/0100", f"{100000000 + i * 7919 % 899999999}", female=female)


def page_lines(number):
    person = make_person(number)
    return [f"Protokol o jednání, strana {number + 1}",
            f"Svědek {person.name}, nar. {person.date()}, bytem {person.address}.",
            f"Tel. {person.phone}.",
            f"Č. ú. {person.account}, OP č.: {person.document}.",
            "Svědek potvrdil, že vozidlo převzal bez výhrad a cenu uhradil v hotovosti."]


def scan_pdf_file(path, first, count):
    """A scanned PDF of ``count`` different pages built in small chunks (memory stays low)."""
    with pymupdf.open() as document:
        for start in range(first, first + count, 10):
            chunk = [page_lines(n) for n in range(start, min(start + 10, first + count))]
            for pixmap in spis.image_pixmaps(chunk):
                page = document.new_page(width=pixmap.width * 72 / 300, height=pixmap.height * 72 / 300)
                page.insert_image(page.rect, pixmap=pixmap)
        document.save(path, deflate=True)


def scan_tiff_file(path, first, count):
    from test_processing import ProcessingTests

    pixmaps = spis.image_pixmaps([page_lines(n) for n in range(first, first + count)])
    ProcessingTests.write_tiff(path, pixmaps)


def big_xlsx(rows):
    """Same layout as the XLSX of the synthetic file, but with a dictionary instead of a list lookup."""
    strings, index, sheet = [], {}, []
    for number, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row):
            if value not in index:
                index[value] = len(strings)
                strings.append(value)
            cells.append(f'<c r="{"ABCDEFG"[column]}{number}" t="s"><v>{index[value]}</v></c>')
        sheet.append(f'<row r="{number}">{"".join(cells)}</row>')
    # Reuse the container of the synthetic builder: build with one row and replace the two big parts.
    base = zipfile.ZipFile(io.BytesIO(spis.xlsx([("a",)])))
    from xml.sax.saxutils import escape

    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in base.namelist():
            if name == "xl/worksheets/sheet1.xml":
                archive.writestr(name, f'{head}<worksheet xmlns="{main}"><sheetData>{"".join(sheet)}</sheetData></worksheet>')
            elif name == "xl/sharedStrings.xml":
                archive.writestr(name, f'{head}<sst xmlns="{main}">'
                                 + "".join(f"<si><t>{escape(value)}</t></si>" for value in strings) + "</sst>")
            else:
                archive.writestr(name, base.read(name))
    return stream.getvalue()


def people_rows(count, start=0):
    rows = [("Jméno a příjmení", "Rodné číslo", "Datum narození", "Adresa", "Telefon a e-mail", "Doklady")]
    for i in range(start, start + count):
        person = make_person(i)
        rows.append((person.name, person.rc, person.date("short"), person.address,
                     f"{person.phone}; {person.email}", f"OP č.: {person.document}, č. ú. {person.account}"))
    return rows


def docx_with_people(start, count):
    def build(document):
        spis.with_frame(document, [f"Seznam účastníků řízení ve věci {spis.CASE}:"])
        for i in range(start, start + count):
            person = make_person(i)
            document.add_paragraph(f"{person.name}, nar. {person.date()}, bytem {person.address}, tel. {person.phone}.")

    return spis.docx(build)


def big_pdf(path, size):
    """A one-page PDF with a big unreferenced incompressible stream (stands for a huge scan)."""
    with pymupdf.open("pdf", spis.text_pdf([f"Příloha: {make_person(5).name}, tel. {make_person(5).phone}."])) as document:
        xref = document.get_new_xref()
        document.update_object(xref, "<<>>")
        document.update_stream(xref, os.urandom(size), compress=False)
        document.xref_set_key(document[0].xref, "Padding", f"{xref} 0 R")
        document.save(path)


def build_big_spis(target, pages=300, megabytes=200, rows=5000, work=None):
    """Write the big synthetic ZIP to ``target``; return the inserted values and a short description."""
    work = Path(work or tempfile.mkdtemp(prefix="SpisRedakt-bench-build-"))
    work.mkdir(parents=True, exist_ok=True)
    base = work / "zaklad.zip"
    values = list(spis.build_spis(base, 2))
    sampled = min(rows, 150)
    for i in range(sampled):
        values.extend(make_person(i).values())
    pdf_pages = max(1, pages * 7 // 10) if pages > 1 else 1
    tiff_pages = pages - pdf_pages
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        with zipfile.ZipFile(base) as source:
            for name in source.namelist():
                out.writestr(name, source.read(name))
        cursor = 0
        part = 0
        while pdf_pages > 0:
            count = min(pdf_pages, 50)
            part += 1
            path = work / f"sken{part}.pdf"
            scan_pdf_file(path, cursor, count)
            out.write(path, f"07_skeny/sken_{part:02d}.pdf")
            path.unlink()
            cursor += count
            pdf_pages -= count
        part = 0
        while tiff_pages > 0:
            count = min(tiff_pages, 20)
            part += 1
            path = work / f"sken{part}.tif"
            scan_tiff_file(path, cursor, count)
            out.write(path, f"07_skeny/sken_{part:02d}.tif")
            path.unlink()
            cursor += count
            tiff_pages -= count
        out.writestr("08_tabulky/osoby.xlsx", big_xlsx(people_rows(rows)))
        out.writestr("08_tabulky/ucastnici.docx", docx_with_people(1000, min(rows, 400)))
        for n in range(max(3, min(40, pages // 8))):
            person = make_person(2000 + n)
            attachment = docx_with_people(3000 + n, 5)
            out.writestr(f"09_posta/zprava_{n + 1:03d}.eml",
                         spis.mail(f"Podklady {n + 1}", person, make_person(2100 + n),
                                   f"Dobrý den, v příloze posílám podklady. Tel. {person.phone}.\n{person.name}\n",
                                   attachment, "priloha.docx"))
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as inner:
            inner.writestr("a.docx", docx_with_people(4000, 20))
            inner.writestr("b.txt", f"Poznámka: {make_person(4100).name}, {make_person(4100).email}.\n")
            deeper = io.BytesIO()
            with zipfile.ZipFile(deeper, "w", zipfile.ZIP_DEFLATED) as innermost:
                innermost.writestr("c.docx", docx_with_people(4200, 10))
            inner.writestr("hlubsi.zip", deeper.getvalue())
        out.writestr("10_archiv/vnoreny.zip", nested.getvalue())
        out.fp.flush()
        used = Path(target).stat().st_size
        extra = int(megabytes * 1_000_000) - used
        if extra > 2_000_000:
            path = work / "velka_polozka.pdf"
            big_pdf(path, extra)
            out.write(path, "11_velka/velka_polozka.pdf", compress_type=zipfile.ZIP_STORED)
            path.unlink()
    return values, f"{pages} stran skenů, {rows} řádků osob"


# -- measuring ---------------------------------------------------------------------------------

def megabytes_of(path):
    path = Path(path)
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    return sum(item.stat().st_size for item in files) / 1_000_000


def peak_megabytes():
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024 / (1024 if sys.platform == "darwin" else 1)


def worker(mode, source, target, tessdata, profile):
    """Run one batch in this process and print one JSON line (sizes, times, memory, tokens; no content)."""
    from anonymizer.batch import process_folders

    ocr = dict(count=0, seconds=0.0)
    original = pymupdf.Pixmap.pdfocr_tobytes

    def timed(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            ocr["count"] += 1
            ocr["seconds"] += time.perf_counter() - started

    pymupdf.Pixmap.pdfocr_tobytes = timed
    summary = {}
    results = []

    def run():
        results.extend(process_folders(source, target, tessdata, markdown=True, copies=(mode == "zip"),
                                       summary=summary.update))

    started = time.perf_counter()
    if profile:
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        profiler.runcall(run)
        stats = pstats.Stats(profiler, stream=sys.stderr)
        stats.sort_stats("cumulative").print_stats(30)
        stats.sort_stats("tottime").print_stats(20)
    else:
        run()
    seconds = time.perf_counter() - started
    print(json.dumps(dict(
        mode=mode, seconds=seconds, ocr_pages=ocr["count"], ocr_seconds=ocr["seconds"], peak_mb=peak_megabytes(),
        released=sum(1 for _, path, _ in results if path), rejected=sum(1 for _, path, _ in results if not path),
        reasons=sorted({str(reason) for _, path, reason in results if not path}),
        documents=summary.get("documents"), tokens=summary.get("tokens_after"),
        tokens_before=summary.get("tokens_before"), skipped=len(summary.get("skipped", ())))))


def run_worker(mode, source, target, tessdata, profile):
    command = [sys.executable, "-I", __file__, "--worker", mode, "--input", str(source), "--output", str(target),
               "--tessdata", str(tessdata)] + (["--profile"] if profile else [])
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
                       QT_QPA_PLATFORM="offscreen")
    # -I ignores PYTHONPATH; the script inserts the paths itself.
    done = subprocess.run(command, capture_output=True, text=True, env=environment)
    if profile:
        sys.stderr.write(done.stderr)
    if done.returncode:
        raise SystemExit(f"Měření '{mode}' selhalo (kód {done.returncode}).")
    return json.loads(done.stdout.strip().splitlines()[-1])


def show(label, result, output, leaks, leak_seconds):
    peak = result["peak_mb"]
    print(f"\n== {label} ==")
    print(f"Čas celkem: {result['seconds']:.1f} s ({result['seconds'] / 60:.1f} min)")
    if result["ocr_pages"]:
        print(f"OCR: {result['ocr_pages']} stran, {result['ocr_seconds']:.1f} s, "
              f"{result['ocr_seconds'] / result['ocr_pages']:.2f} s na stranu")
    print("Špička paměti procesu: " + (f"{peak:.0f} MB" if peak is not None else "neměří se na tomto systému"))
    print(f"Vydáno {result['released']}, odmítnuto {result['rejected']}, dokumentů {result['documents']}, "
          f"nezpracovaných {result['skipped']}")
    for reason in result["reasons"]:
        print(f"Odmítnutí, hlášení: {reason}")
    print(f"Velikost výstupu: {megabytes_of(output):.2f} MB, odhad tokenů: {result['tokens']} (před úpravami {result['tokens_before']})")
    if leaks is not None:
        print(report(leaks) + f" (kontrola {leak_seconds:.1f} s)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=300, help="počet stran skenů (výchozí 300)")
    parser.add_argument("--mb", type=float, default=200, help="přibližná velikost vstupního ZIPu v MB (výchozí 200)")
    parser.add_argument("--rows", type=int, default=5000, help="počet řádků osob v XLSX (výchozí 5000)")
    parser.add_argument("--folder", help="pracovní složka (výchozí dočasná, po skončení se smaže)")
    parser.add_argument("--tessdata", default=os.environ.get("SPISREDAKT_TEST_TESSDATA") or str(ROOT / "tessdata"))
    parser.add_argument("--profile", action="store_true", help="vypíše profil funkcí (jen názvy funkcí) na chybový výstup")
    parser.add_argument("--no-leaks", action="store_true", help="vynechá kontrolu detektorem úniků")
    parser.add_argument("--worker", choices=("md", "zip"), help=argparse.SUPPRESS)
    parser.add_argument("--input", help=argparse.SUPPRESS)
    parser.add_argument("--output", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.worker:
        worker(arguments.worker, arguments.input, arguments.output, arguments.tessdata, arguments.profile)
        return
    if not (Path(arguments.tessdata) / "ces.traineddata").is_file():
        sys.exit("Chybí česká data OCR. Spusťte scripts/get-ocr.sh nebo zadejte --tessdata.")
    scratch = None
    if arguments.folder:
        root = Path(arguments.folder)
        root.mkdir(parents=True, exist_ok=True)
    else:
        scratch = tempfile.TemporaryDirectory(prefix="SpisRedakt-bench-")
        root = Path(scratch.name)
    try:
        started = time.perf_counter()
        input_zip = root / "vstup" / "spis.zip"
        input_zip.parent.mkdir(parents=True, exist_ok=True)
        values, described = build_big_spis(input_zip, arguments.pages, arguments.mb, arguments.rows, root / "stavba")
        print(f"Vstup: {described}, ZIP {megabytes_of(input_zip):.1f} MB, vložených hodnot {len(values)} "
              f"(sestaveno za {time.perf_counter() - started:.0f} s)")
        for mode, label in (("md", "Markdown"), ("zip", "Markdown a kontrolní ZIP")):
            output = root / f"vystup-{mode}"
            output.mkdir()
            result = run_worker(mode, input_zip.parent, output, arguments.tessdata, arguments.profile)
            leaks, spent = None, 0.0
            if not arguments.no_leaks:
                begun = time.perf_counter()
                leaks = scan_tree(output, values, arguments.tessdata)
                spent = time.perf_counter() - begun
            show(label, result, output, leaks, spent)
    finally:
        if scratch:
            scratch.cleanup()


if __name__ == "__main__":
    main()
