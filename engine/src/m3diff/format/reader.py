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
        [for each set bit, in column order: 4B uint32 value length, then UTF-8 bytes;
         a zero-length value in a STRING column is a carry-forward marker meaning
         "repeat this column's last present value" (ADR-026), decompressed here]
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import BinaryIO

from .types import (
    CompressionError,
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


def _decode_row(
    payload: bytes,
    header: TableHeader,
    carry: list[str | None],
    string_flags: tuple[bool, ...],
    ordinal: int,
    decompress: bool,
) -> Row:
    """Decode one row, applying carry-forward decompression (ADR-026).

    ``carry`` holds each column's last present value and is **mutated in place**
    across the stream (allocated once in ``iter_rows``). ``string_flags`` and
    ``ordinal`` are only used for error messages / the compression rules.
    With ``decompress=False`` a zero-length value is returned verbatim as ``""``
    (the raw diagnostic path) and ``carry`` is left untouched.
    """
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
            continue  # bit clear => field absent => null; carry[i] survives the gap
        if pos + 4 > len(payload):
            raise RowLengthError("value-length prefix runs past row payload")
        (vlen,) = _U32.unpack_from(payload, pos)
        pos += 4
        end = pos + vlen
        if end > len(payload):
            raise RowLengthError("value bytes run past row payload")
        if decompress and vlen == 0:
            # Present zero-length value: a carry-forward marker (spec §2.1). Valid
            # only on a string column that already has a value to repeat.
            if not string_flags[i]:
                raise CompressionError(
                    f"zero-length value in non-string column {names[i]!r} "
                    f"(type {header.fields[i].type!r}) at row {ordinal}"
                )
            carried = carry[i]
            if carried is None:
                raise CompressionError(
                    f"carry-forward marker for column {names[i]!r} with no prior "
                    f"value (orphan carry) at row {ordinal}"
                )
            row[names[i]] = carried  # carry unchanged: it stays the last present value
        else:
            value = _decode(payload[pos:end], f"value for column {names[i]!r}")
            row[names[i]] = value
            if decompress:
                carry[i] = value
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


def iter_rows(
    stream: BinaryIO, header: TableHeader, *, decompress: bool = True
) -> Iterator[Row]:
    """Yield fully decoded rows from ``stream`` until EOF, one dict per row.

    String columns are carry-forward compressed on the wire (spec §2.1): a
    present zero-length value repeats that column's last present value. This is
    decompressed transparently, so decoded rows never contain ``""``. Every
    consumer in the diff/classify path must see decompressed rows. Pass
    ``decompress=False`` for the raw cells (diagnostics / byte round-trip tests
    only); it is a library-only knob and is not exposed on the CLI.
    """
    carry: list[str | None] = [None] * header.nfields
    string_flags = header.string_field_flags
    for ordinal, payload in enumerate(_iter_payloads(stream)):
        yield _decode_row(payload, header, carry, string_flags, ordinal, decompress)


def read_table(
    stream: BinaryIO, *, decompress: bool = True
) -> tuple[TableHeader, Iterator[Row]]:
    """Read the header, then return it with a lazy iterator over the rows.

    The iterator reads from the same stream, so consume it before touching the
    stream again. ``decompress`` is passed through to ``iter_rows``.
    """
    header = read_header(stream)
    return header, iter_rows(stream, header, decompress=decompress)


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

    Carry-forward compression (ADR-026) does not touch this fast path: CONO is
    INTEGER in every observed export and non-string columns never compress, so
    the CONO cell is always a literal value, never a carry marker. Guard the
    never-observed case where CONO is string-typed — there a zero-length CONO
    could be a carry marker this path would silently mis-yield, so refuse it up
    front rather than emit a wrong company number.
    """
    if cono_index is not None and header.string_field_flags[cono_index]:
        raise CompressionError(
            f"CONO column {header.names[cono_index]!r} is string-typed; the "
            "stop-at-CONO fast path does not decompress carry-forward markers"
        )
    for payload in _iter_payloads(stream):
        yield None if cono_index is None else _extract_cono(payload, header, cono_index)
