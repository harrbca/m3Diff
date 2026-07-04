"""Core types and errors for the binary M3 export format (spec §2).

A decoded row is a plain ``dict[str, str]`` holding **only the fields present**
in that row's null bitmap. A field absent from the dict is null/default; a field
mapped to ``""`` is a present, zero-length value. The format distinguishes these
two states on the wire, so we keep them distinct here — see spec §2.1.
"""
from __future__ import annotations

from dataclasses import dataclass

# A decoded row: present fields only. Missing key == null/absent.
Row = dict[str, str]


class ExportFormatError(Exception):
    """Base for any binary-export decoding failure.

    Callers diff table-by-table and catch this per table (spec F6): one
    undecodable table records an error and the run continues.
    """


class HeaderError(ExportFormatError):
    """The table header (column descriptors) is malformed or missing."""


class TruncatedExportError(ExportFormatError):
    """The stream ended in the middle of a structure we were reading."""


class RowLengthError(ExportFormatError):
    """A row consumed a number of bytes other than its declared length.

    The per-row length prefix is the format's built-in checksum (spec §2.1);
    a mismatch means the bytes are corrupt or misaligned.
    """


@dataclass(frozen=True, slots=True)
class Field:
    """One column descriptor: ``type;name;maxlen;flag`` from the header.

    The parts are kept as raw strings (faithful to the export, which stores
    everything as text); ``type_code`` / ``max_length`` give typed access when
    the values are numeric, and ``None`` when they are not.
    """

    type: str
    name: str
    maxlen: str
    flag: str

    @property
    def type_code(self) -> int | None:
        """JDBC SQL type code (4 = INTEGER, 12 = VARCHAR), or None if non-numeric."""
        try:
            return int(self.type)
        except ValueError:
            return None

    @property
    def max_length(self) -> int | None:
        try:
            return int(self.maxlen)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class TableHeader:
    """The parsed header of one table export: its columns, in wire order."""

    fields: tuple[Field, ...]

    @property
    def nfields(self) -> int:
        return len(self.fields)

    @property
    def bitmap_bytes(self) -> int:
        """Width of each row's null bitmap: ceil(nfields / 8)."""
        return (self.nfields + 7) // 8

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def cono_field_indexes(self) -> list[int]:
        """Indexes of columns that look like the company column.

        Heuristic (spec §2.1): a 6-character field name ending in ``cono``
        (case-insensitive), e.g. ``mmcono``, ``okcono``. Returns **all** matches
        so the caller can flag ambiguity when more than one column qualifies.
        """
        return [
            i
            for i, f in enumerate(self.fields)
            if len(f.name) == 6 and f.name.lower().endswith("cono")
        ]
