"""Race-resistant filesystem I/O inside configured CAD roots.
Wing: code | Topic: filesystem-containment | Updated: 2026-09-15 16:45

Provider-owned filesystem operations use held directory handles/descriptors for the actual I/O
boundary. AutoCAD COM operations that only accept path strings cannot inherit this guarantee.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from .config import Settings
from .security import revalidate_side_effect_path

_TEMP_TOKEN_BYTES = 12


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _root_and_relative(path: Path, settings: Settings) -> tuple[Path, tuple[str, ...]]:
    root = next((candidate for candidate in settings.allowed_paths if _inside(path, candidate)), None)
    if root is None:
        raise ValueError("path is outside CDT_AUTOCAD_ALLOWED_PATHS")
    relative = path.relative_to(root)
    if not relative.parts:
        raise ValueError("filesystem side effect must target a child of an allowed root")
    return root, relative.parts


class _PosixAnchor:
    def __init__(self, path: Path, settings: Settings) -> None:
        root, relative = _root_and_relative(path, settings)
        self._root = root
        self._parent_parts = relative[:-1]
        self.name = relative[-1]
        self._parent_fd = self._open_parent()
        self._parent_identity = self._identity(self._parent_fd)

    @staticmethod
    def _identity(fd: int) -> tuple[int, int]:
        info = os.fstat(fd)
        return int(info.st_dev), int(info.st_ino)

    def _open_parent(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        current = os.open(self._root, flags)
        try:
            for part in self._parent_parts:
                child = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = child
            return current
        except Exception:
            os.close(current)
            raise

    def assert_parent_current(self) -> None:
        try:
            current = self._open_parent()
        except OSError as exc:
            raise ValueError("path parent namespace changed before filesystem side effect") from exc
        try:
            if self._identity(current) != self._parent_identity:
                raise ValueError("path parent namespace changed before filesystem side effect")
        finally:
            os.close(current)

    def open_existing(self) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.name, flags, dir_fd=self._parent_fd)
        except OSError as exc:
            raise ValueError("contained source changed before filesystem read") from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise ValueError("contained source is not a regular file")
        return fd

    def open_existing_for_move(self) -> int:
        return self.open_existing()

    def create_temp(self, temp_name: str) -> int:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(temp_name, flags, 0o600, dir_fd=self._parent_fd)

    def ensure_directory(self) -> None:
        self.assert_parent_current()
        try:
            os.mkdir(self.name, 0o700, dir_fd=self._parent_fd)
        except FileExistsError:
            pass
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            child = os.open(self.name, flags, dir_fd=self._parent_fd)
        except OSError as exc:
            raise ValueError("contained directory is not a safe directory") from exc
        try:
            self.assert_parent_current()
            current = os.stat(self.name, dir_fd=self._parent_fd, follow_symlinks=False)
            bound = os.fstat(child)
            if (int(current.st_dev), int(current.st_ino)) != (
                int(bound.st_dev),
                int(bound.st_ino),
            ):
                raise ValueError("contained directory entry changed during creation")
        finally:
            os.close(child)

    def commit_temp(self, fd: int, temp_name: str) -> None:
        self.assert_parent_current()
        os.replace(
            temp_name,
            self.name,
            src_dir_fd=self._parent_fd,
            dst_dir_fd=self._parent_fd,
        )
        os.fsync(self._parent_fd)
        try:
            self.assert_parent_current()
            self.assert_current_file(fd)
        except Exception:
            self.remove_bound_file(fd)
            raise

    def assert_current_file(self, fd: int) -> None:
        try:
            current = os.stat(self.name, dir_fd=self._parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("contained file entry changed during filesystem I/O") from exc
        bound = os.fstat(fd)
        if (int(current.st_dev), int(current.st_ino)) != (int(bound.st_dev), int(bound.st_ino)):
            raise ValueError("contained file entry changed during filesystem I/O")

    def remove_temp(self, temp_name: str) -> None:
        try:
            os.unlink(temp_name, dir_fd=self._parent_fd)
        except FileNotFoundError:
            pass

    def remove_bound_file(self, fd: int) -> None:
        try:
            current = os.stat(self.name, dir_fd=self._parent_fd, follow_symlinks=False)
            bound = os.fstat(fd)
            if (int(current.st_dev), int(current.st_ino)) == (int(bound.st_dev), int(bound.st_ino)):
                os.unlink(self.name, dir_fd=self._parent_fd)
                os.fsync(self._parent_fd)
        except FileNotFoundError:
            pass

    def replace_existing_to(self, fd: int, destination: Any) -> None:
        self.assert_parent_current()
        destination.assert_parent_current()
        os.replace(
            self.name,
            destination.name,
            src_dir_fd=self._parent_fd,
            dst_dir_fd=destination._parent_fd,
        )
        os.fsync(self._parent_fd)
        if destination._parent_fd != self._parent_fd:
            os.fsync(destination._parent_fd)
        self.assert_parent_current()
        destination.assert_parent_current()
        destination.assert_current_file(fd)

    def close(self) -> None:
        os.close(self._parent_fd)


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_READ_DATA = 0x0001
    _FILE_WRITE_DATA = 0x0002
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_WRITE_ATTRIBUTES = 0x0100
    _DELETE = 0x00010000
    _SYNCHRONIZE = 0x00100000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_OPEN_IF = 3
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _FILE_RENAME_INFO_CLASS = 3
    _FILE_RENAME_INFORMATION_CLASS = 10
    _FILE_DISPOSITION_INFO_CLASS = 4
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("StatusOrPointer", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class _FileTime(ctypes.Structure):
        _fields_ = [("LowDateTime", wintypes.DWORD), ("HighDateTime", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("CreationTime", _FileTime),
            ("LastAccessTime", _FileTime),
            ("LastWriteTime", _FileTime),
            ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD),
            ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD),
            ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        ]

    class _FileRenameInfoHead(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _ntdll.NtCreateFile.restype = ctypes.c_long
    _ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.c_int,
    ]
    _ntdll.NtSetInformationFile.restype = ctypes.c_long
    _ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG

    def _win_close(handle: int) -> None:
        if handle and handle != _INVALID_HANDLE_VALUE:
            _kernel32.CloseHandle(wintypes.HANDLE(handle))

    def _win_raise_ntstatus(status: int, *, context: str) -> None:
        error = int(_ntdll.RtlNtStatusToDosError(ctypes.c_long(status)))
        exc = ctypes.WinError(error)
        raise OSError(error, f"{context}: {exc.strerror}")

    def _win_assert_not_reparse(handle: int) -> None:
        info = _FileAttributeTagInfo()
        ok = _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        if info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("path contains a symlink/reparse/junction component")

    def _win_identity(handle: int) -> tuple[int, int, int]:
        info = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            int(info.VolumeSerialNumber),
            int(info.FileIndexHigh),
            int(info.FileIndexLow),
        )

    def _win_open_root(root: Path) -> int:
        handle = _kernel32.CreateFileW(
            str(root),
            _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = int(handle) if handle else 0
        if not value or value == _INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            _win_assert_not_reparse(value)
            return value
        except Exception:
            _win_close(value)
            raise

    def _win_nt_open_relative(
        root_handle: int,
        name: str,
        *,
        desired_access: int,
        disposition: int,
        directory: bool,
    ) -> int:
        buffer = ctypes.create_unicode_buffer(name)
        encoded_length = len(name.encode("utf-16-le"))
        unicode_name = _UnicodeString(
            encoded_length,
            encoded_length + ctypes.sizeof(wintypes.WCHAR),
            ctypes.cast(buffer, wintypes.LPWSTR),
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes),
            wintypes.HANDLE(root_handle),
            ctypes.pointer(unicode_name),
            _OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        io_status = _IoStatusBlock()
        handle = wintypes.HANDLE()
        options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
        options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
        status = int(
            _ntdll.NtCreateFile(
                ctypes.byref(handle),
                desired_access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                _FILE_ATTRIBUTE_NORMAL,
                _FILE_SHARE_ALL,
                disposition,
                options,
                None,
                0,
            )
        )
        if status < 0:
            _win_raise_ntstatus(status, context=f"contained open failed for {name}")
        value = int(handle.value)
        try:
            _win_assert_not_reparse(value)
            return value
        except Exception:
            _win_close(value)
            raise

    def _win_mark_delete(handle: int) -> None:
        info = _FileDispositionInfo(True)
        if not _kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle),
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)

    def _win_rename_relative(handle: int, parent_handle: int, name: str) -> None:
        encoded = name.encode("utf-16-le")
        offset = _FileRenameInfoHead.FileName.offset
        size = offset + len(encoded)
        raw = ctypes.create_string_buffer(size)
        head = ctypes.cast(raw, ctypes.POINTER(_FileRenameInfoHead)).contents
        head.ReplaceIfExists = 1
        head.RootDirectory = wintypes.HANDLE(parent_handle)
        head.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(raw) + offset, encoded, len(encoded))
        io_status = _IoStatusBlock()
        status = int(
            _ntdll.NtSetInformationFile(
                wintypes.HANDLE(handle),
                ctypes.byref(io_status),
                ctypes.cast(raw, wintypes.LPVOID),
                size,
                _FILE_RENAME_INFORMATION_CLASS,
            )
        )
        if status < 0:
            _win_raise_ntstatus(status, context=f"contained rename failed for {name}")

    class _WindowsAnchor:
        def __init__(self, path: Path, settings: Settings) -> None:
            root, relative = _root_and_relative(path, settings)
            self._root = root
            self._parent_parts = relative[:-1]
            self.name = relative[-1]
            self._parent_handle = self._open_parent()
            self._parent_identity = _win_identity(self._parent_handle)

        def _open_parent(self) -> int:
            current = _win_open_root(self._root)
            try:
                for part in self._parent_parts:
                    child = _win_nt_open_relative(
                        current,
                        part,
                        desired_access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                        disposition=_FILE_OPEN,
                        directory=True,
                    )
                    _win_close(current)
                    current = child
                return current
            except Exception:
                _win_close(current)
                raise

        def assert_parent_current(self) -> None:
            try:
                current = self._open_parent()
            except (OSError, ValueError) as exc:
                raise ValueError("path parent namespace changed before filesystem side effect") from exc
            try:
                if _win_identity(current) != self._parent_identity:
                    raise ValueError("path parent namespace changed before filesystem side effect")
            finally:
                _win_close(current)

        def open_existing(self) -> int:
            handle = _win_nt_open_relative(
                self._parent_handle,
                self.name,
                desired_access=_FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                disposition=_FILE_OPEN,
                directory=False,
            )
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except Exception:
                _win_close(handle)
                raise

        def open_existing_for_move(self) -> int:
            handle = _win_nt_open_relative(
                self._parent_handle,
                self.name,
                desired_access=_FILE_READ_ATTRIBUTES | _DELETE | _SYNCHRONIZE,
                disposition=_FILE_OPEN,
                directory=False,
            )
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except Exception:
                _win_close(handle)
                raise

        def create_temp(self, temp_name: str) -> int:
            handle = _win_nt_open_relative(
                self._parent_handle,
                temp_name,
                desired_access=(
                    _FILE_READ_DATA
                    | _FILE_WRITE_DATA
                    | _FILE_READ_ATTRIBUTES
                    | _FILE_WRITE_ATTRIBUTES
                    | _DELETE
                    | _SYNCHRONIZE
                ),
                disposition=_FILE_CREATE,
                directory=False,
            )
            try:
                return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except Exception:
                _win_close(handle)
                raise

        def ensure_directory(self) -> None:
            self.assert_parent_current()
            try:
                child = _win_nt_open_relative(
                    self._parent_handle,
                    self.name,
                    desired_access=_FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                    disposition=_FILE_OPEN_IF,
                    directory=True,
                )
            except OSError as exc:
                raise ValueError("contained directory is not a safe directory") from exc
            try:
                self.assert_parent_current()
            finally:
                _win_close(child)

        def commit_temp(self, fd: int, temp_name: str) -> None:
            del temp_name
            self.assert_parent_current()
            handle = int(msvcrt.get_osfhandle(fd))
            _win_rename_relative(handle, self._parent_handle, self.name)
            try:
                self.assert_parent_current()
                self.assert_current_file(fd)
            except Exception:
                self.remove_bound_file(fd)
                raise

        def assert_current_file(self, fd: int) -> None:
            bound_handle = int(msvcrt.get_osfhandle(fd))
            current = _win_nt_open_relative(
                self._parent_handle,
                self.name,
                desired_access=_FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
                disposition=_FILE_OPEN,
                directory=False,
            )
            try:
                if _win_identity(current) != _win_identity(bound_handle):
                    raise ValueError("contained file entry changed during filesystem I/O")
            finally:
                _win_close(current)

        def remove_temp(self, temp_name: str) -> None:
            del temp_name

        def remove_bound_file(self, fd: int) -> None:
            handle = int(msvcrt.get_osfhandle(fd))
            _win_mark_delete(handle)

        def replace_existing_to(self, fd: int, destination: Any) -> None:
            self.assert_parent_current()
            destination.assert_parent_current()
            handle = int(msvcrt.get_osfhandle(fd))
            _win_rename_relative(handle, destination._parent_handle, destination.name)
            self.assert_parent_current()
            destination.assert_parent_current()
            destination.assert_current_file(fd)

        def close(self) -> None:
            _win_close(self._parent_handle)

else:
    _WindowsAnchor = None  # type: ignore[assignment,misc]


def _anchor(path: Path, settings: Settings) -> Any:
    if os.name == "nt":
        assert _WindowsAnchor is not None
        return _WindowsAnchor(path, settings)
    return _PosixAnchor(path, settings)


def _mode(*, binary: bool, writing: bool) -> str:
    if binary:
        return "wb" if writing else "rb"
    return "w" if writing else "r"


@contextlib.contextmanager
def open_contained_reader(
    expected: str | Path,
    settings: Settings,
    *,
    binary: bool,
    encoding: str = "utf-8",
    enforce_size_limit: bool = True,
) -> Iterator[IO[Any]]:
    """Open an existing regular file through a path-bound directory handle/descripor chain."""
    path = revalidate_side_effect_path(expected, settings, must_exist=True)
    anchor = _anchor(path, settings)
    fd = -1
    stream: IO[Any] | None = None
    try:
        fd = anchor.open_existing()
        info = os.fstat(fd)
        if enforce_size_limit and info.st_size > settings.max_dxf_bytes:
            raise ValueError(
                f"CAD document exceeds configured size limit ({info.st_size} > {settings.max_dxf_bytes})"
            )
        kwargs = {} if binary else {"encoding": encoding, "newline": ""}
        stream = os.fdopen(fd, _mode(binary=binary, writing=False), **kwargs)
        fd = -1
        yield stream
        anchor.assert_parent_current()
        anchor.assert_current_file(stream.fileno())
    finally:
        if stream is not None:
            stream.close()
        elif fd >= 0:
            os.close(fd)
        anchor.close()


@contextlib.contextmanager
def open_contained_atomic_writer(
    expected: str | Path,
    settings: Settings,
    *,
    binary: bool,
    encoding: str = "utf-8",
) -> Iterator[IO[Any]]:
    """Atomically replace one contained file through a held parent directory handle/descriptor."""
    path = revalidate_side_effect_path(
        expected,
        settings,
        must_exist=False,
        for_write=True,
    )
    anchor = _anchor(path, settings)
    temp_name = f".{path.name}.cdt-{secrets.token_hex(_TEMP_TOKEN_BYTES)}.tmp"
    fd = -1
    stream: IO[Any] | None = None
    committed = False
    try:
        fd = anchor.create_temp(temp_name)
        kwargs = {} if binary else {"encoding": encoding, "newline": ""}
        stream = os.fdopen(fd, _mode(binary=binary, writing=True), **kwargs)
        fd = -1
        yield stream
        stream.flush()
        os.fsync(stream.fileno())
        anchor.commit_temp(stream.fileno(), temp_name)
        committed = True
    finally:
        if not committed and stream is not None:
            try:
                anchor.remove_bound_file(stream.fileno())
            finally:
                anchor.remove_temp(temp_name)
        elif not committed:
            anchor.remove_temp(temp_name)
        if stream is not None:
            stream.close()
        elif fd >= 0:
            os.close(fd)
        anchor.close()


def read_contained_bytes(expected: str | Path, settings: Settings) -> bytes:
    with open_contained_reader(expected, settings, binary=True) as stream:
        return bytes(stream.read())


def contained_sha256_metadata(
    expected: str | Path,
    settings: Settings,
    *,
    enforce_size_limit: bool = True,
) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    with open_contained_reader(
        expected,
        settings,
        binary=True,
        enforce_size_limit=enforce_size_limit,
    ) as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        info = os.fstat(stream.fileno())
        size = int(info.st_size)
        mtime_ns = int(info.st_mtime_ns)
    return digest.hexdigest(), size, mtime_ns


def copy_contained_file(
    source: str | Path,
    destination: str | Path,
    settings: Settings,
) -> None:
    with open_contained_reader(source, settings, binary=True) as reader:
        with open_contained_atomic_writer(destination, settings, binary=True) as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)


def ensure_contained_directory(expected: str | Path, settings: Settings) -> Path:
    path = revalidate_side_effect_path(
        expected,
        settings,
        must_exist=False,
        for_write=True,
    )
    anchor = _anchor(path, settings)
    try:
        anchor.ensure_directory()
    finally:
        anchor.close()
    return revalidate_side_effect_path(path, settings, must_exist=False)


def replace_contained_file(
    source: str | Path,
    destination: str | Path,
    settings: Settings,
) -> Path:
    source_path = revalidate_side_effect_path(source, settings, must_exist=True)
    destination_path = revalidate_side_effect_path(
        destination,
        settings,
        must_exist=False,
        for_write=True,
    )
    if source_path == destination_path:
        return destination_path
    source_anchor = _anchor(source_path, settings)
    destination_anchor = _anchor(destination_path, settings)
    fd = -1
    try:
        fd = source_anchor.open_existing_for_move()
        source_anchor.replace_existing_to(fd, destination_anchor)
    finally:
        if fd >= 0:
            os.close(fd)
        source_anchor.close()
        destination_anchor.close()
    return revalidate_side_effect_path(destination_path, settings, must_exist=True)


def write_contained_text_atomic(
    expected: str | Path,
    text: str,
    settings: Settings,
    *,
    encoding: str = "utf-8",
) -> None:
    with open_contained_atomic_writer(expected, settings, binary=False, encoding=encoding) as stream:
        stream.write(text)
