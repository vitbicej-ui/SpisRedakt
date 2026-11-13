from pathlib import Path
import tempfile
import os

from .documents import redact_docx, redact_pdf
from .office import BLOCKS, redact_office
from .rules import Rules
from .audit import Audit


def process_folders(input_root, output_root, tessdata=None, audit_root=None, audit_ready=None):
    audit = Audit(audit_root) if audit_root is not None else None
    completed = False
    try:
        if audit and audit_ready:
            audit.on_fallback = audit_ready
            audit_ready(audit.path, audit.fallback)
        yield from _process_folders(input_root, output_root, tessdata, audit)
        completed = True
    finally:
        if audit:
            audit.finish(completed)


def _process_folders(input_root, output_root, tessdata, audit):
    source_root = Path(input_root).resolve(strict=True)
    target_root = Path(output_root).resolve(strict=True)
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("Vstupní a výstupní složka musí být oddělené.")
    sources = sorted(path for path in source_root.rglob("*")
                     if path.is_file() and not path.name.startswith("~$")
                     and path.resolve().is_relative_to(source_root))
    if not sources:
        raise ValueError("Ve vstupní složce nejsou žádné dokumenty DOCX, XLSX, PPTX nebo PDF.")
    rules = Rules()
    for index, source in enumerate(sources, 1):
        suffix = source.suffix.lower()
        supported = suffix in BLOCKS or suffix == ".pdf"
        fields = dict(input_id=index, format=suffix[1:].upper() if supported else "UNKNOWN")
        if not supported:
            if audit:
                audit.record("skipped", phase="identify", code="unsupported_format", attempts=0, **fields)
            continue
        directory = target_root / source.parent.relative_to(source_root)
        destination = directory / f"{source.stem}_anonymizovano{source.suffix}"
        phase = "prepare"
        attempts = 0
        collisions = 0
        try:
            if not directory.resolve().is_relative_to(target_root):
                raise ValueError("Výstupní podsložka odkazuje mimo výstupní složku.")
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".spisredakt-", dir=directory) as scratch:
                temporary = Path(scratch) / ("output" + suffix)
                phase = "redact_and_validate"
                attempts = 1
                if audit:
                    audit.record("processing", phase=phase, attempts=attempts, **fields)
                if suffix == ".pdf":
                    redact_pdf(source, temporary, rules, tessdata)
                else:
                    redact_office(source, temporary, rules, tessdata)
                phase = "publish"
                while True:
                    try:
                        if os.name == "nt":
                            temporary.rename(destination)
                        else:
                            os.link(temporary, destination)
                        break
                    except FileExistsError:
                        collisions += 1
                        destination = directory / f"{source.stem}_anonymizovano_{collisions + 1}{source.suffix}"
        except Exception as error:
            if audit:
                code = ("permission_denied" if isinstance(error, PermissionError) else
                        "validation_or_unsupported_feature" if isinstance(error, ValueError) else
                        "io_error" if isinstance(error, OSError) else "processing_error")
                audit.record("failed", phase=phase, code=code, attempts=attempts, **fields)
            reason = str(error) if type(error) is ValueError else type(error).__name__
            yield source, None, reason
        else:
            if audit:
                audit.record("released", phase=phase, code="pipeline_passed", attempts=attempts,
                             fallback="numbered_filename" if collisions else None,
                             collisions=collisions, **fields)
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
