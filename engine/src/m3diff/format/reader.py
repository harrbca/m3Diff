"""Streaming decoder for the per-table binary export format (spec §2.1).

Ports the verified logic of ``reference/parse_export.py`` but reads from a
stream instead of slurping the whole file, and raises typed errors so the diff
loop can tolerate a bad table without aborting the run (spec F6).

Wire format::

    [4B big-endian uint32: header length]
    [header: UTF-8, column descriptors joined by 0x01; descriptor = "type;name;maxlen;flag"]
    repeated until EOF:
        [4B big-endian uint32: row payload length]
        [null bitmap: ceil(nfields/8) bytes, MSB-first, bit set => value present]
        [for each set bit, in column order: 4B uint32 value length, then UTF-8 bytes]
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import BinaryIO

from .types import (
    ExportFormatError,
    Field,
    HeaderError,
    Row,
    RowLengthError,
    TableHeader,
    TruncatedExportError,
)

_U32 = struct.Struct(">I")


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise TruncatedExportError."""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    buf = b"".join(chunks)
    if len(buf) != n:
        raise TruncatedExportError(f"expected {n} bytes, got {len(buf)}")
    return buf


def _decode(buf: bytes, what: str) -> str:
    try:
        return buf.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportFormatError(f"invalid UTF-8 in {what}: {exc}") from exc


def _parse_header(header: str) -> tuple[Field, ...]:
    if not header:
        raise HeaderError("empty header: table has no columns")
    fields: list[Field] = []
    for descriptor in header.split("\x01"):
        parts = descriptor.split(";")
        if len(parts) != 4:
            raise HeaderError(f"malformed column descriptor: {descriptor!r}")
        type_, name, maxlen, flag = parts
        if not name:
            raise HeaderError(f"empty column name in descriptor: {descriptor!r}")
        fields.append(Field(type=type_, name=name, maxlen=maxlen, flag=flag))
    return tuple(fields)


def read_header(stream: BinaryIO) -> TableHeader:
    """Read and parse the table header from the start of ``stream``."""
    raw_len = stream.read(4)
    if len(raw_len) == 0:
        raise HeaderError("empty stream: no header-length prefix")
    if len(raw_len) != 4:
        raise TruncatedExportError("truncated header-length prefix")
    (hlen,) = _U32.unpack(raw_len)
    header = _decode(_read_exact(stream, hlen), "header")
    return TableHeader(fields=_parse_header(header))


def _decode_row(payload: bytes, header: TableHeader) -> Row:
    nfields = header.nfields
    bitmap_bytes = header.bitmap_bytes
    names = header.names
    if len(payload) < bitmap_bytes:
        raise RowLengthError(
            f"row payload {len(payload)} shorter than bitmap width {bitmap_bytes}"
        )
    bitmap = payload[:bitmap_bytes]
    pos = bitmap_bytes
    row: Row = {}
    for i in range(nfields):
        if not (bitmap[i >> 3] & (0x80 >> (i & 7))):
            continue  # bit clear => field absent => null
        if pos + 4 > len(payload):
            raise RowLengthError("value-length prefix runs past row payload")
        (vlen,) = _U32.unpack_from(payload, pos)
        pos += 4
        end = pos + vlen
        if end > len(payload):
            raise RowLengthError("value bytes run past row payload")
        row[names[i]] = _decode(payload[pos:end], f"value for column {names[i]!r}")
        pos = end
    if pos != len(payload):
        # The length prefix is a checksum: every declared byte must be consumed.
        raise RowLengthError(f"row length mismatch: consumed {pos}, declared {len(payload)}")
    return row


def _iter_payloads(stream: BinaryIO) -> Iterator[bytes]:
    """Yield each row's payload (bitmap + values) until a clean EOF."""
    while True:
        raw_len = stream.read(4)
        if len(raw_len) == 0:
            return  # clean EOF at a row boundary
        if len(raw_len) != 4:
            raise TruncatedExportError("truncated row-length prefix")
        (rowlen,) = _U32.unpack(raw_len)
        yield _read_exact(stream, rowlen)


def iter_rows(stream: BinaryIO, header: TableHeader) -> Iterator[Row]:
    """Yield fully decoded rows from ``stream`` until EOF, one dict per row."""
    for payload in _iter_payloads(stream):
        yield _decode_row(payload, header)


def read_table(stream: BinaryIO) -> tuple[TableHeader, Iterator[Row]]:
    """Read the header, then return it with a lazy iterator over the rows.

    The iterator reads from the same stream, so consume it before touching the
    stream again.
    """
    header = read_header(stream)
    return header, iter_rows(stream, header)


def _extract_cono(payload: bytes, header: TableHeader, cono_index: int) -> str | None:
    """Decode only the CONO cell of a row (spec §2.3 fast path).

    Returns the raw CONO string if present, or None if that column is absent
    from the row's bitmap. Walks just far enough to reach the column, skipping
    earlier value bytes without decoding them.
    """
    bitmap_bytes = header.bitmap_bytes
    if len(payload) < bitmap_bytes:
        raise RowLengthError(
            f"row payload {len(payload)} shorter than bitmap width {bitmap_bytes}"
        )
    bitmap = payload[:bitmap_bytes]
    if not (bitmap[cono_index >> 3] & (0x80 >> (cono_index & 7))):
        return None  # CONO absent => caller treats as tenant-global (CONO 0)
    pos = bitmap_bytes
    for i in range(cono_index + 1):
        if not (bitmap[i >> 3] & (0x80 >> (i & 7))):
            continue
        if pos + 4 > len(payload):
            raise RowLengthError("value-length prefix runs past row payload")
        (vlen,) = _U32.unpack_from(payload, pos)
        pos += 4
        end = pos + vlen
        if end > len(payload):
            raise RowLengthError("value bytes run past row payload")
        if i == cono_index:
            return _decode(payload[pos:end], "CONO value")
        pos = end
    return None  # unreachable: the bit was set above


def iter_cono_values(
    stream: BinaryIO, header: TableHeader, cono_index: int | None
) -> Iterator[str | None]:
    """Stream just the CONO cell of each row (or None per row for a NO_CONO table).

    Reads each full row payload (keeping the stream aligned and catching
    truncation) but decodes only up to the CONO column — the classifier scan
    optimization from spec §2.3. Yields the raw CONO string or None (absent).
    """
    for payload in _iter_payloads(stream):
        yield None if cono_index is None else _extract_cono(payload, header, cono_index)
