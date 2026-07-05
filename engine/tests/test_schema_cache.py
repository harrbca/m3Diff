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


def _schema_cat(component: str, table: str, category: str) -> TableSchema:
    return TableSchema(
        component=component,
        table_name=table,
        category=category,
        description=f"{category}: {table}",
        columns=(_col("AACONO", ("00",)), _col("AAKEY", ("00",))),
        fetched_at="2026-07-04T00:00:00Z",
    )


def test_tables_in_categories_basic_and_case_insensitive():
    with SchemaCache() as cache:
        cache.upsert_table(_schema_cat("MVX", "MITMAS", "MF"))
        cache.upsert_table(_schema_cat("MVX", "OOHEAD", "TF"))
        cache.upsert_table(_schema_cat("MVX", "AINVRO", "WF"))
        assert cache.tables_in_categories(["MF"]) == {"MITMAS"}
        assert cache.tables_in_categories(["mf", "tf"]) == {"MITMAS", "OOHEAD"}
        assert cache.tables_in_categories(["ST"]) == set()


def test_tables_in_categories_prefers_mvx_component():
    """Same table, different category per component: the MVX row decides —
    mirroring resolve()'s component choice (ADR-004)."""
    with SchemaCache() as cache:
        cache.upsert_table(_schema_cat("MJP", "CSYTAB", "TF"))  # non-MVX says TF
        cache.upsert_table(_schema_cat("MVX", "CSYTAB", "MF"))  # MVX says MF
        assert cache.tables_in_categories(["MF"]) == {"CSYTAB"}
        assert cache.tables_in_categories(["TF"]) == set()


def test_tables_in_categories_no_mvx_uses_first_component():
    with SchemaCache() as cache:
        cache.upsert_table(_schema_cat("MDB", "SOMETBL", "MF"))  # alphabetically first
        cache.upsert_table(_schema_cat("MJP", "SOMETBL", "TF"))
        assert cache.tables_in_categories(["MF"]) == {"SOMETBL"}


def test_maintained_by_roundtrip_and_info_update():
    with SchemaCache() as cache:
        schema = TableSchema("MVX", "OCUSMA", "MF", "Customer", (_col("OKCONO", ("00",)),),
                             "t", maintained_by="CRS610")
        cache.upsert_table(schema)
        got = cache.get("OCUSMA", "MVX")
        assert got is not None and got.maintained_by == "CRS610"
        # info-only update touches metadata but not columns
        assert cache.set_table_info("MVX", "OCUSMA", category="MF",
                                    description="Customer", maintained_by="CRS999") is True
        got = cache.get("OCUSMA", "MVX")
        assert got.maintained_by == "CRS999"
        assert got.column_names == ("OKCONO",)
        # unknown table: no row updated
        assert cache.set_table_info("MVX", "NOPE", category="", description="",
                                    maintained_by="") is False


def test_migration_adds_maintained_by_to_old_cache(tmp_path):
    """A cache created before the column existed opens cleanly and reads ''."""
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE tables (
            component TEXT NOT NULL, table_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (component, table_name)
        );
        CREATE TABLE columns (
            component TEXT NOT NULL, table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
            name TEXT NOT NULL, data_type TEXT NOT NULL DEFAULT '', length INTEGER,
            decimals INTEGER, edit_code TEXT NOT NULL DEFAULT '',
            idx_list TEXT NOT NULL DEFAULT '', PRIMARY KEY (component, table_name, ordinal)
        );
        INSERT INTO tables VALUES ('MVX', 'MITMAS', 'MF', 'Item Master', 't');
        INSERT INTO columns VALUES ('MVX', 'MITMAS', 0, 'MMCONO', 'String', 3, NULL, '', '00');
        """
    )
    conn.commit()
    conn.close()
    with SchemaCache(db) as cache:
        got = cache.get("MITMAS", "MVX")
        assert got is not None
        assert got.maintained_by == ""  # migrated tables column, empty default
        assert got.columns[0].description == ""  # migrated columns column, empty default


def test_column_description_roundtrip():
    schema = TableSchema(
        component="MVX",
        table_name="MITMAS",
        category="MF",
        description="Item Master",
        columns=(
            Column("MMCONO", "Decimal", 3, None, "", ("00",), "Company"),
            Column("MMITNO", "String", 15, None, "", ("00",), "Item number"),
            Column("MMITDS", "String", 30, None, "", (), "Item description"),
        ),
        fetched_at="t",
    )
    with SchemaCache() as cache:
        cache.upsert_table(schema)
        got = cache.get("MITMAS", "MVX")
        assert got is not None
        assert [c.description for c in got.columns] == ["Company", "Item number", "Item description"]
