from pathlib import Path
import tempfile
import os

from .documents import redact_docx, redact_pdf
from .rules import Rules


def process_folders(input_root, output_root, tessdata=None):
    source_root = Path(input_root).resolve(strict=True)
    target_root = Path(output_root).resolve(strict=True)
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("Vstupní a výstupní složka musí být oddělené.")
    sources = sorted(path for path in source_root.rglob("*")
                     if path.suffix.lower() in {".docx", ".pdf"}
                     and path.is_file() and not path.name.startswith("~$")
                     and path.resolve().is_relative_to(source_root))
    if not sources:
        raise ValueError("Ve vstupní složce nejsou žádné dokumenty DOCX nebo PDF.")
    rules = Rules()
    for index, source in enumerate(sources, 1):
        suffix = source.suffix.lower()
        name = f"dokument-{index:04d}"
        with tempfile.TemporaryDirectory(prefix=".spisredakt-", dir=target_root) as scratch:
            temporary = Path(scratch) / ("output" + suffix)
            try:
                if suffix == ".docx":
                    redact_docx(source, temporary, rules)
                else:
                    redact_pdf(source, temporary, rules, tessdata)
                revision = 1
                while True:
                    infix = f"-{revision}" if revision > 1 else ""
                    destination = target_root / f"{name}{infix}{suffix}"
                    try:
                        if os.name == "nt":
                            temporary.rename(destination)
                        else:
                            os.link(temporary, destination)
                    except FileExistsError:
                        revision += 1
                    else:
                        break
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
