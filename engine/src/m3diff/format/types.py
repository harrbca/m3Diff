"""Core types and errors for the binary M3 export format (spec §2).

A decoded row is a plain ``dict[str, str]`` holding **only the fields present**
in that row's null bitmap. A field absent from the dict is null/default.

String columns are **carry-forward compressed** on the wire (spec §2.1,
ADR-026): a column present in a row's bitmap with a **zero-length value means
"same value as this column's last present value"**, not an empty string. The
reader decompresses this transparently, so a decoded row **never contains
``""``** — a genuine blank is bitmap-absent instead. A zero-length value in a
non-string column, or with no prior value to carry, is a format violation
(``CompressionError``).
"""
from __future__ import annotations

from dataclasses import dataclass

# A decoded row: present fields only. Missing key == null/absent.
Row = dict[str, str]

# JDBC SQL type codes for string columns — the only columns that carry-forward
# compress (spec §2.1). VARCHAR (12) is what real headers use; CHAR (1) is
# included defensively (never observed, but would be a string type).
STRING_TYPE_CODES = frozenset({1, 12})


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


class CompressionError(ExportFormatError):
    """A carry-forward marker (present zero-length value) is invalid (ADR-026).

    Raised when a zero-length string value appears in a **non-string column**
    (numerics never compress), or with **no prior value to carry** (an orphan
    marker, e.g. on a column's first present occurrence). Like the row-length
    invariant this is treated as a checksum: a violation raises rather than
    being silently absorbed, so a future writer changing the semantics surfaces
    as a loud per-table ``error`` (spec F6) instead of a silently wrong diff.
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

    @property
    def string_field_flags(self) -> tuple[bool, ...]:
        """Per-column mask: True where the column is string-typed (CHAR/VARCHAR).

        Only string columns carry-forward compress (spec §2.1). Consulted per
        cell in the reader's hot loop, so bind it **once per stream** and index
        the local — do not re-read it per row.
        """
        return tuple(f.type_code in STRING_TYPE_CODES for f in self.fields)

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
