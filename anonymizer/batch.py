from pathlib import Path
import tempfile
import os

from .documents import redact_docx, redact_pdf
from .office import BLOCKS, redact_office
from .rules import Rules


def process_folders(input_root, output_root, tessdata=None):
    source_root = Path(input_root).resolve(strict=True)
    target_root = Path(output_root).resolve(strict=True)
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("Vstupní a výstupní složka musí být oddělené.")
    sources = sorted(path for path in source_root.rglob("*")
                     if (path.suffix.lower() in BLOCKS or path.suffix.lower() == ".pdf")
                     and path.is_file() and not path.name.startswith("~$")
                     and path.resolve().is_relative_to(source_root))
    if not sources:
        raise ValueError("Ve vstupní složce nejsou žádné dokumenty DOCX, XLSX, PPTX nebo PDF.")
    rules = Rules()
    for source in sources:
        suffix = source.suffix.lower()
        directory = target_root / source.parent.relative_to(source_root)
        destination = directory / f"{source.stem}_anonymizovano{source.suffix}"
        try:
            if not directory.resolve().is_relative_to(target_root):
                raise ValueError("Výstupní podsložka odkazuje mimo výstupní složku.")
            if destination.exists():
                raise ValueError("Výstup už existuje. Vyberte prázdnou výstupní složku.")
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".spisredakt-", dir=directory) as scratch:
                temporary = Path(scratch) / ("output" + suffix)
                if suffix == ".pdf":
                    redact_pdf(source, temporary, rules, tessdata)
                else:
                    redact_office(source, temporary, rules, tessdata)
                if os.name == "nt":
                    temporary.rename(destination)
                else:
                    os.link(temporary, destination)
        except Exception as error:
            reason = str(error) if type(error) is ValueError else type(error).__name__
            yield source, None, reason
        else:
            yield source, destination, ""


def process_batch(sources, output_root, alias_lines=(), tessdata=None, force_ocr=False):
    """Create a private review folder and yield one result per input."""
    rules = Rules(alias_lines)
    sources = sorted({Path(source).resolve(strict=True) for source in sources})
    directory = Path(tempfile.mkdtemp(prefix="KE-KONTROLE-", dir=output_root))
    for index, source in enumerate(sources, 1):
        suffix = source.suffix.lower()
        destination = directory / f"dokument-{index:04d}{suffix}"
        try:
            if suffix == ".docx":
                redact_docx(source, destination, rules)
            elif suffix == ".pdf":
                redact_pdf(source, destination, rules, tessdata, force_ocr)
            else:
                raise ValueError("Nepodporovaný formát.")
        except Exception as error:
            destination.unlink(missing_ok=True)
            reason = str(error) if type(error) is ValueError else type(error).__name__
            yield source, None, reason
        else:
            yield source, destination, ""
