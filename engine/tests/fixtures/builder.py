"""Programmatic builders for synthetic M3 exports — the inverse of the reader.

Everything the tests need is generated here from the format spec (§2), so no
real M3 data ever enters the repo (CLAUDE.md). ``encode_table`` is the exact
inverse of ``m3diff.format.reader`` in both modes:

- ``compress=False`` (default): each present value is written verbatim. Well-
  formed fixtures use ``None`` for blanks (bitmap-absent) and must contain **no
  ``""`` string values** — on the wire a present zero-length string is a
  carry-forward marker, so a literal ``""`` decodes to something else (or an
  error) through the decompressing reader (ADR-026).
- ``compress=True``: string columns are carry-forward compressed — a value equal
  to that column's last present value is emitted as a present zero-length cell.
  Such fixtures round-trip through the (decompressing) reader to their original
  logical rows.

``encode_row`` stays raw and permissive so tests can hand-craft wire-level
violations (orphan ``''``, ``''`` in a numeric column, ``''`` on the first row).
``encode_table_info`` emits a faithful ``java.io.ObjectOutputStream`` encoding of
``ArrayList<TableInfo>`` so the TABLE_INFO deserializer can be round-tripped.
"""
from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Mapping, Sequence

from m3diff.format.types import STRING_TYPE_CODES, Field

_U32 = struct.Struct(">I")

# --- value/row semantics ----------------------------------------------------
# A row is a mapping name -> value. A field mapped to a str (including "") is
# written present; a field absent from the mapping, or mapped to None, is left
# out of the bitmap (null). This mirrors the reader's present-only dicts.
RowSpec = Mapping[str, "str | None"]


def field(name: str, type: str = "12", maxlen: str = "0", flag: str = "") -> Field:
    """Convenience constructor for a column descriptor (defaults to VARCHAR)."""
    return Field(type=type, name=name, maxlen=maxlen, flag=flag)


def encode_header(fields: Sequence[Field]) -> bytes:
    header = "\x01".join(f"{f.type};{f.name};{f.maxlen};{f.flag}" for f in fields)
    raw = header.encode("utf-8")
    return _U32.pack(len(raw)) + raw


def encode_row(fields: Sequence[Field], values: RowSpec) -> bytes:
    names = {f.name for f in fields}
    unknown = set(values) - names
    if unknown:
        raise ValueError(f"values reference unknown columns: {sorted(unknown)}")

    nfields = len(fields)
    bitmap = bytearray((nfields + 7) // 8)
    body = bytearray()
    for i, f in enumerate(fields):
        value = values.get(f.name)
        if value is None:
            continue  # absent from bitmap => null
        bitmap[i >> 3] |= 0x80 >> (i & 7)
        encoded = value.encode("utf-8")
        body += _U32.pack(len(encoded)) + encoded

    payload = bytes(bitmap) + bytes(body)
    return _U32.pack(len(payload)) + payload


def _encode_row_compressed(
    fields: Sequence[Field],
    values: RowSpec,
    carry: list["str | None"],
    string_flags: Sequence[bool],
) -> bytes:
    """Encode one row, carry-forward compressing string columns (ADR-026).

    ``carry`` holds each column's last present value and is mutated in place
    across the table. A present string value equal to its carry is written as a
    zero-length cell (the marker); anything else is written verbatim and updates
    the carry. Absent (``None``) leaves the carry untouched, mirroring the reader.
    """
    names = {f.name for f in fields}
    unknown = set(values) - names
    if unknown:
        raise ValueError(f"values reference unknown columns: {sorted(unknown)}")

    nfields = len(fields)
    bitmap = bytearray((nfields + 7) // 8)
    body = bytearray()
    for i, f in enumerate(fields):
        value = values.get(f.name)
        if value is None:
            continue  # absent from bitmap => null; carry untouched (rule 2)
        if string_flags[i] and value == "":
            # A literal empty string is not representable when compressing: on the
            # wire a zero-length string IS the carry marker. Real blanks are absent.
            raise ValueError(
                f"compressed fixture cannot hold a literal empty string for "
                f"column {f.name!r}; use None (bitmap-absent) for a blank"
            )
        bitmap[i >> 3] |= 0x80 >> (i & 7)
        if string_flags[i] and carry[i] is not None and value == carry[i]:
            body += _U32.pack(0)  # same as last present value => carry marker
        else:
            encoded = value.encode("utf-8")
            body += _U32.pack(len(encoded)) + encoded
            carry[i] = value

    payload = bytes(bitmap) + bytes(body)
    return _U32.pack(len(payload)) + payload


def encode_table(
    fields: Sequence[Field], rows: Sequence[RowSpec], *, compress: bool = False
) -> bytes:
    out = bytearray(encode_header(fields))
    if not compress:
        for row in rows:
            out += encode_row(fields, row)
        return bytes(out)
    string_flags = [f.type_code in STRING_TYPE_CODES for f in fields]
    carry: list[str | None] = [None] * len(fields)
    for row in rows:
        out += _encode_row_compressed(fields, row, carry, string_flags)
    return bytes(out)


# --- TABLE_INFO (Java serialization) ----------------------------------------
_TC_OBJECT = b"\x73"
_TC_CLASSDESC = b"\x72"
_TC_ENDBLOCKDATA = b"\x78"
_TC_NULL = b"\x70"
_TC_REFERENCE = b"\x71"
_TC_STRING = b"\x74"
_TC_BLOCKDATA = b"\x77"

# java.util.ArrayList's serialVersionUID. The parser ignores its value (it scans
# for markers), but real bytes keep the layout honest.
_ARRAYLIST_SUID = 8683452581122892189
_TABLEINFO_SUID = 0  # placeholder; the parser never reads it
_TABLEINFO_CLASS = "gridaccess.client.tools.proxy.ToolProxy$TableInfo"


def _jstr(text: str) -> bytes:
    """A serialization short-UTF string: 2-byte length + UTF-8 bytes."""
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def encode_table_info(entries: Sequence[tuple[str, int]]) -> bytes:
    """Encode ``(table_name, record_count)`` pairs as a TABLE_INFO blob."""
    out = bytearray()
    out += b"\xac\xed\x00\x05"  # STREAM_MAGIC + version

    # Top-level ArrayList object + its class descriptor.
    out += _TC_OBJECT
    out += _TC_CLASSDESC
    out += _jstr("java.util.ArrayList")
    out += struct.pack(">q", _ARRAYLIST_SUID)
    out += b"\x03"  # SC_WRITE_METHOD | SC_SERIALIZABLE
    out += struct.pack(">H", 1)  # one field
    out += b"I" + _jstr("size")  # int size   -> the "size" of the "sizexp" marker
    out += _TC_ENDBLOCKDATA  # 'x'
    out += _TC_NULL  # 'p'  (no superclass) -> completes "sizexp"

    n = len(entries)
    out += struct.pack(">i", n)  # 'size' field value (defaultWriteObject)
    out += _TC_BLOCKDATA + b"\x04" + struct.pack(">i", n)  # writeInt(size)

    for index, (name, count) in enumerate(entries):
        if index == 0:
            out += _TC_OBJECT
            out += _TC_CLASSDESC
            out += _jstr(_TABLEINFO_CLASS)
            out += struct.pack(">q", _TABLEINFO_SUID)
            out += b"\x02"  # SC_SERIALIZABLE
            out += struct.pack(">H", 2)  # two fields
            out += b"J" + _jstr("noRecords")  # long noRecords
            out += b"L" + _jstr("tableName")  # Object tableName ...
            out += _TC_STRING + _jstr("Ljava/lang/String;")  # ... of type String
            out += _TC_ENDBLOCKDATA  # 'x'
            out += _TC_NULL  # 'p'  -> completes "Ljava/lang/String;xp"
        else:
            # TC_OBJECT + TC_REFERENCE to the TableInfo class descriptor (0x7E0002).
            out += _TC_OBJECT + _TC_REFERENCE + struct.pack(">i", 0x7E0002)
        out += struct.pack(">q", count)  # noRecords (long)
        out += _TC_STRING + _jstr(name)  # tableName (String)

    out += _TC_ENDBLOCKDATA  # end of ArrayList writeObject block
    return bytes(out)


# --- whole-export zip -------------------------------------------------------
def build_export_zip(
    tables: Mapping[str, tuple[Sequence[Field], Sequence[RowSpec]]],
    *,
    table_info: bool = True,
    compress: bool = False,
) -> bytes:
    """Build a zip: one entry per table (filename = table name, no extension),
    plus a TABLE_INFO catalog unless disabled. ``compress`` applies carry-forward
    string compression (ADR-026) to every table's rows."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, (fields, rows) in tables.items():
            zf.writestr(name, encode_table(fields, rows, compress=compress))
        if table_info:
            entries = [(name, len(rows)) for name, (fields, rows) in tables.items()]
            zf.writestr("TABLE_INFO", encode_table_info(entries))
    return buffer.getvalue()
