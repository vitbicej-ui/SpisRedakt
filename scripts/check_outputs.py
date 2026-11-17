"""Check that redacted outputs open in LibreOffice and keep no personal data.

Synthetic inputs are produced by LibreOffice itself (from HTML and CSV), run
through the normal batch and converted back to PDF with LibreOffice. The
script fails when a conversion fails, a known value survives, or no label
appears. It never touches real documents.

Usage: python scripts/check_outputs.py [work folder]
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf as pdf  # noqa: E402

from anonymizer.batch import process_folders  # noqa: E402

PERSONAL = ["Karolína Bílá", "Bílá", "777 123 456", "kb@example.test", "800101/1234", "Dlouhá 5"]
HTML = """<html><head><meta charset="utf-8"></head><body>
<h1>Usnesení</h1>
<p>Smlouvu uzavřela Karolína Bílá, nar. 1. 2. 1980, bytem Dlouhá 5, Praha.</p>
<p>Kontakt: kb@example.test, telefon 777 123 456, rodné číslo 800101/1234.</p>
<table border="1"><tr><th>Jméno</th><th>Telefon</th><th>Poznámka</th></tr>
<tr><td>Karolína Bílá</td><td>777 123 456</td><td>bez poznámky</td></tr></table>
<p>Srov. usnesení Nejvyššího soudu ze dne 20. 12. 2016, sp. zn. 20 Cdo 4567/2016.</p>
</body></html>"""
CSV = "Jméno;Telefon;Poznámka\nKarolína Bílá;777 123 456;bez poznámky\n"
WRITER = ["docx", "odt", "rtf", "doc"]
CALC = ["xlsx", "ods", "xls"]


def soffice(*arguments, cwd):
    command = shutil.which("soffice") or shutil.which("libreoffice")
    if not command:
        sys.exit("LibreOffice (soffice) není nainstalovaný.")
    result = subprocess.run([command, "--headless", "--norestore", *arguments], cwd=cwd,
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def main(work):
    work = Path(work)
    source, target, back = work / "vstup", work / "vystup", work / "kontrola"
    for folder in (source, target, back):
        folder.mkdir(parents=True, exist_ok=True)
    (work / "dokument.html").write_text(HTML, encoding="utf-8")
    (work / "tabulka.csv").write_text(CSV, encoding="utf-8")
    # HTML would open in Writer/Web, which cannot save Office formats.
    for extension in WRITER:
        soffice("--infilter=HTML (StarWriter)", "--convert-to", extension, "--outdir", str(source),
                "dokument.html", cwd=work)
    for extension in CALC:
        soffice("--infilter=CSV:59,34,76,1", "--convert-to", extension, "--outdir", str(source),
                "tabulka.csv", cwd=work)
    created = sorted(path.suffix for path in source.iterdir())
    if created != sorted("." + extension for extension in WRITER + CALC):
        sys.exit(f"LibreOffice nevytvořil všechny vstupy: {created}")
    failures = []
    outputs = []
    for original, output, reason in process_folders(source, target):
        if output is None:
            failures.append(f"{original.name}: nevydáno ({reason})")
        else:
            outputs.append(output)
    for output in outputs:
        try:
            soffice("--convert-to", "pdf", "--outdir", str(back), str(output), cwd=work)
        except Exception as error:  # noqa: BLE001 - report every failed format
            failures.append(f"{output.name}: LibreOffice výstup neotevřel ({error})")
            continue
        converted = back / (output.stem + ".pdf")
        if not converted.is_file():
            failures.append(f"{output.name}: LibreOffice nevytvořil PDF")
            continue
        with pdf.open(converted) as document:
            text = " ".join(" ".join(page.get_text().split()) for page in document)
        leaked = [value for value in PERSONAL if value in text]
        if leaked:
            failures.append(f"{output.name}: zůstalo {leaked}")
        if "[OSOBA-" not in text:
            failures.append(f"{output.name}: chybí štítek osoby")
        print(f"{output.name}: OK" if not leaked and "[OSOBA-" in text else f"{output.name}: CHYBA")
    expected = len(WRITER) + len(CALC)
    if len(outputs) != expected:
        failures.append(f"vydáno {len(outputs)} z {expected} souborů")
    if failures:
        print("\n".join(failures))
        sys.exit(1)
    print(f"Všech {expected} výstupů LibreOffice otevřel bez osobních údajů.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        with tempfile.TemporaryDirectory(prefix="spisredakt-lo-") as folder:
            main(folder)
