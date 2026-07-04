"""Property-style round-trip test: reader(writer(x)) == x, invariant holds.

Deterministic (fixed seed) so it is reproducible without a property-test
dependency. Covers bitmap-width boundaries and multibyte values.
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
                row[f.name] = ""  # present, zero-length
            else:
                row[f.name] = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 8)))
        rows.append(row)
    return rows


def test_roundtrip_across_bitmap_boundaries():
    rng = random.Random(20260704)
    for ncols in (1, 7, 8, 9, 15, 16, 17, 34, 100, 255, 256, 268):
        fields = [field(f"c{i:04d}") for i in range(ncols)]
        rows = _random_rows(rng, fields, nrows=30)
        _, rows_iter = read_table(io.BytesIO(encode_table(fields, rows)))
        got = list(rows_iter)
        assert got == rows, f"round-trip mismatch at ncols={ncols}"


def test_empty_table_has_header_no_rows():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15")]
    header, rows_iter = read_table(io.BytesIO(encode_table(fields, [])))
    assert header.names == ("mmcono", "mmitno")
    assert list(rows_iter) == []
