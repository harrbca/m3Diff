"""Golden tests for the table classifier (spec §2.3, §6.3)."""
from __future__ import annotations

import io
import zipfile

from fixtures.builder import build_export_zip, encode_table, field

from m3diff.classify import classify_export, classify_stream, observed_conos
from m3diff.source import ZipExportSource


def _classify(fields, rows, name="T"):
    return classify_stream(io.BytesIO(encode_table(fields, rows)), name)


def test_company_table():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15")]
    rows = [
        {"mmcono": "100", "mmitno": "A"},
        {"mmcono": "500", "mmitno": "B"},
        {"mmcono": "100", "mmitno": "C"},
    ]
    result = _classify(fields, rows, "MITMAS")
    assert result.cls == "COMPANY"
    assert result.cono_field == "mmcono"
    assert (result.rows, result.rows_global) == (3, 0)
    assert result.conos == ("100", "500")
    assert not result.cono_ambiguous


def test_global_table_absent_blank_and_zero_all_count_as_global():
    fields = [field("svcono", "4"), field("svsiid", maxlen="10")]
    rows = [
        {"svsiid": "X"},  # CONO absent      -> global (how real exports store globals)
        # A present zero-length INTEGER CONO does not occur in a well-formed export
        # (globals are bitmap-absent), but the stop-at-CONO fast path does not
        # decompress/validate — it defensively normalizes a stray blank to global.
        {"svcono": "", "svsiid": "Y"},  # CONO blank present -> global (fast-path robustness)
        {"svcono": "0", "svsiid": "Z"},  # CONO literal "0"   -> global
    ]
    result = _classify(fields, rows, "COSRVI")
    assert result.cls == "GLOBAL"
    assert (result.rows, result.rows_global) == (3, 3)
    assert result.conos == ()


def test_mixed_table():
    fields = [field("mmcono", "4"), field("x", maxlen="3")]
    rows = [{"mmcono": "100", "x": "a"}, {"x": "b"}]  # one company, one global (absent)
    result = _classify(fields, rows, "MIX")
    assert result.cls == "MIXED"
    assert result.rows_global == 1
    assert result.conos == ("100",)


def test_no_cono_table():
    fields = [field("aaaa", maxlen="3"), field("bbbb", maxlen="3")]  # no ...cono column
    result = _classify(fields, [{"aaaa": "1"}, {"aaaa": "2"}], "NOCONO")
    assert result.cls == "NO_CONO"
    assert result.cono_field is None
    assert result.rows == 2


def test_empty_table():
    result = _classify([field("mmcono", "4")], [], "EMPTY")
    assert result.cls == "EMPTY"
    assert result.rows == 0


def test_cono_column_need_not_be_first():
    fields = [field("aa", maxlen="3"), field("mmcono", "4"), field("bb", maxlen="3")]
    rows = [
        {"aa": "x", "mmcono": "100", "bb": "y"},
        {"aa": "z", "bb": "w"},  # CONO absent -> global
    ]
    result = _classify(fields, rows, "OFFSET")
    assert result.cls == "MIXED"
    assert result.rows_global == 1
    assert result.conos == ("100",)


def test_multiple_cono_columns_are_flagged():
    fields = [field("mmcono", "4"), field("xxcono", "4"), field("mmitno", maxlen="5")]
    rows = [{"mmcono": "100", "xxcono": "200", "mmitno": "A"}]
    result = _classify(fields, rows, "AMBIG")
    assert result.cono_ambiguous
    assert result.cono_field == "mmcono"  # first match wins, deterministically
    assert result.conos == ("100",)  # classified on the first CONO column


def test_classify_export_tolerates_a_bad_table():
    buffer = io.BytesIO(build_export_zip({"MITMAS": ([field("mmcono", "4")], [{"mmcono": "100"}])}))
    with zipfile.ZipFile(buffer, "a") as zf:
        zf.writestr("BROKEN", b"\x00\x00\x00\x05short")  # header claims 5 bytes: undecodable
    results = {r.table: r for r in classify_export(ZipExportSource(io.BytesIO(buffer.getvalue())))}
    assert results["MITMAS"].cls == "COMPANY"
    assert results["BROKEN"].cls == "PARSE_ERROR"
    assert results["BROKEN"].error


def test_observed_conos_aggregates_across_tables():
    tables = {
        "A": (
            [field("aacono", "4"), field("x", maxlen="3")],
            [{"aacono": "100", "x": "1"}, {"aacono": "500", "x": "2"}],
        ),
        "B": (
            [field("bbcono", "4"), field("y", maxlen="3")],
            [{"bbcono": "100", "y": "1"}, {"bbcono": "900", "y": "2"}],
        ),
        "G": ([field("cccono", "4")], [{}]),  # global-only (CONO absent)
    }
    results = classify_export(ZipExportSource(io.BytesIO(build_export_zip(tables))))
    assert observed_conos(results) == ["100", "500", "900"]
