"""Tests for the TABLE_INFO deserializer (spec §2.2)."""
from __future__ import annotations

import io
import zipfile

import pytest
from fixtures.builder import build_export_zip, encode_table_info, field

from m3diff.format import TableInfoEntry, TableInfoError, parse_table_info


def test_roundtrips_multiple_entries_in_order():
    entries = [("MITMAS", 1_234_567), ("OCUSMA", 42), ("CSYTAB", 0)]
    got = parse_table_info(encode_table_info(entries))
    assert [(e.table_name, e.record_count) for e in got] == entries


def test_single_entry():
    got = parse_table_info(encode_table_info([("MITMAS", 5)]))
    assert got == [TableInfoEntry("MITMAS", 5)]


def test_empty_list():
    assert parse_table_info(encode_table_info([])) == []


def test_record_count_uses_full_long_range():
    got = parse_table_info(encode_table_info([("MITTRA", 5_000_000_000)]))
    assert got[0].record_count == 5_000_000_000  # exceeds 32-bit int


def test_bad_magic_raises():
    with pytest.raises(TableInfoError):
        parse_table_info(b"definitely-not-java-serialized")


def test_reads_table_info_from_a_built_zip():
    tables = {
        "MITMAS": ([field("mmcono", "4"), field("mmitno", maxlen="15")], [{"mmitno": "A"}]),
        "OCUSMA": ([field("occono", "4")], []),
    }
    zbytes = build_export_zip(tables)
    with zipfile.ZipFile(io.BytesIO(zbytes)).open("TABLE_INFO") as stream:
        got = parse_table_info(stream.read())
    assert {e.table_name: e.record_count for e in got} == {"MITMAS": 1, "OCUSMA": 0}
