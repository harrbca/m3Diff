"""Tests for ExportSource (zip and directory access)."""
from __future__ import annotations

import io
import zipfile

import pytest
from fixtures.builder import build_export_zip, encode_table, encode_table_info, field

from m3diff.format import read_table
from m3diff.source import DirectoryExportSource, ZipExportSource, open_export


def _tables():
    return {
        "MITMAS": (
            [field("mmcono", "4"), field("mmitno", maxlen="15")],
            [{"mmcono": "100", "mmitno": "A"}, {"mmcono": "500", "mmitno": "B"}],
        ),
        "OCUSMA": ([field("occono", "4")], []),
    }


def test_zip_table_names_exclude_table_info_and_are_sorted():
    src = ZipExportSource(io.BytesIO(build_export_zip(_tables())))
    assert src.table_names() == ["MITMAS", "OCUSMA"]


def test_zip_open_table_and_manifest():
    with ZipExportSource(io.BytesIO(build_export_zip(_tables()))) as src:
        with src.open_table("MITMAS") as stream:
            header, rows = read_table(stream)
            assert header.names == ("mmcono", "mmitno")
            assert len(list(rows)) == 2
        info = src.table_info()
        assert info is not None
        assert {e.table_name: e.record_count for e in info} == {"MITMAS": 2, "OCUSMA": 0}


def test_directory_source(tmp_path):
    tables = _tables()
    for name, (fields, rows) in tables.items():
        (tmp_path / name).write_bytes(encode_table(fields, rows))
    (tmp_path / "TABLE_INFO").write_bytes(
        encode_table_info([(n, len(r)) for n, (f, r) in tables.items()])
    )
    with open_export(tmp_path) as src:
        assert isinstance(src, DirectoryExportSource)
        assert src.table_names() == ["MITMAS", "OCUSMA"]
        assert src.table_info() is not None


def test_open_export_selects_zip(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(build_export_zip(_tables()))
    with open_export(path) as src:
        assert isinstance(src, ZipExportSource)
        assert src.table_names() == ["MITMAS", "OCUSMA"]


def test_open_export_rejects_non_export(tmp_path):
    path = tmp_path / "notazip.txt"
    path.write_text("hello")
    with pytest.raises(ValueError):
        open_export(path)


def test_missing_manifest_is_none():
    src = ZipExportSource(io.BytesIO(build_export_zip(_tables(), table_info=False)))
    assert src.table_info() is None


def test_unparseable_manifest_degrades_to_none():
    buffer = io.BytesIO(build_export_zip(_tables(), table_info=False))
    with zipfile.ZipFile(buffer, "a") as zf:
        zf.writestr("TABLE_INFO", b"not-java-serialized")
    src = ZipExportSource(io.BytesIO(buffer.getvalue()))
    assert src.table_info() is None


def test_unknown_table_raises_keyerror():
    src = ZipExportSource(io.BytesIO(build_export_zip(_tables())))
    with pytest.raises(KeyError):
        src.open_table("DOES_NOT_EXIST")
