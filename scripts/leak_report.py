"""Count leaks of personal data in the output for a synthetic case file.

Builds a synthetic case file (tests/synthetic_spis.py) in a temporary folder,
runs it through the batch twice (as one ZIP and unpacked into single files),
looks for the inserted values in every layer of the outputs and prints counts
per kind, sizes and time. It never prints any value and never touches real
documents.

Usage: python scripts/leak_report.py [--scan-pages N] [--tessdata FOLDER]
"""
import argparse
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from anonymizer.batch import process_folders  # noqa: E402
from anonymizer.leaks import report, scan_tree  # noqa: E402
from anonymizer.reasons import reason_code  # noqa: E402
import synthetic_spis  # noqa: E402


def megabytes(path):
    path = Path(path)
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    return sum(item.stat().st_size for item in files) / 1_000_000


def run(mode, archive, root, values, tessdata):
    source, target = root / f"vstup-{mode}", root / f"vystup-{mode}"
    source.mkdir()
    target.mkdir()
    if mode == "ZIP":
        (source / "spis.zip").write_bytes(archive.read_bytes())
    else:
        with zipfile.ZipFile(archive) as packed:
            packed.extractall(source)
    started = time.time()
    results = list(process_folders(source, target, tessdata, markdown=True))
    processed = time.time() - started
    started = time.time()
    counted = scan_tree(target, values, tessdata)
    checked = time.time() - started
    released = [result for result in results if result[1] is not None]
    print(f"\n== {mode} ==")
    print(f"Vydáno {len(released)} z {len(results)} vstupů.")
    for _, destination, reason in results:
        if destination is None:
            print(f"Odmítnuto, kód: {reason_code(reason)}")
    print(report(counted))
    print(f"Velikost výstupů: {megabytes(target):.2f} MB")
    print(f"Čas zpracování {processed:.1f} s, kontroly {checked:.1f} s")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scan-pages", type=int, default=2)
    parser.add_argument("--tessdata", default=os.environ.get("SPISREDAKT_TEST_TESSDATA") or str(ROOT / "tessdata"))
    arguments = parser.parse_args()
    if not (Path(arguments.tessdata) / "ces.traineddata").is_file():
        sys.exit("Chybí česká data OCR. Spusťte scripts/get-ocr.sh nebo zadejte --tessdata.")
    with tempfile.TemporaryDirectory(prefix="SpisRedakt-leaks-") as folder:
        root = Path(folder)
        archive = root / "spis.zip"
        values = synthetic_spis.build_spis(archive, arguments.scan_pages)
        print(f"Vstupní ZIP: {megabytes(archive):.2f} MB, vložených hodnot: {len(values)}")
        for mode in ("ZIP", "dávka"):
            run(mode, archive, root, values, arguments.tessdata)


if __name__ == "__main__":
    main()
