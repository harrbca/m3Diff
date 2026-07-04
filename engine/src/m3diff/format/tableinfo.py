"""Deserializer for the ``TABLE_INFO`` catalog (spec §2.2).

``TABLE_INFO`` is a Java-serialized ``ArrayList`` of
``gridaccess.client.tools.proxy.ToolProxy$TableInfo`` objects, each
``{long noRecords; String tableName}``. Rather than implement a full Java
deserializer, we scan for the same stable byte markers the verified reference
(``reference/parse_tableinfo.py``) keys on:

- ``"sizexp"`` — the ArrayList class descriptor's ``size`` field name (``size``)
  followed by TC_ENDBLOCKDATA (0x78 = 'x') and TC_NULL (0x70 = 'p'); the 4-byte
  list size follows immediately after.
- ``"Ljava/lang/String;xp"`` — the end of the TableInfo class descriptor; the
  first element's field values (``long`` then ``String``) follow.
- ``sq \x00\x7e\x00\x02`` — TC_OBJECT + TC_REFERENCE to the TableInfo class
  descriptor handle (0x7E0002); each subsequent element starts this way.

Used only as a manifest (which tables exist / are non-empty). Its counts are
snapshot-time, all-companies totals and must NOT be trusted for per-company
validation (spec §2.2).
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from .types import ExportFormatError

_STREAM_MAGIC = b"\xac\xed\x00\x05"
_STRING_ANCHOR = b"Ljava/lang/String;xp"
_ELEMENT_RE = re.compile(rb"sq\x00\x7e\x00\x02(.{8})\x74(..)", re.DOTALL)


class TableInfoError(ExportFormatError):
    """The TABLE_INFO stream is missing or not in the expected shape."""


@dataclass(frozen=True, slots=True)
class TableInfoEntry:
    table_name: str
    record_count: int


def parse_table_info(data: bytes) -> list[TableInfoEntry]:
    """Parse a ``TABLE_INFO`` blob into ordered ``(table_name, record_count)`` entries."""
    if not data.startswith(_STREAM_MAGIC):
        raise TableInfoError("not a Java serialization stream (bad magic)")

    marker = data.find(b"sizexp")
    if marker < 0:
        raise TableInfoError("ArrayList 'size' marker not found")
    try:
        (declared,) = struct.unpack(">i", data[marker + 6 : marker + 10])
    except struct.error as exc:
        raise TableInfoError("truncated before ArrayList size") from exc

    anchor = data.find(_STRING_ANCHOR)
    if anchor < 0:
        if declared == 0:
            return []
        raise TableInfoError("TableInfo class marker not found")

    entries: list[TableInfoEntry] = []
    try:
        pos = anchor + len(_STRING_ANCHOR)
        # First element: 8-byte long, then TC_STRING (0x74) + 2-byte len + name.
        (count,) = struct.unpack(">q", data[pos : pos + 8])
        pos += 8
        if data[pos] != 0x74:
            raise TableInfoError("expected TC_STRING for the first table name")
        (slen,) = struct.unpack(">H", data[pos + 1 : pos + 3])
        pos += 3
        name = data[pos : pos + slen].decode("utf-8")
        pos += slen
        entries.append(TableInfoEntry(name, count))

        # Subsequent elements reuse the class descriptor via a back-reference.
        while (match := _ELEMENT_RE.match(data, pos)) is not None:
            (count,) = struct.unpack(">q", match.group(1))
            (slen,) = struct.unpack(">H", match.group(2))
            start = match.end()
            name = data[start : start + slen].decode("utf-8")
            entries.append(TableInfoEntry(name, count))
            pos = start + slen
    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        raise TableInfoError(f"malformed TableInfo element: {exc}") from exc

    if declared != len(entries):
        raise TableInfoError(f"declared {declared} entries but parsed {len(entries)}")
    return entries
