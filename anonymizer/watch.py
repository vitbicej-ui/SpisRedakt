"""Decides which input files are ready to be processed. No GUI dependency, so it is testable."""
import hashlib
import os
from pathlib import Path

TRANSIENT = {"PermissionError", "FileNotFoundError", "OSError", "TimeoutError", "BlockingIOError"}
MAX_RETRIES = 5


class Watcher:
    """A file is processed once per content version (size and modification time), after it has
    stayed unchanged for two consecutive scans and can be opened for reading.

    Deleting an output does not trigger a new run while the input is unchanged; that is what
    the explicit "process again" action (reset) is for. A renamed or moved input is a new file.
    """

    def __init__(self, handled=None):
        self.handled = dict(handled or {})
        self.observed = {}
        self.retries = {}

    def reset(self):
        self.handled.clear()
        self.retries.clear()

    def forget_observations(self):
        self.observed.clear()

    def scan(self, source, output):
        """Return {path: (key, signature)} of files ready now. Raises OSError/ValueError
        when the folders are unavailable or not separate."""
        source, output = Path(source).resolve(strict=True), Path(output).resolve(strict=True)
        if (not source.is_dir() or not output.is_dir() or source == output
                or source.is_relative_to(output) or output.is_relative_to(source)):
            raise ValueError("Vyberte dvě oddělené složky.")
        current, pending = {}, {}
        for path in source.rglob("*"):
            if path.name.startswith("~$") or path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = hashlib.sha256(os.fsencode(str(output) + "\0" + str(path))).hexdigest()
            signature = f"{stat.st_size}:{stat.st_mtime_ns}"
            current[key] = signature
            if self.observed.get(key) == signature and self.handled.get(key) != signature and readable(path):
                pending[path] = (key, signature)
        self.observed = current
        return pending

    def result(self, key, signature, error=""):
        """Record the outcome. A transient failure (file still locked) is retried a few times."""
        if error in TRANSIENT:
            self.retries[key] = self.retries.get(key, 0) + 1
            if self.retries[key] < MAX_RETRIES:
                return False
        self.retries.pop(key, None)
        self.handled[key] = signature
        return True


def readable(path):
    try:
        with open(path, "rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False
