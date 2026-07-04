"""Access to an export: a zip file or a directory of table dumps.

An ``ExportSource`` enumerates the tables present (a table is identified by name
only — the binary header carries no component), opens a per-table binary stream
lazily, and reads the optional ``TABLE_INFO`` manifest. It never loads a whole
export into memory; callers stream one table at a time.

Ports the zip/directory handling of ``reference/classify_export.py`` into an
addressable source (open a table by name, not just iterate).
"""
from __future__ import annotations

import os
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from .format import TableInfoEntry, TableInfoError, parse_table_info

_TABLE_INFO = "TABLE_INFO"


def _is_table_info(name: str) -> bool:
    return name.upper() == _TABLE_INFO


class ExportSource(ABC):
    """A set of named table streams plus an optional TABLE_INFO manifest."""

    @abstractmethod
    def table_names(self) -> list[str]:
        """Table names present, sorted, excluding TABLE_INFO."""

    @abstractmethod
    def open_table(self, name: str) -> BinaryIO:
        """Open a binary stream for one table. Caller closes it (or uses `with`)."""

    @abstractmethod
    def _table_info_bytes(self) -> bytes | None:
        """Raw TABLE_INFO bytes, or None if the export has none."""

    def table_info(self) -> list[TableInfoEntry] | None:
        """The parsed manifest, or None if absent or unparseable.

        The manifest is advisory (spec §2.2), so an unparseable one degrades to
        None rather than failing the whole export.
        """
        raw = self._table_info_bytes()
        if raw is None:
            return None
        try:
            return parse_table_info(raw)
        except TableInfoError:
            return None

    def close(self) -> None:  # noqa: B027 - optional override
        """Release any held resources (no-op by default)."""

    def __enter__(self) -> "ExportSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ZipExportSource(ExportSource):
    """An export packaged as a zip. Accepts a path or an open binary file."""

    def __init__(self, file: str | os.PathLike[str] | BinaryIO) -> None:
        self._zip = zipfile.ZipFile(file)
        self._entries: dict[str, zipfile.ZipInfo] = {}
        self._table_info: str | None = None
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            base = os.path.basename(info.filename)
            if not base:
                continue
            if _is_table_info(base):
                self._table_info = info.filename
                continue
            self._entries[base] = info  # last wins on a basename collision

    def table_names(self) -> list[str]:
        return sorted(self._entries)

    def open_table(self, name: str) -> BinaryIO:
        try:
            info = self._entries[name]
        except KeyError:
            raise KeyError(f"table {name!r} not in export") from None
        return self._zip.open(info)

    def _table_info_bytes(self) -> bytes | None:
        if self._table_info is None:
            return None
        with self._zip.open(self._table_info) as stream:
            return stream.read()

    def close(self) -> None:
        self._zip.close()


class DirectoryExportSource(ExportSource):
    """An export already unpacked into a directory (one file per table)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._dir = Path(path)
        self._files: dict[str, Path] = {}
        self._table_info: str | None = None
        for entry in sorted(self._dir.iterdir()):
            if not entry.is_file():
                continue
            if _is_table_info(entry.name):
                self._table_info = entry.name
                continue
            self._files[entry.name] = entry

    def table_names(self) -> list[str]:
        return sorted(self._files)

    def open_table(self, name: str) -> BinaryIO:
        try:
            path = self._files[name]
        except KeyError:
            raise KeyError(f"table {name!r} not in export") from None
        return path.open("rb")

    def _table_info_bytes(self) -> bytes | None:
        if self._table_info is None:
            return None
        return (self._dir / self._table_info).read_bytes()


def open_export(path: str | os.PathLike[str]) -> ExportSource:
    """Open an export by path — a directory or a zip file."""
    resolved = Path(path)
    if resolved.is_dir():
        return DirectoryExportSource(resolved)
    if zipfile.is_zipfile(resolved):
        return ZipExportSource(resolved)
    raise ValueError(f"{os.fspath(path)!r} is neither a directory nor a zip export")
