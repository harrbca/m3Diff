"""The diff result contract (spec §5) — the single source of truth (ADR-005).

These dataclasses define the result JSON. Additive changes are free; a breaking
change (rename/remove/retype) requires a `tool_version` bump and an ADR. The
serializer is deterministic: sorted keys, sorted change lists (done upstream in
`diff.py`), UTF-8, trailing newline — so identical inputs give byte-identical
output, modulo the caller-supplied `generated_at` and file labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RowRef:
    pk: list[str | None]  # the masked comparison key
    row: dict[str, str]  # the row's present fields


@dataclass(frozen=True, slots=True)
class FieldChange:
    a: str | None
    b: str | None


@dataclass(frozen=True, slots=True)
class ModRef:
    pk: list[str | None]
    changes: dict[str, FieldChange]


@dataclass(frozen=True, slots=True)
class ChangeCounts:
    added: int
    removed: int
    modified: int


@dataclass(frozen=True, slots=True)
class TableDiff:
    table_class: str  # serialized as "class"
    pk: list[str]
    pk_source: str  # "metadata" | "heuristic"
    schema_component: str | None
    component_ambiguous: bool
    schema_match: bool
    rows_a: int
    rows_b: int
    status: str  # identical | modified | missing_in_a | missing_in_b | error
    counts: ChangeCounts
    added: list[RowRef]
    removed: list[RowRef]
    modified: list[ModRef]
    truncated: bool  # embedded rows capped below the true counts
    global_subset: bool  # global mode: only the CONO-0 subset of a MIXED table
    modified_detail: bool  # False if downgraded to hash-only (huge table)
    # True when the metadata PK collided on this export's rows (a PK column
    # blank on the wire) and the table fell back to full-row identity.
    pk_degenerate: bool = False
    # Maintaining program from the schema metadata (e.g. OCUSMA → "CRS610"),
    # None when unknown. Triage hint: where to fix a drifted table.
    maintained_by: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SideInfo:
    file: str
    cono: str | None
    tables: int
    rows: int | None


@dataclass(frozen=True, slots=True)
class SettingsInfo:
    ignored_fields: list[str]
    null_equals_empty: bool
    pk_mask: list[str]


@dataclass(frozen=True, slots=True)
class Summary:
    tables_compared: int
    identical: int
    modified: int
    missing_in_a: int
    missing_in_b: int
    errors: int


@dataclass(frozen=True, slots=True)
class DiffResult:
    tool_version: str
    mode: str
    generated_at: str
    a: SideInfo
    b: SideInfo
    settings: SettingsInfo
    summary: Summary
    tables: dict[str, TableDiff]


def _side_to_dict(side: SideInfo) -> dict[str, Any]:
    return {"file": side.file, "cono": side.cono, "tables": side.tables, "rows": side.rows}


def _rowref_to_dict(ref: RowRef) -> dict[str, Any]:
    return {"pk": ref.pk, "row": ref.row}


def _modref_to_dict(ref: ModRef) -> dict[str, Any]:
    return {
        "pk": ref.pk,
        "changes": {name: {"a": ch.a, "b": ch.b} for name, ch in ref.changes.items()},
    }


def _table_to_dict(table: TableDiff) -> dict[str, Any]:
    return {
        "class": table.table_class,
        "pk": table.pk,
        "pk_source": table.pk_source,
        "schema_component": table.schema_component,
        "component_ambiguous": table.component_ambiguous,
        "schema_match": table.schema_match,
        "rows_a": table.rows_a,
        "rows_b": table.rows_b,
        "status": table.status,
        "counts": {
            "added": table.counts.added,
            "removed": table.counts.removed,
            "modified": table.counts.modified,
        },
        "added": [_rowref_to_dict(r) for r in table.added],
        "removed": [_rowref_to_dict(r) for r in table.removed],
        "modified": [_modref_to_dict(m) for m in table.modified],
        "truncated": table.truncated,
        "global_subset": table.global_subset,
        "modified_detail": table.modified_detail,
        "pk_degenerate": table.pk_degenerate,
        "maintained_by": table.maintained_by,
        "error": table.error,
    }


def to_dict(result: DiffResult) -> dict[str, Any]:
    return {
        "tool_version": result.tool_version,
        "mode": result.mode,
        "generated_at": result.generated_at,
        "a": _side_to_dict(result.a),
        "b": _side_to_dict(result.b),
        "settings": {
            "ignored_fields": result.settings.ignored_fields,
            "null_equals_empty": result.settings.null_equals_empty,
            "pk_mask": result.settings.pk_mask,
        },
        "summary": {
            "tables_compared": result.summary.tables_compared,
            "identical": result.summary.identical,
            "modified": result.summary.modified,
            "missing_in_a": result.summary.missing_in_a,
            "missing_in_b": result.summary.missing_in_b,
            "errors": result.summary.errors,
        },
        "tables": {name: _table_to_dict(td) for name, td in result.tables.items()},
    }


def to_json(result: DiffResult) -> str:
    """Serialize to the canonical, deterministic result JSON (with trailing newline)."""
    return json.dumps(to_dict(result), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
