import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from uuid import uuid4
from collections import Counter

VERSION = "0.8.0"


class Audit:
    def __init__(self, root):
        self.id = uuid4().hex
        self.counts = Counter()
        self.errors = Counter()
        self.fallback = False
        self.backup = Path(tempfile.gettempdir()) / "SpisRedakt" / "audit" / self.id
        self.on_fallback = None
        for folder in (Path(root), self.backup.parent):
            try:
                self.path = folder / self.id
                self.path.mkdir(parents=True, exist_ok=False)
                self.record("started", phase="batch")
                break
            except OSError:
                self.fallback = True
        else:
            raise ValueError("Audit nelze zapsat ani do náhradní místní složky.")

    def record(self, status, **fields):
        event = dict(run_id=self.id, version=VERSION, utc=datetime.now(timezone.utc).isoformat(),
                     status=status, **fields)
        line = json.dumps(event, ensure_ascii=True) + "\n"
        try:
            self.append(line)
        except OSError:
            if self.path == self.backup:
                raise
            self.backup.mkdir(parents=True, exist_ok=True)
            self.path = self.backup
            self.fallback = True
            self.append(json.dumps(dict(run_id=self.id, version=VERSION,
                                        status="audit_storage_fallback", phase="audit")) + "\n")
            self.append(line)
            if self.on_fallback:
                self.on_fallback(self.path, True)
        if status in {"released", "failed", "skipped"}:
            self.counts[status] += 1
        if status == "failed":
            self.errors[fields["code"]] += 1

    def append(self, line):
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, completed):
        self.record("completed" if completed else "interrupted", phase="batch",
                    counts=self.counts, errors=self.errors)
        summary = "\n".join(f"{label}: {self.counts[key]}" for key, label in
                            (("released", "Vydáno"), ("failed", "Chyby"), ("skipped", "Přeskočeno")))
        (self.path / "souhrn.txt").write_text(
            f"SpisRedakt {VERSION} · {self.id}\n"
            + ("Dávka dokončena.\n" if completed else "Dávka přerušena.\n") + summary + "\n"
            + "".join(f"{code}: {count}\n" for code, count in self.errors.items()),
            encoding="utf-8")
