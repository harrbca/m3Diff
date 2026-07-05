"""Cached table-schema types (populated from the Metadata Publisher, ADR-002).

A table's primary key is *derived*, not stored separately: it is the columns
whose index membership includes ``00`` (the PK index), in response/definition
order — see METADATA-PUBLISHER-NOTES.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass

_PK_INDEX = "00"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    data_type: str  # "String" | "Decimal"
    length: int | None
    decimals: int | None
    edit_code: str
    indexes: tuple[str, ...]  # index codes this column belongs to, e.g. ("00","10")

    @property
    def is_pk(self) -> bool:
        return _PK_INDEX in self.indexes


@dataclass(frozen=True, slots=True)
class TableSchema:
    component: str  # e.g. "MVX" — part of identity (ADR-004)
    table_name: str
    category: str  # "MF" | "TF" | "WF" | "ST" | "SF" | ""  (ADR-006/016 scope signal)
    description: str
    columns: tuple[Column, ...]  # in definition order
    fetched_at: str
    # Maintaining program from MDP's tableMaintainedBy (e.g. OCUSMA → "CRS610",
    # OOTYPE → "OIS010"); "" when the metadata doesn't name one.
    maintained_by: str = ""

    @property
    def primary_key(self) -> tuple[str, ...]:
        """PK column names in order: the columns in index 00, response order."""
        return tuple(c.name for c in self.columns if c.is_pk)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


@dataclass(frozen=True, slots=True)
class SchemaResolution:
    """The result of resolving an export table name to a cached schema.

    ``ambiguous`` is True when the name existed under more than one component,
    so the diff can surface ``component_ambiguous`` (ADR-004).
    """

    schema: TableSchema | None
    component: str | None
    ambiguous: bool
