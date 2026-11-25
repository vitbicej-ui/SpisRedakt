from pathlib import Path
import dataclasses
import re
import tempfile
import os

from .documents import redact_docx, redact_pdf
from .formats import SUPPORTED, ARCHIVE_BYTES, FormatError, size_limit, identify, has_signature, HEADER_BYTES, output_suffix, redact_file
from .rules import Rules
from .pseudonyms import INTERNAL_NAMES, LabelMap
from .audit import Audit, VERSION
from .manual import MANUAL_MISMATCH
from .reasons import reason_code


MARKDOWN_NAME = "spis_anonymizovano"
# The control archive of an input ZIP has a fixed name: the name of the input may contain personal data.
ZIP_COPY_NAME = "spis_kontrola"
RELEASE_CHECK = ("markdown_release_check", "Kontrola výstupu našla možný osobní údaj, soubor nebyl vydán.")


def process_folders(input_root, output_root, tessdata=None, audit_root=None, audit_ready=None, selected=None,
                    markdown=False, copies=True, split=False, progress=None, summary=None, catalog=None, manual_areas=None):
    """Process every input in two phases and yield ``(source, destination, error)``.

    Phase 1 reads all inputs and registers every person of the whole case file in one ``Rules``.
    Then the Markdown file (``markdown``) is written, checked and released as
    ``(None, path_or_None, error)``, and the anonymized copies (``copies``) follow, one tuple per input.
    ``summary(dict)`` is called once about the Markdown (path, documents, tokens, skipped, error).
    ``catalog(dict)`` (memory only, for the preview) is called with ``{"documents": {Dn: facts}, "inputs":
    {input_id: (path, signature)}}`` after phase 1, ``{"markdown": path_or_None, "split": bool}`` after the
    Markdown and ``{"copy": (input_id, path)}`` for every released copy. It never changes any output.
    ``manual_areas`` (``manual.Areas``, memory only) adds blackout of areas drawn in the preview to the inputs
    shown as pages, in the Markdown and in the control copy alike; an input whose areas do not fit
    (changed file, missing page or item, other orientation) is released in neither.
    """
    audit = Audit(audit_root) if audit_root is not None else None
    completed = False
    try:
        if audit and audit_ready:
            audit.on_fallback = audit_ready
            audit_ready(audit.path, audit.fallback)
        yield from _process_folders(input_root, output_root, tessdata, audit, selected,
                                    markdown, copies, split, progress, summary, catalog, manual_areas)
        completed = True
    finally:
        if audit:
            audit.finish(completed)


def stage_input(source, private, size_cap=ARCHIVE_BYTES):
    """Copy an input into the private folder and detect its format.

    Returns ``(staged path with its real suffix, suffix)``, or ``(None, None)`` for an unsupported format.
    """
    staged = Path(private) / "input"
    with source.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size > size_cap:
            raise FormatError("size_limit", "Vstup překračuje limit velikosti.")
        signed = has_signature(stream.read(HEADER_BYTES))
        if not signed and source.suffix.lower() not in SUPPORTED:
            return None, None
        stream.seek(0)
        # Without a binary signature the format follows the suffix (text, EML),
        # so its own limit applies before anything larger reaches the disk.
        size = copy_limited(stream, staged, size_cap if signed else size_limit(source.suffix.lower()))
    suffix = identify(staged, source.suffix.lower())
    if size > size_limit(suffix):
        raise FormatError("size_limit", "Vstup překračuje limit velikosti.")
    if suffix is None and source.suffix.lower() in SUPPORTED:
        raise FormatError("invalid_signature", "Obsah neodpovídá deklarovanému formátu.")
    if suffix not in SUPPORTED:
        return None, None
    return staged.rename(staged.with_name("input" + suffix)), suffix


def failure_code(error):
    if isinstance(error, FormatError):
        return error.code
    code = reason_code(error)
    return "processing_error" if code == "other" else code


def input_signature(source):
    """Size and modification time of an input; the preview refuses an input that changed since."""
    try:
        stat = Path(source).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def collect_documents(sources, rules, tessdata, progress, audit, catalog=None, views=None):
    """Phase 1: extract every input into memory (numbered D1..Dn across the batch) and register all labels."""
    from . import extract, markdown as md

    documents, origin, signatures = [], {}, {}
    offset = base_items = base_pages = 0
    for input_id, source in enumerate(sources, 1):
        found = []
        raised = []
        last = [0, 0]
        signatures[input_id] = (source, input_signature(source))

        def relay(items, pages):
            last[:] = items, pages
            if progress:
                try:
                    progress(base_items + items, base_pages + pages)
                except BaseException as error:
                    raised.append(error)
                    raise

        view = views.get(input_id) if views else None
        if view is not None:
            view.begin("extract")
            # Areas of a file that changed since they were drawn would land on other content.
            view.refused = view.signature != signatures[input_id][1]
        try:
            if view is not None and view.refused:
                found.append(extract.Skipped(1, "UNKNOWN", "manual_area_mismatch"))
            else:
                with tempfile.TemporaryDirectory(prefix="SpisRedakt-input-") as private:
                    staged, suffix = stage_input(source, private)
                    if staged is None:
                        found.append(extract.Skipped(1, "UNKNOWN", "format_unsupported"))
                    else:
                        found.extend(extract.walk(staged, tessdata, relay, view))
        except Exception as error:
            if raised:
                raise
            # Items already read stay; the failure itself becomes one unprocessed item.
            found.append(extract.Skipped(1 + max((item.index for item in found), default=0), "UNKNOWN",
                                         failure_code(error)))
        if view is not None and not view.refused and view.unused():
            # An area whose item was never reached has nothing to apply to: nothing of this input is released.
            view.refused = True
            found = [extract.Skipped(1, "UNKNOWN", "manual_area_mismatch")]
        base_items += last[0]
        base_pages += last[1]
        top = max((item.index for item in found), default=0)
        for item in found:
            if isinstance(item, extract.Document):
                item = dataclasses.replace(item, index=item.index + offset,
                                           parent=None if item.parent is None else item.parent + offset,
                                           locator=None if item.locator is None else (input_id, item.locator[1]))
            else:
                item = dataclasses.replace(item, index=item.index + offset)
            origin[item.index] = input_id
            documents.append(item)
        offset += top
        if audit:
            audit.record("extracted", phase="extract", input_id=input_id,
                         documents=sum(isinstance(i, extract.Document) for i in found),
                         skipped=sum(isinstance(i, extract.Skipped) for i in found),
                         **(dict(manual_areas=view.count) if view is not None else {}))
    md.collect(documents, rules)
    if catalog:
        catalog(dict(documents=_facts(documents), inputs=signatures))
    return documents, origin


def _facts(documents):
    """What the preview may know about each item: format, page count, parent, locator and the code of a skip."""
    facts = {}
    for item in documents:
        if hasattr(item, "pages"):
            facts[item.index] = dict(format=item.format, pages=sum(page.number is not None for page in item.pages),
                                     parent=item.parent, locator=item.locator, code=None)
        else:
            facts[item.index] = dict(format=item.format, pages=0, parent=None, locator=None, code=item.code)
    return facts


def release_values(rules):
    """Known personal values of the batch for the independent check (memory only)."""
    from . import czech
    from .leaks import Value

    values = []
    for surface in rules.surfaces.get("OSOBA", ()):
        if len(surface.split()) >= 2:
            values.append(Value("JMENO", surface))
    for kind in ("RC", "ADRESA", "TELEFON", "EMAIL", "UCET", "DOKLAD", "ICO"):
        values.extend(Value(kind, surface) for surface in rules.surfaces.get(kind, ()))
    for surface in rules.surfaces.get("NAROZENI", ()):
        iso = czech.structured_identity("NAROZENI", surface)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
            values.append(Value("NAROZENI", iso))
    return values


def check_release(texts, rules):
    """Refuse the Markdown when the rules or the independent detector find a personal value in it."""
    from . import leaks

    values = release_values(rules)
    for text in texts:
        if rules.find(text):
            raise FormatError(*RELEASE_CHECK)
    if sum(leaks.scan_texts(texts, values).values()):
        raise FormatError(*RELEASE_CHECK)


def publish_markdown(output, target_root, rules):
    """Check and write the rendered Markdown atomically; return the path of the file or folder."""
    check_release([output.text, *output.files.values()], rules)
    with tempfile.TemporaryDirectory(prefix=".spisredakt-", dir=target_root) as scratch:
        if output.files:
            staged = Path(scratch) / "folder"
            staged.mkdir()
            for filename, text in output.files.items():
                (staged / filename).write_text(text, encoding="utf-8")
        else:
            staged = Path(scratch) / "spis.md"
            staged.write_text(output.text, encoding="utf-8")
        suffix = "" if output.files else ".md"
        collisions = 0
        while True:
            destination = target_root / (MARKDOWN_NAME + (f"_{collisions + 1}" if collisions else "") + suffix)
            try:
                if output.files:
                    # A directory cannot be hard-linked; rename fails if the target is a non-empty directory.
                    if destination.exists():
                        raise FileExistsError
                    staged.rename(destination)
                elif os.name == "nt":
                    staged.rename(destination)
                else:
                    os.link(staged, destination)
                return destination
            except FileExistsError:
                collisions += 1


def _process_folders(input_root, output_root, tessdata, audit, selected, markdown=False, copies=True,
                     split=False, progress=None, summary=None, catalog=None, manual_areas=None):
    source_root = Path(input_root).resolve(strict=True)
    target_root = Path(output_root).resolve(strict=True)
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("Vstupní a výstupní složka musí být oddělené.")
    sources = sorted(path for path in (source_root.rglob("*") if selected is None else selected)
                     if path.is_file() and not path.name.startswith("~$") and path.name not in INTERNAL_NAMES
                     and path.resolve().is_relative_to(source_root))
    if not sources:
        raise ValueError("Ve vstupní složce nejsou žádné dokumenty DOCX, XLSX, PPTX nebo PDF.")
    # Pseudonyms shared with earlier batches into this folder; None (no DPAPI, damaged map) = as without it.
    label_map = LabelMap.open(target_root)
    rules = Rules(label_map=label_map)
    try:
        yield from _release(sources, source_root, target_root, rules, tessdata, audit, markdown, copies, split,
                            progress, summary, catalog, manual_areas)
    finally:
        if label_map:
            label_map.save(rules.counts)


def _release(sources, source_root, target_root, rules, tessdata, audit, markdown, copies, split, progress, summary,
             catalog=None, manual_areas=None):
    views = manual_areas.views() if manual_areas else {}
    documents, _ = collect_documents(sources, rules, tessdata, progress, audit, catalog, views)
    if markdown:
        for item in _markdown(documents, rules, target_root, audit, split, summary):
            if catalog:
                catalog(dict(markdown=item[1], split=split))
            yield item
    if not copies:
        return
    for index, source in enumerate(sources, 1):
        view = views.get(index)
        fields = dict(input_id=index, format="UNKNOWN", **(dict(manual_areas=view.count) if view else {}))
        phase = "identify"
        attempts = 0
        collisions = 0
        skipped_members = []
        try:
            if view is not None:
                if view.refused:
                    raise ValueError(MANUAL_MISMATCH)
                view.begin("copy")
            with tempfile.TemporaryDirectory(prefix="SpisRedakt-input-") as private:
                staged, suffix = stage_input(source, private)
                if staged is None:
                    if audit:
                        audit.record("skipped", phase=phase, code="unsupported_format", attempts=0, **fields)
                    continue
                fields["format"] = suffix[1:].upper()
                phase = "prepare"
                directory = target_root.joinpath(*(safe_name(part, rules) for part in source.parent.relative_to(source_root).parts))
                stem = safe_name(source.stem, rules)
                released = output_suffix(suffix)
                stem = ZIP_COPY_NAME if suffix == ".zip" else f"{stem}_anonymizovano"
                destination = directory / f"{stem}{released}"
                if not directory.resolve().is_relative_to(target_root):
                    raise ValueError("Výstupní podsložka odkazuje mimo výstupní složku.")
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".spisredakt-", dir=directory) as scratch:
                    temporary = Path(scratch) / ("output" + released)
                    phase = "redact_and_validate"
                    attempts = 1
                    if audit:
                        audit.record("processing", phase=phase, attempts=attempts, **fields)
                    skipped_members = redact_file(staged, temporary, suffix, rules, tessdata,
                                                  view.item(()) if view is not None else None) or []
                    if view is not None and view.unused():
                        raise ValueError(MANUAL_MISMATCH)
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
                            destination = directory / f"{stem}_{collisions + 1}{released}"
        except Exception as error:
            if audit:
                code = (error.code if isinstance(error, FormatError) else
                        "permission_denied" if isinstance(error, PermissionError) else
                        "validation_or_unsupported_feature" if isinstance(error, ValueError) else
                        "io_error" if isinstance(error, OSError) else "processing_error")
                reason = reason_code(error) if code == "validation_or_unsupported_feature" else None
                audit.record("failed", phase=phase, code=code, reason=reason, attempts=attempts, **fields)
            reason = str(error) if isinstance(error, FormatError) or type(error) is ValueError else type(error).__name__
            yield source, None, reason
        else:
            if audit:
                extra = dict(skipped_members=[list(item) for item in skipped_members]) if skipped_members else {}
                audit.record("released", phase=phase, code="pipeline_passed", attempts=attempts,
                             fallback="numbered_filename" if collisions else None,
                             collisions=collisions, **fields, **extra)
            if catalog:
                catalog(dict(copy=(index, destination)))
            yield source, destination, ""


def _markdown(documents, rules, target_root, audit, split, summary):
    """Render, check and release the Markdown; yield one ``(None, path_or_None, error)``."""
    from . import markdown as md

    result = dict(markdown=None, documents=0, tokens_before=0, tokens_after=0, skipped=[], error="")
    path = None
    try:
        output = md.render(documents, rules, split, VERSION)
        result.update(documents=output.documents, tokens_before=output.tokens_before,
                      tokens_after=output.tokens_after, skipped=[tuple(item) for item in output.skipped])
        path = publish_markdown(output, target_root, rules)
        result["markdown"] = path
    except Exception as error:
        result["error"] = str(error) if isinstance(error, FormatError) or type(error) is ValueError else type(error).__name__
        if audit:
            audit.record("markdown", phase="markdown_release", state="failed",
                         code=error.code if isinstance(error, FormatError) else "processing_error",
                         documents=result["documents"], skipped_count=len(result["skipped"]),
                         skipped=[list(item) for item in result["skipped"]],
                         tokens=result["tokens_after"])
    else:
        if audit:
            audit.record("markdown", phase="markdown_release", state="released", code="pipeline_passed",
                         documents=result["documents"], skipped_count=len(result["skipped"]),
                         skipped=[list(item) for item in result["skipped"]],
                         tokens=result["tokens_after"])
    if summary:
        summary(result)
    yield None, path, result["error"]


def copy_limited(stream, target, limit=ARCHIVE_BYTES, block=1 << 20):
    """Copy a stream block by block and refuse it as soon as it exceeds the limit."""
    size = 0
    with open(target, "wb") as out:
        while chunk := stream.read(block):
            size += len(chunk)
            if size > limit:
                raise FormatError("size_limit", "Vstup překračuje limit velikosti.")
            out.write(chunk)
    return size


BIRTH_NUMBER = re.compile(r"(?<!\d)(\d{6})[-_ .]?(\d{3,4})(?!\d)")


def name_variant(value, rules):
    """The name written the way the rules read text: a slash in the birth number, spaces between words.

    A file name cannot hold a slash and often joins words with underscores, so the plain name would hide
    a person from the rules. The variant is used only when it reveals something the name itself does not.
    """
    variants = [BIRTH_NUMBER.sub(r"\1/\2", value), value.replace("_", " ").replace(".", " ")]
    variants.append(BIRTH_NUMBER.sub(r"\1/\2", variants[1]))
    for variant in variants:
        if variant != value and len(rules.find(variant)) > len(rules.find(value)):
            value = variant
    return value


def safe_name(value, rules):
    cleaned = rules.replace(name_variant(value, rules))[0]
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned).strip(" .")
    if cleaned.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"{kind}{i}" for kind in ("COM", "LPT") for i in range(1, 10))}:
        cleaned = "_" + cleaned
    return cleaned[:100].rstrip(" .") or "dokument"


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
