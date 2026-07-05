"""Property-style round-trip tests: reader(writer(x)) == x, invariant holds.

Deterministic (fixed seed) so it is reproducible without a property-test
dependency. Covers bitmap-width boundaries, multibyte values, and (ADR-026)
carry-forward compressed fixtures.
"""
from __future__ import annotations

import io
import random

from fixtures.builder import encode_table, field

from m3diff.format import read_table

_ALPHABET = "ABCabc012 -_/.áé数据"


def _random_rows(rng: random.Random, fields, nrows: int):
    rows = []
    for _ in range(nrows):
        row = {}
        for f in fields:
            roll = rng.random()
            if roll < 0.33:
                continue  # null: leave the field out entirely
            if roll < 0.5:
                row[f.name] = ""  # present, zero-length (a raw carry-forward marker)
            else:
                row[f.name] = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 8)))
        rows.append(row)
    return rows


def test_roundtrip_across_bitmap_boundaries():
    # These fixtures include bare present zero-length cells, which on the wire are
    # carry-forward markers (ADR-026). Read them on the raw path (decompress=False)
    # so this stays a byte-exact writer/reader inverse across bitmap widths; the
    # decompressing path is exercised by test_compressed_fixture_* below.
    rng = random.Random(20260704)
    for ncols in (1, 7, 8, 9, 15, 16, 17, 34, 100, 255, 256, 268):
        fields = [field(f"c{i:04d}") for i in range(ncols)]
        rows = _random_rows(rng, fields, nrows=30)
        _, rows_iter = read_table(io.BytesIO(encode_table(fields, rows)), decompress=False)
        got = list(rows_iter)
        assert got == rows, f"round-trip mismatch at ncols={ncols}"


def test_compressed_fixture_roundtrips_to_logical_rows():
    """Builder ``compress=True`` (the writer-side inverse of the decompressing
    reader): compressed bytes decode back to the original logical rows."""
    fields = [field("k"), field("v")]
    rows = [
        {"k": "1", "v": "A"},
        {"k": "2", "v": "A"},  # v repeats -> emitted as a carry marker
        {"k": "3", "v": "B"},
        {"k": "4", "v": "B"},  # v repeats -> carry marker
    ]
    _, rows_iter = read_table(io.BytesIO(encode_table(fields, rows, compress=True)))
    assert list(rows_iter) == rows


def test_compressed_fixture_actually_emits_zero_length_cells():
    """Guard that the compressed fixture really exercises the carry path: the
    stream is smaller than the uncompressed one and carries a zero-length cell."""
    fields = [field("k"), field("v")]
    rows = [{"k": "1", "v": "A"}, {"k": "2", "v": "A"}]
    compressed = encode_table(fields, rows, compress=True)
    assert len(compressed) < len(encode_table(fields, rows, compress=False))
    _, raw_iter = read_table(io.BytesIO(compressed), decompress=False)
    assert list(raw_iter)[1]["v"] == ""  # the carry marker is really on the wire


def test_empty_table_has_header_no_rows():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15")]
    header, rows_iter = read_table(io.BytesIO(encode_table(fields, [])))
    assert header.names == ("mmcono", "mmitno")
    assert list(rows_iter) == []
