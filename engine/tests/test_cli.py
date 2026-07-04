"""Tests for the m3diff CLI (spec §4.6), including CLI == library JSON."""
from __future__ import annotations

import io
import json

from fixtures.builder import build_export_zip, field

from m3diff.cli import main
from m3diff.contract import to_json
from m3diff.diff import CompareOptions, compare
from m3diff.schema import Column, SchemaCache, TableSchema
from m3diff.source import open_export

_MM = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]


def _write_zip(path, tables):
    path.write_bytes(build_export_zip(tables))
    return str(path)


def _write_mm_schema_db(path):
    with SchemaCache(path) as cache:
        cols = tuple(
            Column(name, "String", None, None, "", ("00",) if name in ("MMCONO", "MMITNO") else ())
            for name in ("MMCONO", "MMITNO", "MMITDS")
        )
        cache.upsert_table(TableSchema("MVX", "MITMAS", "MF", "Item Master", cols, "2026-07-04"))
    return str(path)


def test_compare_writes_json(tmp_path):
    a = _write_zip(tmp_path / "a.zip", {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])})
    b = _write_zip(tmp_path / "b.zip", {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])})
    db = _write_mm_schema_db(tmp_path / "schema.db")
    out = tmp_path / "result.json"
    code = main(
        ["compare", "--mode", "inter", "--a", a, "--b", b, "--cono-a", "100", "--cono-b", "100",
         "--schema-db", db, "--out", str(out), "--generated-at", "2026-07-04T00:00:00Z"]
    )
    assert code == 0
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["mode"] == "inter"
    assert parsed["tables"]["MITMAS"]["status"] == "modified"
    assert parsed["tables"]["MITMAS"]["modified"][0]["changes"]["mmitds"] == {"a": "OLD", "b": "NEW"}


def test_cli_json_matches_library(tmp_path):
    tables_a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}
    tables_b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}
    a = _write_zip(tmp_path / "a.zip", tables_a)
    b = _write_zip(tmp_path / "b.zip", tables_b)
    db = _write_mm_schema_db(tmp_path / "schema.db")
    out = tmp_path / "cli.json"
    main(
        ["compare", "--mode", "inter", "--a", a, "--b", b, "--cono-a", "100", "--cono-b", "100",
         "--schema-db", db, "--out", str(out), "--generated-at", "2026-07-04T00:00:00Z"]
    )

    with SchemaCache(db) as cache:
        result = compare(
            open_export(a),
            open_export(b),
            CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=cache),
            tool_version=__import__("m3diff").__version__,
            generated_at="2026-07-04T00:00:00Z",
            a_label="a.zip",
            b_label="b.zip",
        )
    assert out.read_text(encoding="utf-8") == to_json(result)  # byte-identical


def test_compare_intra_requires_both_conos(tmp_path):
    a = _write_zip(tmp_path / "a.zip", {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A"}])})
    code = main(["compare", "--mode", "intra", "--a", a, "--cono-a", "500"])  # missing --cono-b
    assert code == 2


def test_compare_inter_requires_b(tmp_path):
    a = _write_zip(tmp_path / "a.zip", {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A"}])})
    code = main(["compare", "--mode", "inter", "--a", a, "--cono-a", "100", "--cono-b", "200"])
    assert code == 2


def test_compare_tables_scope(tmp_path):
    tables = {
        "MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A"}]),
        "OCUSMA": (_MM, [{"mmcono": "100", "mmitno": "X"}]),
        "CSYTAB": (_MM, [{"mmcono": "100", "mmitno": "T"}]),
    }
    a = _write_zip(tmp_path / "a.zip", tables)
    out = tmp_path / "r.json"
    main(
        ["compare", "--mode", "intra", "--a", a, "--cono-a", "100", "--cono-b", "100",
         "--tables", "CSY*,MITMAS", "--out", str(out), "--generated-at", "t"]
    )
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert set(parsed["tables"]) == {"MITMAS", "CSYTAB"}  # OCUSMA excluded by scope


def test_classify_writes_csv(tmp_path):
    tables = {
        "MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A"}, {"mmcono": "500", "mmitno": "B"}]),
        "COSRVI": ([field("svcono", "4"), field("svsiid", maxlen="10")], [{"svsiid": "G"}]),
    }
    a = _write_zip(tmp_path / "a.zip", tables)
    out = tmp_path / "cls.csv"
    code = main(["classify", a, "--out", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "table,class,rows" in text.splitlines()[0]
    assert "MITMAS,COMPANY,2" in text
    assert "COSRVI,GLOBAL,1" in text


def test_schema_refresh_missing_ionapi_errors(tmp_path):
    # A missing .ionapi fails before any network call.
    code = main(["schema", "refresh", "--ionapi", str(tmp_path / "nope.ionapi")])
    assert code == 2


def test_version_flag(capsys):
    try:
        main(["--version"])
    except SystemExit:
        pass
    assert "m3diff" in capsys.readouterr().out
