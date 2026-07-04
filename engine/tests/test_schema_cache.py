"""Tests for the SQLite schema cache (ADR-004)."""
from __future__ import annotations

from m3diff.schema import Column, SchemaCache, TableSchema


def _col(name: str, indexes: tuple[str, ...] = (), dtype: str = "String", length=None) -> Column:
    return Column(
        name=name, data_type=dtype, length=length, decimals=None, edit_code="", indexes=indexes
    )


def _schema(component: str = "MVX", table: str = "MITMAS") -> TableSchema:
    return TableSchema(
        component=component,
        table_name=table,
        category="MF",
        description="Item Master",
        columns=(
            _col("MMCONO", ("00", "01"), "Decimal", 3),
            _col("MMITNO", ("00", "01"), "String", 15),
            _col("MMITDS", (), "String", 30),
        ),
        fetched_at="2026-07-04T00:00:00Z",
    )


def test_upsert_and_get():
    with SchemaCache() as cache:
        cache.upsert_table(_schema())
        schema = cache.get("MITMAS", "MVX")
        assert schema is not None
        assert schema.column_names == ("MMCONO", "MMITNO", "MMITDS")
        assert schema.primary_key == ("MMCONO", "MMITNO")
        assert schema.category == "MF"
        assert schema.columns[0].length == 3


def test_resolve_prefers_mvx_and_flags_ambiguous():
    with SchemaCache() as cache:
        cache.upsert_table(_schema("MVX", "CSYTAB"))
        cache.upsert_table(_schema("MJP", "CSYTAB"))
        resolution = cache.resolve("CSYTAB")
        assert resolution.component == "MVX"
        assert resolution.ambiguous is True
        assert resolution.schema is not None and resolution.schema.component == "MVX"


def test_resolve_single_component_is_not_ambiguous():
    with SchemaCache() as cache:
        cache.upsert_table(_schema("MVX", "MITMAS"))
        resolution = cache.resolve("MITMAS")
        assert resolution.component == "MVX"
        assert resolution.ambiguous is False


def test_resolve_falls_back_to_only_component_when_no_mvx():
    with SchemaCache() as cache:
        cache.upsert_table(_schema("MDB", "SOMETBL"))
        resolution = cache.resolve("SOMETBL")
        assert resolution.component == "MDB"
        assert resolution.ambiguous is False


def test_resolve_missing_table():
    with SchemaCache() as cache:
        resolution = cache.resolve("NOPE")
        assert resolution.schema is None
        assert resolution.component is None
        assert resolution.ambiguous is False


def test_upsert_replaces_existing():
    with SchemaCache() as cache:
        cache.upsert_table(_schema())
        cache.upsert_table(_schema())
        assert cache.table_count() == 1
        schema = cache.get("MITMAS", "MVX")
        assert schema is not None and len(schema.columns) == 3


def test_persists_to_disk(tmp_path):
    path = tmp_path / "schema.db"
    with SchemaCache(path) as cache:
        cache.upsert_table(_schema())
    with SchemaCache(path) as cache:
        schema = cache.get("MITMAS", "MVX")
        assert schema is not None
        assert schema.primary_key == ("MMCONO", "MMITNO")


def test_empty_index_membership_roundtrips():
    with SchemaCache() as cache:
        cache.upsert_table(_schema())
        schema = cache.get("MITMAS", "MVX")
        assert schema is not None
        assert schema.columns[2].indexes == ()  # MMITDS is in no index
        assert schema.columns[0].indexes == ("00", "01")
