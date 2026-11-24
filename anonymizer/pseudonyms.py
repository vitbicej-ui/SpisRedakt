"""Persistent pseudonyms between batches into the same output folder.

The map ``.spisredakt-mapa`` holds no identities: only HMAC-SHA256 digests of ``kind + identity`` with the
labels and counters that belong to them. The random HMAC key is protected by Windows DPAPI (current user) and
kept next to the map in ``.spisredakt-klic``. Without DPAPI (other systems, an error) or with a damaged map or
key, the map is not used and the batch runs as if there were none. Nothing here logs or raises document content.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

MAP_NAME = ".spisredakt-mapa"
KEY_NAME = ".spisredakt-klic"
INTERNAL_NAMES = {MAP_NAME, KEY_NAME}
FORMAT = 1
LABEL_FORM = re.compile(r"\[[A-Z_]+-\d+\]")
COUNTER_FORM = re.compile(r"[A-Z_]+")


def _dpapi_protect(data):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    if not crypt32.CryptProtectData(ctypes.byref(source), "SpisRedakt", None, None, None, 0x1, ctypes.byref(result)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _dpapi_unprotect(data):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    source = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(result)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _system_protection():
    """(protect, unprotect) of the system, or None where DPAPI does not exist."""
    return (_dpapi_protect, _dpapi_unprotect) if os.name == "nt" else None


# Replaceable by tests; returns (protect, unprotect) or None.
protection = _system_protection


class LabelMap:
    """Digests of identities -> labels. ``open`` returns None whenever the map cannot be used safely."""

    def __init__(self, key, folder, labels=None, initials=None, counters=None, writable=True):
        self.key = key
        self.folder = Path(folder)
        self.labels = labels or {}
        self.initials = initials or {}
        self.counters = counters or {}
        self.writable = writable
        self.changed = False

    @classmethod
    def open(cls, folder):
        try:
            protect = protection()
            if protect is None:
                return None
            protect, unprotect = protect
            folder = Path(folder)
            key_path, map_path = folder / KEY_NAME, folder / MAP_NAME
            if key_path.is_file():
                key = unprotect(key_path.read_bytes())
                if len(key) != 32:
                    return None
            elif map_path.exists():
                return None  # a map without its key cannot be read and is not overwritten
            else:
                key = secrets.token_bytes(32)
                _write(key_path, protect(key))
            if not map_path.is_file():
                return cls(key, folder)
            return cls._load(key, folder, map_path.read_bytes())
        except Exception:
            return None

    @classmethod
    def _load(cls, key, folder, raw):
        try:
            data = json.loads(raw.decode("ascii"))
            body = data["body"]
            if (data["v"] != FORMAT or not isinstance(body, str)
                    or not hmac.compare_digest(data["mac"], hmac.new(key, body.encode("ascii"), "sha256").hexdigest())):
                return None
            content = json.loads(body)
            labels, initials, counters = content["labels"], content["initials"], content["counters"]
            if not (all(isinstance(k, str) and isinstance(v, str) and LABEL_FORM.fullmatch(v)
                        for k, v in labels.items())
                    and all(isinstance(k, str) and isinstance(v, list)
                            and all(isinstance(x, str) and LABEL_FORM.fullmatch(x) for x in v)
                            for k, v in initials.items())
                    and all(COUNTER_FORM.fullmatch(k) and type(v) is int and v >= 0 for k, v in counters.items())):
                return None
        except Exception:
            return None
        return cls(key, folder, labels, {k: list(v) for k, v in initials.items()}, counters)

    def digest(self, *parts):
        return hmac.new(self.key, "\0".join(parts).encode("utf-8"), "sha256").hexdigest()

    def get(self, kind, identity):
        return self.labels.get(self.digest(kind, identity))

    def put(self, kind, identity, label):
        digest = self.digest(kind, identity)
        if self.labels.get(digest) != label:
            self.labels[digest] = label
            self.changed = True

    @staticmethod
    def initial_key(initial, surname):
        return "INITIAL", initial, surname

    def get_initials(self, initial, surname):
        return set(self.initials.get(self.digest(*self.initial_key(initial, surname)), ()))

    def put_initials(self, initial, surname, label):
        entry = self.initials.setdefault(self.digest(*self.initial_key(initial, surname)), [])
        if label not in entry:
            entry.append(label)
            self.changed = True

    def save(self, counts):
        """Write the map (atomically) with counters at least as high as ``counts``; never raises."""
        try:
            counters = dict(self.counters)
            for kind, number in counts.items():
                if number > counters.get(kind, 0):
                    counters[kind] = number
                    self.changed = True
            if not self.changed:
                return False
            body = json.dumps(dict(labels=self.labels, initials=self.initials, counters=counters),
                              sort_keys=True, ensure_ascii=True)
            mac = hmac.new(self.key, body.encode("ascii"), "sha256").hexdigest()
            _write(self.folder / MAP_NAME, json.dumps(dict(v=FORMAT, body=body, mac=mac)).encode("ascii"))
            return True
        except Exception:
            return False


def _write(path, data):
    temporary = path.with_name(path.name + f".{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
