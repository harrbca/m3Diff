"""SQLite schema cache, keyed on (component, table_name), MVX-preferred (ADR-004).

Export files identify a table by name only (the binary header carries no
component), so lookups resolve a bare name to a schema — preferring the MVX
component and flagging when the same name exists under several components.

The cache is the source of truth for the diff once populated; a refresh from the
Metadata Publisher (Phase 3b) fills it. Everything here is offline.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable

from .models import Column, SchemaResolution, TableSchema

_MVX = "MVX"

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS tables (
    component     TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL DEFAULT '',
    maintained_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (component, table_name)
);
CREATE TABLE IF NOT EXISTS columns (
    component   TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    data_type   TEXT NOT NULL DEFAULT '',
    length      INTEGER,
    decimals    INTEGER,
    edit_code   TEXT NOT NULL DEFAULT '',
    idx_list    TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (component, table_name, ordinal)
);
CREATE INDEX IF NOT EXISTS columns_by_table ON columns (table_name);
CREATE INDEX IF NOT EXISTS tables_by_name ON tables (table_name);
"""


class SchemaCache:
    """A local SQLite store of table schemas."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:") -> None:
        #: The on-disk path (or ":memory:"). A file-backed cache can be re-opened
        #: read-only inside a worker process; an in-memory one cannot be shared.
        self.path = path if isinstance(path, str) else os.fspath(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_DDL)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Bring a pre-existing cache file up to the current DDL (additive only)."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(tables)")}
        if "maintained_by" not in cols:
            self._conn.execute(
                "ALTER TABLE tables ADD COLUMN maintained_by TEXT NOT NULL DEFAULT ''"
            )
        col_cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(columns)")}
        if "description" not in col_cols:
            self._conn.execute(
                "ALTER TABLE columns ADD COLUMN description TEXT NOT NULL DEFAULT ''"
            )

    def upsert_table(self, schema: TableSchema) -> None:
        """Insert or replace one table's schema (and all its columns)."""
        conn = self._conn
        with conn:  # single transaction
            conn.execute(
                "DELETE FROM tables WHERE component = ? AND table_name = ?",
                (schema.component, schema.table_name),
            )
            conn.execute(
                "DELETE FROM columns WHERE component = ? AND table_name = ?",
                (schema.component, schema.table_name),
            )
            conn.execute(
                "INSERT INTO tables VALUES (?, ?, ?, ?, ?, ?)",
                (
                    schema.component,
                    schema.table_name,
                    schema.category,
                    schema.description,
                    schema.fetched_at,
                    schema.maintained_by,
                ),
            )
            conn.executemany(
                "INSERT INTO columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        schema.component,
                        schema.table_name,
                        ordinal,
                        col.name,
                        col.data_type,
                        col.length,
                        col.decimals,
                        col.edit_code,
                        ",".join(col.indexes),
                        col.description,
                    )
                    for ordinal, col in enumerate(schema.columns)
                ],
            )

    def components_for(self, table_name: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT component FROM tables WHERE table_name = ? ORDER BY component",
            (table_name,),
        ).fetchall()
        return [row["component"] for row in rows]

    def get(self, table_name: str, component: str) -> TableSchema | None:
        """Fetch one exact (component, table_name) schema, or None."""
        trow = self._conn.execute(
            "SELECT * FROM tables WHERE component = ? AND table_name = ?",
            (component, table_name),
        ).fetchone()
        if trow is None:
            return None
        crows = self._conn.execute(
            "SELECT * FROM columns WHERE component = ? AND table_name = ? ORDER BY ordinal",
            (component, table_name),
        ).fetchall()
        columns = tuple(
            Column(
                name=row["name"],
                data_type=row["data_type"],
                length=row["length"],
                decimals=row["decimals"],
                edit_code=row["edit_code"],
                indexes=tuple(code for code in row["idx_list"].split(",") if code),
                description=row["description"],
            )
            for row in crows
        )
        return TableSchema(
            component=component,
            table_name=table_name,
            category=trow["category"],
            description=trow["description"],
            columns=columns,
            fetched_at=trow["fetched_at"],
            maintained_by=trow["maintained_by"],
        )

    def set_table_info(
        self, component: str, table_name: str, *,
        category: str, description: str, maintained_by: str,
    ) -> bool:
        """Update one cached table's list-endpoint metadata without touching its
        columns (the cheap `--info-only` refresh). Returns False if not cached."""
        cursor = self._conn.execute(
            "UPDATE tables SET category = ?, description = ?, maintained_by = ? "
            "WHERE component = ? AND table_name = ?",
            (category, description, maintained_by, component, table_name),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def resolve(self, table_name: str) -> SchemaResolution:
        """Resolve a bare export table name to a schema, MVX-preferred (ADR-004)."""
        components = self.components_for(table_name)
        if not components:
            return SchemaResolution(schema=None, component=None, ambiguous=False)
        chosen = _MVX if _MVX in components else components[0]
        return SchemaResolution(
            schema=self.get(table_name, chosen),
            component=chosen,
            ambiguous=len(components) > 1,
        )

    def tables_in_categories(self, categories: Iterable[str]) -> set[str]:
        """Table names whose category matches, MVX-preferred (ADR-006/016).

        A table under several components takes its category from the same
        component ``resolve()`` would choose: MVX when present, else the
        alphabetically first. Category matching is case-insensitive.
        """
        wanted = {c.upper() for c in categories}
        chosen: dict[str, tuple[str, str]] = {}  # name -> (component, category)
        rows = self._conn.execute(
            "SELECT table_name, component, category FROM tables ORDER BY table_name, component"
        )
        for name, component, category in rows:
            prev = chosen.get(name)
            # first row per name is components[0]; MVX overrides it (resolve()'s rule)
            if prev is None or (component == _MVX and prev[0] != _MVX):
                chosen[name] = (component, category)
        return {name for name, (_, cat) in chosen.items() if cat.upper() in wanted}

    def table_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SchemaCache":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
