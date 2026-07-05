"""Primary-key resolution and CONO masking (spec §3.2).

Row identity for the diff. Resolution order:

1. **Metadata** — the cached schema's PK (index 00), aligned to the export's
   column casing. Metadata columns are uppercase (e.g. ``MMITNO``); export
   headers are lowercase (``mmitno``), so we map case-insensitively and return
   the *export's* names, which index the decoded rows.
2. **Heuristic** — when there is no usable metadata PK, identify a row by its
   full set of columns (``pk_source: "heuristic"``). Two rows then match only if
   every field matches, so the diff degrades to set membership (added/removed),
   never a false "modified".

**CONO masking** is the part CLAUDE.md flags as easiest to get subtly wrong:
dropping the company column from the comparison key so (500, ITEM001) matches
(100, ITEM001). Modeled as a set of columns to drop, so DIVI remap (v1.1,
ADR-010) slots in later without changing callers.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from .format.types import Row, TableHeader
from .schema.cache import SchemaCache
from .schema.models import SchemaResolution


@dataclass(frozen=True, slots=True)
class PrimaryKey:
    columns: tuple[str, ...]  # PK column names, in the export's casing
    source: str  # "metadata" | "heuristic"
    component: str | None = None
    component_ambiguous: bool = False
    maintained_by: str | None = None  # maintaining program (MDP), e.g. "CRS610"
    description: str | None = None  # table description (MDP), e.g. "MF: Item master"


def cono_column(header: TableHeader) -> str | None:
    """The export's company column name (first ``…cono`` match), or None."""
    indexes = header.cono_field_indexes()
    return header.names[indexes[0]] if indexes else None


def _align_to_header(pk_columns: tuple[str, ...], header: TableHeader) -> tuple[str, ...] | None:
    """Map metadata PK column names to the export's actual (cased) names.

    Returns None if any PK column is absent from the export — then the metadata
    PK can't key these rows and we fall back to the heuristic.
    """
    by_lower = {name.lower(): name for name in header.names}
    aligned: list[str] = []
    for column in pk_columns:
        actual = by_lower.get(column.lower())
        if actual is None:
            return None
        aligned.append(actual)
    return tuple(aligned)


def resolve_pk(
    table_name: str, header: TableHeader, cache: SchemaCache | None = None
) -> PrimaryKey:
    """Resolve the primary key for a table, metadata first then heuristic."""
    resolution = (
        cache.resolve(table_name)
        if cache is not None
        else SchemaResolution(schema=None, component=None, ambiguous=False)
    )
    # Known even when the PK falls back to heuristic — the schema still names
    # the maintaining program and describes the table.
    maintained_by = (resolution.schema.maintained_by or None) if resolution.schema else None
    description = (resolution.schema.description or None) if resolution.schema else None
    if resolution.schema is not None:
        aligned = _align_to_header(resolution.schema.primary_key, header)
        if aligned:
            return PrimaryKey(
                columns=aligned,
                source="metadata",
                component=resolution.component,
                component_ambiguous=resolution.ambiguous,
                maintained_by=maintained_by,
                description=description,
            )
    return PrimaryKey(
        columns=header.names,
        source="heuristic",
        component=resolution.component,
        component_ambiguous=resolution.ambiguous,
        maintained_by=maintained_by,
        description=description,
    )


def masked_key(row: Row, pk: PrimaryKey, drop: Collection[str] = ()) -> tuple[str | None, ...]:
    """Build a row's comparison key from its PK columns, dropping masked ones.

    ``drop`` is the set of columns to remove from the key (v1: the CONO column,
    via ``cono_column``). Dropped columns' values are irrelevant, which is what
    makes rows from different companies collide.
    """
    dropped = {column for column in drop if column is not None}
    return tuple(row.get(column) for column in pk.columns if column not in dropped)
