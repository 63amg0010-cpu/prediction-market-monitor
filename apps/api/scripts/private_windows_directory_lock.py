"""Hold a verified Windows directory handle without delete sharing."""

# pyright: reportAny=false, reportDeprecated=false, reportUnannotatedClassAttribute=false, reportUnusedCallResult=false
# ruff: noqa: E501, EM101, PTH100, TC003

from __future__ import annotations

import contextlib
import ctypes
import os
import stat
import sys
from collections.abc import Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Final

FILE_READ_ATTRIBUTES: Final = 0x0080
DELETE_ACCESS: Final = 0x00010000
FILE_SHARE_READ: Final = 0x00000001
FILE_SHARE_WRITE: Final = 0x00000002
OPEN_EXISTING: Final = 3
FILE_FLAG_BACKUP_SEMANTICS: Final = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x00000400
FILE_ATTRIBUTE_TAG_INFO_CLASS: Final = 9
FILE_NAME_NORMALIZED: Final = 0x0
VOLUME_NAME_DOS: Final = 0x0
INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value


class DirectoryLockError(RuntimeError):
    """Raised when an owner-private directory cannot be locked safely."""


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    ]


def _normalized_final_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


@contextlib.contextmanager
def hold_private_directory(path: Path) -> Iterator[None]:
    """Block rename/delete replacement while a validated directory is in use."""
    if os.name != "nt":
        status = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(status.st_mode) or path.is_symlink():
            raise DirectoryLockError("private_directory_invalid")
        yield
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_info.restype = wintypes.BOOL
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    absolute = os.path.abspath(path)
    handle = create_file(
        absolute,
        FILE_READ_ATTRIBUTES | DELETE_ACCESS,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        raise DirectoryLockError("private_directory_lock_failed") from OSError(error)
    try:
        info = _FileAttributeTagInfo()
        if not get_info(
            handle,
            FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            raise DirectoryLockError("private_directory_inspection_failed") from OSError(
                error
            )
        if info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise DirectoryLockError("private_directory_reparse_forbidden")
        required = get_final_path(
            handle,
            None,
            0,
            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
        )
        if required == 0:
            error = ctypes.get_last_error()
            raise DirectoryLockError("private_directory_inspection_failed") from OSError(
                error
            )
        buffer = ctypes.create_unicode_buffer(required + 1)
        written = get_final_path(
            handle,
            buffer,
            len(buffer),
            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS,
        )
        if written == 0 or written >= len(buffer):
            error = ctypes.get_last_error()
            raise DirectoryLockError("private_directory_inspection_failed") from OSError(
                error
            )
        if _normalized_final_path(buffer.value) != _normalized_final_path(absolute):
            raise DirectoryLockError("private_directory_alias_detected")
        yield
    finally:
        _ = close_handle(handle)


def main() -> int:
    """Hold the private directory until the parent process closes stdin."""
    raw_path = os.environ.get("PROVIDER_CAPTURE_LOCK_PATH")
    if not raw_path:
        return 2
    try:
        with hold_private_directory(Path(raw_path)):
            sys.stdout.write("READY\n")
            sys.stdout.flush()
            _ = sys.stdin.buffer.read(1)
    except (DirectoryLockError, OSError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
