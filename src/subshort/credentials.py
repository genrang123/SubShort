from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CREDENTIAL_FIELDS = ("openai_api_key", "gemini_api_key", "openai_compatible_api_key")

_LOAD_ERROR = "Không đọc được credential được bảo vệ; dữ liệu hiện có được giữ nguyên"
_SAVE_ERROR = "Không lưu được credential được bảo vệ; dữ liệu hiện có được giữ nguyên"
_WINDOWS_ERROR = "Credential được bảo vệ chỉ hỗ trợ trên Windows"
_MIGRATE_ERROR = "Không migrate được credential; dữ liệu hiện có được giữ nguyên"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CredentialError(Exception):
    pass


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL


def _dpapi(data: bytes, *, protect: bool, error: str) -> bytes:
    if sys.platform != "win32":
        raise CredentialError(_WINDOWS_ERROR)
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    try:
        call = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        ok = call(ctypes.byref(source), None, None, None, None,
                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target))
    except Exception:
        raise CredentialError(error) from None
    if not ok:
        raise CredentialError(error)
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class CredentialStore:
    def __init__(self, root: Path):
        self.path = Path(root) / "config" / "credentials.dpapi"

    def load(self) -> dict[str, str]:
        if sys.platform != "win32":
            raise CredentialError(_WINDOWS_ERROR)
        try:
            if not self.path.exists():
                return {field: "" for field in CREDENTIAL_FIELDS}
            payload = _dpapi(self.path.read_bytes(), protect=False, error=_LOAD_ERROR)
            data = json.loads(payload.decode("utf-8"))
        except CredentialError:
            raise
        except Exception:
            raise CredentialError(_LOAD_ERROR) from None
        if not isinstance(data, dict):
            raise CredentialError(_LOAD_ERROR)
        result = {}
        for field in CREDENTIAL_FIELDS:
            value = data.get(field, "")
            if not isinstance(value, str):
                raise CredentialError(_LOAD_ERROR)
            result[field] = value
        return result

    def save(self, values: dict[str, Any]) -> None:
        if sys.platform != "win32":
            raise CredentialError(_WINDOWS_ERROR)
        if not isinstance(values, dict):
            raise CredentialError(_SAVE_ERROR)
        # Validate an existing store before replacing it; a corrupt file is never overwritten.
        existing = self.load()
        payload = {field: existing[field] for field in CREDENTIAL_FIELDS}
        for field in CREDENTIAL_FIELDS:
            if field in values:
                if not isinstance(values[field], str):
                    raise CredentialError(_SAVE_ERROR)
                payload[field] = values[field]
        try:
            encrypted = _dpapi(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                protect=True,
                error=_SAVE_ERROR,
            )
            _write_atomic(self.path, encrypted)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError(_SAVE_ERROR) from None


__all__ = ["CREDENTIAL_FIELDS", "CredentialError", "CredentialStore", "_MIGRATE_ERROR"]
