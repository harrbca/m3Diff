"""Golden tests for the diff engine (spec §6.3)."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fixtures.builder import build_export_zip, encode_table, field

from m3diff.contract import to_dict, to_json
from m3diff.diff import CompareCancelled, CompareOptions, _values_equal, compare
from m3diff.format import read_table
from m3diff.schema import Column, SchemaCache, TableSchema
from m3diff.source import ZipExportSource

# Columns reused across tests: mmcono (company), mmitno (item), mmitds (description).
_MM = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]


def _src(tables):
    return ZipExportSource(io.BytesIO(build_export_zip(tables)))


def _cache(table, pk_cols, all_cols, component="MVX"):
    cache = SchemaCache()
    columns = tuple(
        Column(
            name=name.upper(),  # metadata is uppercase; resolve_pk aligns to the export
            data_type="String",
            length=None,
            decimals=None,
            edit_code="",
            indexes=("00",) if name in pk_cols else (),
        )
        for name in all_cols
    )
    cache.upsert_table(TableSchema(component, table, "MF", "desc", columns, "2026-07-04"))
    return cache


def _mm_cache():
    return _cache("MITMAS", {"mmcono", "mmitno"}, ["mmcono", "mmitno", "mmitds"])


def _compare(a_tables, b_tables=None, **options):
    a = _src(a_tables)
    b = _src(b_tables) if b_tables is not None else None
    return compare(
        a,
        b,
        CompareOptions(**options),
        tool_version="0.1.0",
        generated_at="2026-07-04T00:00:00Z",
        a_label="a.zip",
        b_label="b.zip",
    )


# --- membership -------------------------------------------------------------
def test_identical():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "Widget"}])}
    result = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache())
    td = result.tables["MITMAS"]
    assert td.status == "identical"
    assert td.pk == ["mmcono", "mmitno"]
    assert td.pk_source == "metadata"
    assert td.schema_component == "MVX"
    assert result.summary.identical == 1


def test_added_row():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "W"},
        {"mmcono": "100", "mmitno": "B", "mmitds": "G"},
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.status == "modified"
    assert td.counts.added == 1 and td.counts.removed == 0 and td.counts.modified == 0
    assert td.added[0].pk == ["B"]  # masked key = (mmitno,) with CONO dropped


def test_removed_row():
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "W"},
        {"mmcono": "100", "mmitno": "B", "mmitds": "G"},
    ])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.status == "modified"
    assert td.counts.removed == 1
    assert td.removed[0].pk == ["B"]


def test_modified_field():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.status == "modified"
    assert td.counts.modified == 1
    change = td.modified[0]
    assert change.pk == ["A"]
    assert change.changes["mmitds"].a == "OLD"
    assert change.changes["mmitds"].b == "NEW"


# --- CONO masking (the danger zone) -----------------------------------------
def test_cono_masking_intra_identical():
    rows = [
        {"mmcono": "500", "mmitno": "A", "mmitds": "W"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "W"},
    ]
    result = _compare(
        {"MITMAS": (_MM, rows)}, None, mode="intra", cono_a="500", cono_b="100", cache=_mm_cache()
    )
    assert result.tables["MITMAS"].status == "identical"
    assert result.settings.pk_mask == ["CONO"]


def test_cono_masking_intra_detects_drift():
    rows = [
        {"mmcono": "500", "mmitno": "A", "mmitds": "MASTER"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "DRIFTED"},
    ]
    td = _compare(
        {"MITMAS": (_MM, rows)}, None, mode="intra", cono_a="500", cono_b="100", cache=_mm_cache()
    ).tables["MITMAS"]
    assert td.status == "modified"
    assert td.modified[0].changes["mmitds"].a == "MASTER"
    assert td.modified[0].changes["mmitds"].b == "DRIFTED"


# --- null vs empty ----------------------------------------------------------
# Post-ADR-026 a decoded row never contains "" — a present zero-length string is
# a carry marker, decompressed away, and a genuine blank is bitmap-absent. So the
# present-empty-vs-absent wire distinction no longer exists; the null_equals_empty
# toggle only bridges None vs a literal "" from a hypothetical future writer or a
# raw (decompress=False) side. Exercise that at the value-comparison level, and
# cover the still-reachable "real value vs absent" case end-to-end.
def test_null_equals_empty_bridges_none_and_blank_at_value_level():
    assert _values_equal(None, "", null_equals_empty=True) is True
    assert _values_equal("", None, null_equals_empty=True) is True
    assert _values_equal(None, None, null_equals_empty=True) is True
    assert _values_equal("W", None, null_equals_empty=True) is False  # real value != absent


def test_null_vs_empty_strict_mode_distinguishes_them_at_value_level():
    assert _values_equal(None, "", null_equals_empty=False) is False
    assert _values_equal("", "", null_equals_empty=False) is True
    assert _values_equal("W", "W", null_equals_empty=False) is True


def test_field_present_on_one_side_absent_on_other_is_a_change():
    # The remaining null case a real export produces: a field present with a value
    # on one side, absent (bitmap-clear) on the other.
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A"}])}  # mmitds absent
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.status == "modified"
    assert td.modified[0].changes["mmitds"].a == "W"
    assert td.modified[0].changes["mmitds"].b is None


# --- ignore list ------------------------------------------------------------
def test_ignored_timestamp_field_is_not_a_change():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmlmdt", maxlen="8")]
    a = {"MITMAS": (fields, [{"mmcono": "100", "mmitno": "A", "mmlmdt": "20260101"}])}
    b = {"MITMAS": (fields, [{"mmcono": "100", "mmitno": "A", "mmlmdt": "20260704"}])}
    cache = _cache("MITMAS", {"mmcono", "mmitno"}, ["mmcono", "mmitno", "mmlmdt"])
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=cache).tables["MITMAS"]
    assert td.status == "identical"  # *lmdt is in the default ignore list


# --- schema mismatch --------------------------------------------------------
def test_schema_mismatch_compares_on_intersection():
    fa = _MM
    fb = _MM + [field("mmextr", maxlen="5")]
    a = {"MITMAS": (fa, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    b = {"MITMAS": (fb, [{"mmcono": "100", "mmitno": "A", "mmitds": "W", "mmextr": "Z"}])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.schema_match is False
    assert td.status == "identical"  # intersection matches; mmextr is not compared


# --- error tolerance --------------------------------------------------------
def test_corrupt_table_recorded_as_error_run_continues():
    good = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    buffer = io.BytesIO(build_export_zip(good))
    with zipfile.ZipFile(buffer, "a") as zf:
        zf.writestr("BROKEN", b"\x00\x00\x00\x05short")  # header claims 5 bytes: undecodable
    a = ZipExportSource(io.BytesIO(buffer.getvalue()))
    b = _src(good)
    result = compare(
        a, b, CompareOptions(mode="inter", cono_a="100", cono_b="100"),
        tool_version="0.1.0", generated_at="t", a_label="a", b_label="b",
    )
    assert result.tables["MITMAS"].status == "identical"
    assert result.tables["BROKEN"].status == "error"
    assert result.tables["BROKEN"].error
    assert result.summary.errors == 1


# --- NO_CONO ----------------------------------------------------------------
def test_no_cono_table():
    fields = [field("aaaa", maxlen="3"), field("bbbb", maxlen="3")]  # no ...cono column
    a = {"CIDMAS": (fields, [{"aaaa": "1", "bbbb": "x"}])}
    b = {"CIDMAS": (fields, [{"aaaa": "1", "bbbb": "y"}])}
    cache = _cache("CIDMAS", {"aaaa"}, ["aaaa", "bbbb"])
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="200", cache=cache).tables["CIDMAS"]
    assert td.table_class == "NO_CONO"
    assert td.status == "modified"
    assert td.modified[0].changes["bbbb"].a == "x"


# --- global mode ------------------------------------------------------------
def test_global_mode_uses_only_the_mixed_subset():
    fields = [field("svcono", "4"), field("svsiid", maxlen="10"), field("svtx", maxlen="10")]
    a = {"COSRVI": (fields, [
        {"svsiid": "G1", "svtx": "AAA"},  # global (CONO absent)
        {"svcono": "100", "svsiid": "C1", "svtx": "BBB"},  # company
    ])}
    b = {"COSRVI": (fields, [
        {"svsiid": "G1", "svtx": "CHANGED"},  # global, drifted
        {"svcono": "200", "svsiid": "C1", "svtx": "ZZZ"},  # company, other tenant
    ])}
    cache = _cache("COSRVI", {"svcono", "svsiid"}, ["svcono", "svsiid", "svtx"])
    td = _compare(a, b, mode="global", cache=cache).tables["COSRVI"]
    assert td.table_class == "MIXED"
    assert td.global_subset is True
    assert td.rows_a == 1 and td.rows_b == 1  # company rows excluded
    assert td.status == "modified" and td.counts.modified == 1


def test_global_mode_skips_pure_company_table():
    fields = [field("mmcono", "4"), field("mmitno", maxlen="15")]
    a = {"MITMAS": (fields, [{"mmcono": "100", "mmitno": "A"}])}
    b = {"MITMAS": (fields, [{"mmcono": "200", "mmitno": "A"}])}
    result = _compare(a, b, mode="global")
    assert "MITMAS" not in result.tables


# --- table present on one side ----------------------------------------------
def test_table_missing_from_b():
    a = {
        "MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}]),
        "OCUSMA": (_MM, [{"mmcono": "100", "mmitno": "X", "mmitds": "Cust"}]),
    }
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    result = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache())
    assert result.tables["OCUSMA"].status == "missing_in_b"
    assert result.tables["OCUSMA"].counts.removed == 1
    assert result.summary.missing_in_b == 1


# --- caps and downgrade -----------------------------------------------------
def test_embed_cap_sets_truncated():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": f"I{i}", "mmitds": "OLD"} for i in range(5)])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": f"I{i}", "mmitds": "NEW"} for i in range(5)])}
    td = _compare(
        a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache(), max_rows_per_change=2
    ).tables["MITMAS"]
    assert td.counts.modified == 5
    assert len(td.modified) == 2
    assert td.truncated is True


def test_hash_downgrade_drops_field_detail_but_keeps_counts():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": f"I{i}", "mmitds": "OLD"} for i in range(5)])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": f"I{i}", "mmitds": "NEW"} for i in range(5)])}
    td = _compare(
        a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache(), hash_downgrade_threshold=2
    ).tables["MITMAS"]
    assert td.modified_detail is False
    assert td.counts.modified == 5
    assert td.modified[0].changes == {}  # no field-level detail once downgraded


# --- degenerate metadata PK (genuine duplicate PK values, ADR-014/025/026) ---
# NB: since ADR-026 a *blank* string PK column on the wire is a carry marker,
# decompressed away — it no longer degenerates a PK. What still can is metadata
# that declares a PK which does not uniquely key the rows: distinct rows share
# the same (non-blank) PK values. These tests use a genuine duplicate value.
def test_degenerate_pk_ambiguous_group_degrades_to_set_membership():
    """Two A rows share a masked key (the metadata PK does not uniquely key them):
    keying on the metadata PK would silently overwrite one row. The per-key
    retry keeps the metadata PK and compares just that group by set membership
    — the changed row is add+remove, never a false "modified"."""
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "WH1"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "WH2"},  # same masked key (A)
    ])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "WH1"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "CHANGED"},
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.pk_degenerate is True
    assert td.pk_source == "metadata"  # the PK is kept; only the group degrades
    assert td.ambiguous_keys == 1
    assert td.rows_a == 2 and td.rows_b == 2  # no rows silently dropped
    assert td.counts.modified == 0
    assert td.counts.added == 1 and td.counts.removed == 1


def test_degenerate_pk_clean_keys_keep_field_level_detail():
    """The point of the per-key retry: rows whose key IS unique still get real
    field-level "modified" detail even when another key in the table collides."""
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "GOOD", "mmitds": "OLD"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Y"},  # duplicate key ("DUP",)
    ])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "GOOD", "mmitds": "NEW"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Z"},
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.pk_source == "metadata" and td.pk_degenerate is True
    assert td.ambiguous_keys == 1
    # clean key GOOD: a real modified with field detail
    assert td.counts.modified == 1
    assert td.modified[0].changes["mmitds"].a == "OLD"
    assert td.modified[0].changes["mmitds"].b == "NEW"
    # ambiguous key "DUP": Y removed, Z added, X matched silently
    assert td.counts.added == 1 and td.counts.removed == 1
    assert td.modified_detail is True


def test_degenerate_pk_ambiguous_group_respects_ignored_fields():
    """Rows in an ambiguous group match on the compared-columns signature, so a
    difference only in an ignored field (change timestamps) is not a change."""
    cols = _MM + [field("mmlmdt", maxlen="10")]  # *lmdt is in DEFAULT_IGNORED_FIELDS
    a = {"MITMAS": (cols, [
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X", "mmlmdt": "20260101"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Y", "mmlmdt": "20260101"},
    ])}
    b = {"MITMAS": (cols, [
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X", "mmlmdt": "20260704"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Y", "mmlmdt": "20260704"},
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.pk_degenerate is True and td.ambiguous_keys == 1
    assert td.status == "identical"  # only ignored fields differ


def test_degenerate_pk_side_b_only_also_retries_per_key():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "A", "mmitds": "W"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "OTHER"},  # B-side collision
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.pk_degenerate is True
    assert td.pk_source == "metadata" and td.ambiguous_keys == 1
    assert td.counts.added == 1  # the extra B row is an add, not an overwrite


def test_degenerate_pk_over_threshold_falls_back_to_full_row_identity():
    """A degenerate table too large for the per-key retry keeps ADR-014's
    whole-table full-row fallback (field detail is lost past that size anyway)."""
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "DUP", "mmitds": f"R{i}"} for i in range(4)
    ])}
    td = _compare(a, a, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache(),
                  hash_downgrade_threshold=3).tables["MITMAS"]
    assert td.pk_degenerate is True
    assert td.pk_source == "heuristic"  # whole-table fallback, not per-key
    assert td.ambiguous_keys == 0
    assert td.status == "identical"


def test_unique_metadata_pk_is_not_flagged_degenerate():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    td = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()).tables["MITMAS"]
    assert td.pk_degenerate is False
    assert td.pk_source == "metadata"


# --- carry-forward compression composed with the diff (ADR-026 golden) -------
# MITBAL-shaped: a multi-column PK (cono, itno, whlo) where one PK column carries
# down runs on the wire, exactly the real-export pattern that used to degenerate.
_MB = [field("mbcono", "4"), field("mbitno", maxlen="15"), field("mbwhlo", maxlen="5")]


def _mb_cache():
    return _cache("MITBAL", {"mbcono", "mbitno", "mbwhlo"}, ["mbcono", "mbitno", "mbwhlo"])


def test_carry_forward_crossing_cono_boundary_masks_to_identical():
    """The single easiest thing to get subtly wrong, now in two ways: company B's
    first row carries string values from company A's last row (compression is
    positional and does NOT reset at the CONO boundary). With CONO masked, the
    intra diff of A vs B is identical — decompression and masking compose."""
    rows = [
        {"mmcono": "500", "mmitno": "ITEM", "mmitds": "Widget"},   # company 500
        {"mmcono": "100", "mmitno": "ITEM", "mmitds": "Widget"},   # 100: itno+itds carried across CONO
    ]
    # Guard: the fixture really put carry markers on company 100's row.
    raw = list(read_table(io.BytesIO(encode_table(_MM, rows, compress=True)), decompress=False)[1])
    assert raw[1]["mmitno"] == "" and raw[1]["mmitds"] == ""

    a = ZipExportSource(io.BytesIO(build_export_zip({"MITMAS": (_MM, rows)}, compress=True)))
    td = compare(
        a, None, CompareOptions(mode="intra", cono_a="500", cono_b="100", cache=_mm_cache()),
        tool_version="0.1.0", generated_at="t",
    ).tables["MITMAS"]
    assert td.status == "identical"
    assert (td.counts.added, td.counts.removed, td.counts.modified) == (0, 0, 0)


def test_compressed_pk_column_no_longer_degenerates():
    """A PK column (whlo) carries down runs. Read literally the markers collapse
    (itno, whlo) keys and the metadata PK looks degenerate; decompression restores
    the distinct warehouses so the PK keys cleanly (pk_degenerate False)."""
    rows = [
        {"mbcono": "100", "mbitno": "IT1", "mbwhlo": "WHA"},
        {"mbcono": "100", "mbitno": "IT2", "mbwhlo": "WHA"},  # whlo carried (WHA run)
        {"mbcono": "100", "mbitno": "IT1", "mbwhlo": "WHB"},
        {"mbcono": "100", "mbitno": "IT2", "mbwhlo": "WHB"},  # whlo carried (WHB run)
    ]
    # Guard: whlo really carried on rows 2 and 4 — read raw these collapse to
    # (IT2,'') twice, the false degenerate PK the reader fix eliminates.
    raw = list(read_table(io.BytesIO(encode_table(_MB, rows, compress=True)), decompress=False)[1])
    assert raw[1]["mbwhlo"] == "" and raw[3]["mbwhlo"] == ""

    zbytes = build_export_zip({"MITBAL": (_MB, rows)}, compress=True)
    td = compare(
        ZipExportSource(io.BytesIO(zbytes)), ZipExportSource(io.BytesIO(zbytes)),
        CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=_mb_cache()),
        tool_version="0.1.0", generated_at="t",
    ).tables["MITBAL"]
    assert td.pk_degenerate is False
    assert td.ambiguous_keys == 0
    assert td.pk_source == "metadata"
    assert td.status == "identical"


def test_same_data_different_compression_is_identical():
    """Side A uncompressed, side B carry-forward compressed: same logical rows,
    so the diff is identical (the reader decompresses B before comparing)."""
    rows = [
        {"mmcono": "100", "mmitno": "A", "mmitds": "Widget"},
        {"mmcono": "100", "mmitno": "B", "mmitds": "Widget"},  # itds repeats -> compresses on side B
    ]
    # Guard: side B really compressed mmitds on its second row.
    raw = list(read_table(io.BytesIO(encode_table(_MM, rows, compress=True)), decompress=False)[1])
    assert raw[1]["mmitds"] == ""

    a = ZipExportSource(io.BytesIO(build_export_zip({"MITMAS": (_MM, rows)}, compress=False)))
    b = ZipExportSource(io.BytesIO(build_export_zip({"MITMAS": (_MM, rows)}, compress=True)))
    td = compare(
        a, b, CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=_mm_cache()),
        tool_version="0.1.0", generated_at="t",
    ).tables["MITMAS"]
    assert td.status == "identical"
    assert (td.counts.added, td.counts.removed, td.counts.modified) == (0, 0, 0)


# --- maintaining program (maintained_by) --------------------------------------
def test_maintained_by_flows_from_schema_to_result():
    cache = SchemaCache()
    columns = tuple(
        Column(n.upper(), "String", None, None, "", ("00",) if n != "mmitds" else ())
        for n in ("mmcono", "mmitno", "mmitds")
    )
    cache.upsert_table(
        TableSchema("MVX", "MITMAS", "MF", "Item Master", columns, "t", maintained_by="MMS001")
    )
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    result = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100", cache=cache)
    td = result.tables["MITMAS"]
    assert td.maintained_by == "MMS001"
    assert json.loads(to_json(result))["tables"]["MITMAS"]["maintained_by"] == "MMS001"


def test_maintained_by_none_without_schema():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    td = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100").tables["MITMAS"]
    assert td.maintained_by is None


# --- table description --------------------------------------------------------
def test_description_flows_from_schema_to_result():
    cache = SchemaCache()
    columns = tuple(
        Column(n.upper(), "String", None, None, "", ("00",) if n != "mmitds" else ())
        for n in ("mmcono", "mmitno", "mmitds")
    )
    cache.upsert_table(
        TableSchema("MVX", "MITMAS", "MF", "MF: Item master", columns, "t")
    )
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    result = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100", cache=cache)
    td = result.tables["MITMAS"]
    assert td.description == "MF: Item master"
    assert json.loads(to_json(result))["tables"]["MITMAS"]["description"] == "MF: Item master"


def test_description_none_without_schema():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    td = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100").tables["MITMAS"]
    assert td.description is None


# --- column descriptions ------------------------------------------------------
def _desc_cache():
    """MITMAS cache where mmcono and mmitds carry column descriptions."""
    cache = SchemaCache()
    descs = {"mmcono": "Company", "mmitds": "Item description"}
    columns = tuple(
        Column(
            n.upper(), "String", None, None, "",
            ("00",) if n in ("mmcono", "mmitno") else (),
            descs.get(n, ""),
        )
        for n in ("mmcono", "mmitno", "mmitds")
    )
    cache.upsert_table(TableSchema("MVX", "MITMAS", "MF", "MF: Item master", columns, "t"))
    return cache


def test_column_descriptions_annotate_modified_fields():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}
    result = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_desc_cache())
    td = result.tables["MITMAS"]
    assert td.status == "modified"
    # mmitds is annotated; mmcono is excluded — the CONO field is masked out of
    # the comparison, so its description would never label a change.
    assert td.column_descriptions == {"mmitds": "Item description"}
    assert json.loads(to_json(result))["tables"]["MITMAS"]["column_descriptions"] == {
        "mmitds": "Item description"
    }


def test_column_descriptions_empty_on_identical_table():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    td = _compare(tables, tables, mode="inter", cono_a="100", cono_b="100",
                  cache=_desc_cache()).tables["MITMAS"]
    assert td.status == "identical"
    assert td.column_descriptions == {}  # no field detail to annotate → not embedded


def test_degenerate_pk_intra_mode_cono_collision():
    """Intra mode: masking CONO makes rows from the two companies collide only in
    the B stream if the same masked key repeats within one company — a plain
    cross-company match must NOT be flagged degenerate."""
    rows = [
        {"mmcono": "500", "mmitno": "A", "mmitds": "W"},
        {"mmcono": "100", "mmitno": "A", "mmitds": "W"},
    ]
    td = _compare(
        {"MITMAS": (_MM, rows)}, None, mode="intra", cono_a="500", cono_b="100", cache=_mm_cache()
    ).tables["MITMAS"]
    assert td.pk_degenerate is False  # each side sees the key once
    assert td.status == "identical"


def test_degenerate_pk_preserves_schema_description_and_column_descriptions():
    """A degenerate metadata PK must keep the schema-derived metadata —
    description and column descriptions (ADR-022/023) — through the retry.
    Regression: the old whole-table fallback rebuilt the PK and dropped them."""
    # mmitno duplicated in both rows → the masked PK collapses to the same key →
    # the metadata PK degenerates and the table retries per key (ADR-025).
    a = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Y"},
    ])}
    b = {"MITMAS": (_MM, [
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "X"},
        {"mmcono": "100", "mmitno": "DUP", "mmitds": "Z"},
    ])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_desc_cache()).tables["MITMAS"]
    assert td.pk_degenerate is True and td.pk_source == "metadata"
    assert td.ambiguous_keys == 1
    assert td.description == "MF: Item master"
    assert td.column_descriptions == {"mmitds": "Item description"}
    assert td.status == "modified"  # ambiguous group: the changed row is add+remove


# --- heuristic fallback -----------------------------------------------------
def test_heuristic_pk_degrades_to_set_membership():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}
    td = _compare(a, b, mode="inter", cono_a="100", cono_b="100").tables["MITMAS"]  # no cache
    assert td.pk_source == "heuristic"
    assert td.counts.modified == 0  # full-row identity: a change is add+remove
    assert td.counts.added == 1 and td.counts.removed == 1


# --- category scoping (ADR-006/016) -----------------------------------------
def _two_category_cache():
    """MITMAS categorized MF, OOHEAD categorized TF, in one cache."""
    cache = _mm_cache()  # MITMAS, category MF
    cols = tuple(
        Column(n.upper(), "String", None, None, "", ("00",) if n != "mmitds" else ())
        for n in ("mmcono", "mmitno", "mmitds")
    )
    cache.upsert_table(TableSchema("MVX", "OOHEAD", "TF", "CO header", cols, "2026-07-04"))
    return cache


def test_category_scope_selects_only_matching_tables():
    tables = {
        "MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}]),
        "OOHEAD": (_MM, [{"mmcono": "100", "mmitno": "O1", "mmitds": "T"}]),
    }
    result = _compare(
        tables, tables, mode="inter", cono_a="100", cono_b="100",
        cache=_two_category_cache(), categories=("MF",),
    )
    assert set(result.tables) == {"MITMAS"}  # OOHEAD (TF) excluded


def test_category_scope_unions_with_table_patterns():
    tables = {
        "MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}]),
        "OOHEAD": (_MM, [{"mmcono": "100", "mmitno": "O1", "mmitds": "T"}]),
        "ZZUNKN": (_MM, [{"mmcono": "100", "mmitno": "Z", "mmitds": "?"}]),  # not in cache
    }
    result = _compare(
        tables, tables, mode="inter", cono_a="100", cono_b="100",
        cache=_two_category_cache(), categories=("MF",), tables=("ZZ*",),
    )
    # MF picks MITMAS; the glob picks the uncached ZZUNKN; OOHEAD stays out.
    assert set(result.tables) == {"MITMAS", "ZZUNKN"}


def test_category_scope_without_cache_is_an_error():
    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    with pytest.raises(ValueError, match="schema cache"):
        _compare(tables, tables, mode="inter", cono_a="100", cono_b="100", categories=("MF",))


# --- serialization ----------------------------------------------------------
def test_compare_honors_cancellation():
    tables = {
        "AAA": (_MM, [{"mmcono": "100", "mmitno": "1", "mmitds": "x"}]),
        "BBB": (_MM, [{"mmcono": "100", "mmitno": "2", "mmitds": "y"}]),
    }
    a = _src(tables)
    with pytest.raises(CompareCancelled):
        compare(a, None, CompareOptions(mode="intra", cono_a="100", cono_b="100"), cancelled=lambda: True)


def test_compare_reports_progress_per_table():
    tables = {
        "AAA": (_MM, [{"mmcono": "100", "mmitno": "1"}]),
        "BBB": (_MM, [{"mmcono": "100", "mmitno": "2"}]),
    }
    seen: list[tuple[int, int, str]] = []
    compare(
        _src(tables), None, CompareOptions(mode="intra", cono_a="100", cono_b="100"),
        progress=lambda d, t, n: seen.append((d, t, n)),
    )
    assert seen == [(1, 2, "AAA"), (2, 2, "BBB")]


def test_result_json_is_valid_and_deterministic():
    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": n, "mmitds": "OLD"} for n in ("C", "A", "B")])}
    b = {"MITMAS": (_MM, [])}
    r1 = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache())
    r2 = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache())
    assert to_json(r1) == to_json(r2)  # deterministic
    parsed = json.loads(to_json(r1))
    assert parsed["mode"] == "inter"
    assert parsed["tool_version"] == "0.1.0"
    removed_pks = [entry["pk"] for entry in parsed["tables"]["MITMAS"]["removed"]]
    assert removed_pks == sorted(removed_pks)  # change lists sorted by masked PK


def test_from_dict_round_trips_to_identical_json():
    """from_dict(to_dict(r)) re-serializes byte-identically (render RPC contract)."""
    from m3diff.contract import from_dict, to_dict

    a = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": n, "mmitds": "OLD"} for n in ("B", "A")])}
    b = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}
    result = _compare(a, b, mode="inter", cono_a="100", cono_b="100", cache=_mm_cache())
    assert to_json(from_dict(to_dict(result))) == to_json(result)


def test_from_dict_tolerates_older_json_without_additive_fields():
    from m3diff.contract import from_dict, to_dict

    tables = {"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "W"}])}
    d = to_dict(_compare(tables, tables, mode="inter", cono_a="100", cono_b="100"))
    for td in d["tables"].values():  # simulate a result written before ADR-014/017/023/025
        del td["pk_degenerate"]
        del td["maintained_by"]
        del td["column_descriptions"]
        del td["ambiguous_keys"]
    rebuilt = from_dict(d)
    td = rebuilt.tables["MITMAS"]
    assert td.pk_degenerate is False and td.maintained_by is None
    assert td.column_descriptions == {} and td.ambiguous_keys == 0
