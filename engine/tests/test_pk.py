"""Tests for PK resolution and CONO masking (spec §3.2; the danger zone)."""
from __future__ import annotations

from m3diff.format.types import Field, TableHeader
from m3diff.pk import PrimaryKey, cono_column, masked_key, resolve_pk
from m3diff.schema import Column, SchemaCache, TableSchema


def _header(*names: str) -> TableHeader:
    return TableHeader(fields=tuple(Field(type="12", name=n, maxlen="0", flag="") for n in names))


def _col(name: str, indexes: tuple[str, ...]) -> Column:
    return Column(name=name, data_type="String", length=None, decimals=None, edit_code="", indexes=indexes)


def _cache_with(table: str, component: str, columns) -> SchemaCache:
    cache = SchemaCache()
    cache.upsert_table(
        TableSchema(component, table, "MF", "", tuple(columns), "2026-07-04")
    )
    return cache


# --- resolution -------------------------------------------------------------
def test_metadata_pk_is_aligned_to_lowercase_export_columns():
    cache = _cache_with(
        "MITMAS", "MVX", [_col("MMCONO", ("00",)), _col("MMITNO", ("00",)), _col("MMITDS", ())]
    )
    header = _header("mmcono", "mmitno", "mmitds")  # export header is lowercase
    pk = resolve_pk("MITMAS", header, cache)
    assert pk.source == "metadata"
    assert pk.columns == ("mmcono", "mmitno")  # mapped to the export's casing
    assert pk.component == "MVX"
    assert pk.component_ambiguous is False


def test_metadata_pk_carries_component_ambiguity():
    cache = SchemaCache()
    for component in ("MVX", "MJP"):
        cache.upsert_table(
            TableSchema(component, "CSYTAB", "MF", "", (_col("CTCONO", ("00",)), _col("CTDIVI", ("00",))), "t")
        )
    pk = resolve_pk("CSYTAB", _header("ctcono", "ctdivi"), cache)
    assert pk.source == "metadata"
    assert pk.component == "MVX"
    assert pk.component_ambiguous is True


def test_heuristic_when_table_not_cached():
    pk = resolve_pk("UNKNOWN", _header("aacono", "aaitno", "aades"), cache=None)
    assert pk.source == "heuristic"
    assert pk.columns == ("aacono", "aaitno", "aades")  # full-row identity


def test_heuristic_when_pk_column_absent_from_export():
    cache = _cache_with("T", "MVX", [_col("TTCONO", ("00",)), _col("TTKEY", ("00",))])
    header = _header("ttcono", "ttother")  # ttkey missing from the export
    pk = resolve_pk("T", header, cache)
    assert pk.source == "heuristic"


def test_heuristic_when_schema_has_no_pk_columns():
    cache = _cache_with("T", "MVX", [_col("TTAAA", ()), _col("TTBBB", ())])
    pk = resolve_pk("T", _header("ttaaa", "ttbbb"), cache)
    assert pk.source == "heuristic"


# --- CONO masking (golden) --------------------------------------------------
def test_cono_column_detection():
    assert cono_column(_header("mmcono", "mmitno")) == "mmcono"
    assert cono_column(_header("aaa", "bbb")) is None


def test_masking_makes_cross_company_rows_match():
    header = _header("mmcono", "mmitno")
    pk = PrimaryKey(columns=("mmcono", "mmitno"), source="metadata")
    drop = {cono_column(header)}
    company_500 = {"mmcono": "500", "mmitno": "ITEM001"}
    company_100 = {"mmcono": "100", "mmitno": "ITEM001"}
    assert masked_key(company_500, pk, drop) == masked_key(company_100, pk, drop)
    assert masked_key(company_500, pk, drop) == ("ITEM001",)


def test_masking_does_not_collapse_distinct_business_keys():
    pk = PrimaryKey(columns=("mmcono", "mmitno"), source="metadata")
    drop = {"mmcono"}
    a = masked_key({"mmcono": "500", "mmitno": "ITEM001"}, pk, drop)
    b = masked_key({"mmcono": "500", "mmitno": "ITEM002"}, pk, drop)
    assert a != b


def test_masking_absent_cono_matches_present_cono():
    # A global row (CONO absent) and a company row with the same business key
    # collide once CONO is masked out.
    pk = PrimaryKey(columns=("mmcono", "mmitno"), source="metadata")
    drop = {"mmcono"}
    absent = masked_key({"mmitno": "ITEM001"}, pk, drop)  # mmcono absent
    present = masked_key({"mmcono": "100", "mmitno": "ITEM001"}, pk, drop)
    assert absent == present == ("ITEM001",)


def test_no_masking_keeps_cono_in_key():
    pk = PrimaryKey(columns=("mmcono", "mmitno"), source="metadata")
    assert masked_key({"mmcono": "500", "mmitno": "A"}, pk) == ("500", "A")
