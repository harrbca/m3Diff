"""Tests for the streaming binary export reader (spec §2.1)."""
from __future__ import annotations

import io
import struct
import zipfile

import pytest
from fixtures.builder import build_export_zip, encode_header, encode_row, encode_table, field

from m3diff.format import (
    CompressionError,
    HeaderError,
    RowLengthError,
    TableHeader,
    TruncatedExportError,
    read_header,
    read_table,
)


def _roundtrip(fields, rows):
    header, rows_iter = read_table(io.BytesIO(encode_table(fields, rows)))
    return header, list(rows_iter)


def _raw_table(fields, raw_rows):
    """Assemble a table stream from hand-crafted rows. A value of ``""`` places a
    present zero-length cell on the wire (a carry-forward marker), which the
    normal builder path never emits — so carry semantics are exercised directly.
    """
    out = bytearray(encode_header(fields))
    for values in raw_rows:
        out += encode_row(fields, values)
    return bytes(out)


def _read_raw(fields, raw_rows, **kw):
    header, rows_iter = read_table(io.BytesIO(_raw_table(fields, raw_rows)), **kw)
    return list(rows_iter)


def test_header_and_simple_rows():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]
    rows = [
        {"mmcono": "100", "mmitno": "ITEM001", "mmitds": "Widget"},
        {"mmcono": "100", "mmitno": "ITEM002", "mmitds": "Gadget"},
    ]
    header, got = _roundtrip(fields, rows)
    assert header.names == ("mmcono", "mmitno", "mmitds")
    assert header.nfields == 3
    assert header.bitmap_bytes == 1
    assert got == rows


# --- carry-forward string decompression (ADR-026) ---------------------------
def test_carry_forward_repeats_last_present_value():
    fields = [field("k"), field("v")]
    # v on the wire: A '' '' '' -> decodes to A A A A (k varies to keep rows apart)
    got = _read_raw(fields, [
        {"k": "1", "v": "A"},
        {"k": "2", "v": ""},
        {"k": "3", "v": ""},
        {"k": "4", "v": ""},
    ])
    assert [r["v"] for r in got] == ["A", "A", "A", "A"]


def test_carry_forward_updates_on_each_new_value():
    fields = [field("k"), field("v")]
    # v on the wire: A '' C '' -> A A C C (the carry updates when a value is present)
    got = _read_raw(fields, [
        {"k": "1", "v": "A"},
        {"k": "2", "v": ""},
        {"k": "3", "v": "C"},
        {"k": "4", "v": ""},
    ])
    assert [r["v"] for r in got] == ["A", "A", "C", "C"]


def test_carry_survives_rows_where_the_column_is_absent():
    fields = [field("k"), field("v")]
    # v: present A, then absent (bitmap-clear), then '' -> A, absent, A.
    got = _read_raw(fields, [
        {"k": "1", "v": "A"},
        {"k": "2"},           # v absent from the bitmap
        {"k": "3", "v": ""},  # carry marker: repeats the last *present* value
    ])
    assert got[0]["v"] == "A"
    assert "v" not in got[1]   # absent stays absent — carry is not materialized here
    assert got[2]["v"] == "A"  # carry survived the gap (rule 2: last present value)


def test_columns_compress_independently():
    fields = [field("a"), field("b"), field("c")]
    got = _read_raw(fields, [
        {"a": "X", "b": "Y", "c": "Z"},
        {"a": "", "b": "Q", "c": ""},   # a,c carried; b new
        {"a": "", "b": "", "c": "Z2"},  # a,b carried; c new
    ])
    assert got == [
        {"a": "X", "b": "Y", "c": "Z"},
        {"a": "X", "b": "Q", "c": "Z"},
        {"a": "X", "b": "Q", "c": "Z2"},
    ]


def test_orphan_carry_on_first_row_raises():
    fields = [field("a"), field("b")]
    with pytest.raises(CompressionError):
        _read_raw(fields, [{"a": "X", "b": ""}])  # b '' with nothing to carry


def test_orphan_carry_after_only_absent_rows_raises():
    fields = [field("a"), field("b")]
    with pytest.raises(CompressionError):
        _read_raw(fields, [
            {"a": "1"},            # b absent, never present
            {"a": "2", "b": ""},   # b '' but no prior present value
        ])


def test_zero_length_in_non_string_column_raises():
    fields = [field("n", "4"), field("v")]  # n is INTEGER (type 4)
    with pytest.raises(CompressionError):
        _read_raw(fields, [
            {"n": "5", "v": "A"},
            {"n": "", "v": "B"},   # '' in a non-string column is a format violation
        ])


def test_decompress_false_returns_raw_carry_markers():
    fields = [field("k"), field("v")]
    got = _read_raw(fields, [
        {"k": "1", "v": "A"},
        {"k": "2", "v": ""},
    ], decompress=False)
    assert got[1]["v"] == ""  # the raw diagnostic path preserves the marker verbatim


def test_decompressed_rows_never_contain_empty_string():
    fields = [field("a"), field("b"), field("c")]
    got = _read_raw(fields, [
        {"a": "P", "b": "Q", "c": "R"},
        {"a": "", "b": "", "c": ""},
        {"a": "P2", "b": "", "c": "R"},
    ])
    assert all(v != "" for r in got for v in r.values())


def test_absent_cono_stays_absent():
    fields = [field("okcono", "4"), field("okother", maxlen="5")]
    header, got = _roundtrip(fields, [{"okother": "Z"}])  # cono omitted
    assert header.cono_field_indexes() == [0]
    assert "okcono" not in got[0]  # the classify/diff layer maps absent -> CONO 0


def test_cono_detection_flags_multiple_matches():
    header = TableHeader(
        fields=(field("mmcono", "4"), field("xxcono", "4"), field("mmitno", maxlen="15"))
    )
    assert header.cono_field_indexes() == [0, 1]


def test_cono_detection_ignores_non_six_char_and_non_suffix():
    header = TableHeader(
        fields=(field("conox"), field("aconob"), field("mmcono", "4"), field("cono"))
    )
    assert header.cono_field_indexes() == [2]


@pytest.mark.parametrize(
    ("ncols", "expected_bitmap"),
    [(1, 1), (8, 1), (9, 2), (16, 2), (34, 5), (268, 34)],
)
def test_bitmap_width_scales_with_columns(ncols, expected_bitmap):
    header = TableHeader(fields=tuple(field(f"c{i:04d}") for i in range(ncols)))
    assert header.bitmap_bytes == expected_bitmap


def test_wide_268_column_table_roundtrips():
    fields = [field(f"z{i:04d}") for i in range(268)]
    row = {f.name: str(i) for i, f in enumerate(fields) if i % 3 == 0}
    header, got = _roundtrip(fields, [row])
    assert header.bitmap_bytes == 34
    assert got == [row]


def test_multibyte_utf8_values_roundtrip():
    fields = [field("a"), field("b")]
    rows = [{"a": "café", "b": "数据"}]  # value byte length != char length
    _, got = _roundtrip(fields, rows)
    assert got == rows


def test_truncated_stream_raises():
    data = encode_table([field("a"), field("b")], [{"a": "hello", "b": "world"}])
    with pytest.raises(TruncatedExportError):
        list(read_table(io.BytesIO(data[:-3]))[1])


def test_row_length_mismatch_raises():
    fields = [field("a"), field("b")]
    data = bytearray(encode_table(fields, [{"a": "hello", "b": "world"}]))
    off = len(encode_header(fields))  # first row's length prefix sits right after the header
    (rowlen,) = struct.unpack_from(">I", data, off)
    struct.pack_into(">I", data, off, rowlen - 1)  # under-declare the payload
    with pytest.raises((RowLengthError, TruncatedExportError)):
        list(read_table(io.BytesIO(bytes(data)))[1])


def test_empty_stream_raises_header_error():
    with pytest.raises(HeaderError):
        read_header(io.BytesIO(b""))


def test_malformed_descriptor_raises_header_error():
    bad = b"4;mmcono;3"  # only three parts
    data = struct.pack(">I", len(bad)) + bad
    with pytest.raises(HeaderError):
        read_header(io.BytesIO(data))


def test_reads_from_a_zip_entry_stream():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15")]
    rows = [{"mmcono": "100", "mmitno": "A"}, {"mmcono": "100", "mmitno": "B"}]
    zbytes = build_export_zip({"MITMAS": (fields, rows)})
    with zipfile.ZipFile(io.BytesIO(zbytes)).open("MITMAS") as stream:
        header, rows_iter = read_table(stream)
        got = list(rows_iter)
    assert header.names == ("mmcono", "mmitno")
    assert got == rows
