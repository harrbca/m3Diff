"""Tests for the CSV and Markdown reporters (spec F14/F15)."""
from __future__ import annotations

import io

from fixtures.builder import build_export_zip, field

from m3diff.cli import main
from m3diff.diff import CompareOptions, compare
from m3diff.report import to_markdown, to_summary_csv, to_table_csv
from m3diff.schema import Column, SchemaCache, TableSchema
from m3diff.source import ZipExportSource

_MM = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]


def _mm_cache():
    cache = SchemaCache()
    cols = tuple(
        Column(n, "String", None, None, "", ("00",) if n in ("MMCONO", "MMITNO") else ())
        for n in ("MMCONO", "MMITNO", "MMITDS")
    )
    cache.upsert_table(TableSchema("MVX", "MITMAS", "MF", "Item Master", cols, "t"))
    return cache


def _result():
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "OLD"},
        {"mmcono": "100", "mmitno": "B", "mmitds": "Keep"},
    ])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "NEW"},
        {"mmcono": "100", "mmitno": "C", "mmitds": "Added"},
    ])}
    return compare(
        ZipExportSource(io.BytesIO(build_export_zip(a))),
        ZipExportSource(io.BytesIO(build_export_zip(b))),
        CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()),
        generated_at="2026-07-04T00:00:00Z",
        a_label="a.zip",
        b_label="b.zip",
    )


def test_summary_csv():
    text = to_summary_csv(_result())
    rows = text.splitlines()
    assert rows[0] == "table,class,status,pk_source,schema_component,schema_match,rows_a,rows_b,added,removed,modified,error"
    # A modified (mmitds), B->added C, B-only -> removed B
    assert "MITMAS,COMPANY,modified,metadata,MVX,yes,2,2,1,1,1," in rows[1]


def test_table_detail_csv():
    td = _result().tables["MITMAS"]
    text = to_table_csv(td)
    assert "modified,A,mmitds,OLD,NEW" in text
    assert "added,C,mmitds,,Added" in text
    assert "removed,B,mmitds,Keep," in text


def test_markdown_report():
    md = to_markdown(_result())
    assert "# m3diff report" in md
    assert "| Modified | 1 |" in md
    assert "### MITMAS — modified" in md
    assert "mmitds: 'OLD' → 'NEW'" in md


def test_cli_format_csv(tmp_path):
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "X"}])}))
    b.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "Y"}])}))
    out = tmp_path / "r.csv"
    code = main(
        ["compare", "--mode", "inter", "--a", str(a), "--b", str(b), "--cono-a", "100",
         "--cono-b", "100", "--format", "csv", "--out", str(out), "--generated-at", "t"]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("table,class,status")


def test_cli_format_markdown(tmp_path):
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "X"}])}))
    b.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "Y"}])}))
    out = tmp_path / "r.md"
    main(
        ["compare", "--mode", "inter", "--a", str(a), "--b", str(b), "--cono-a", "100",
         "--cono-b", "100", "--format", "md", "--out", str(out), "--generated-at", "t"]
    )
    assert out.read_text(encoding="utf-8").startswith("# m3diff report")
