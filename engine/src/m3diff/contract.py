"""The diff result contract (spec §5) — the single source of truth (ADR-005).

These dataclasses define the result JSON. Additive changes are free; a breaking
change (rename/remove/retype) requires a `tool_version` bump and an ADR. The
serializer is deterministic: sorted keys, sorted change lists (done upstream in
`diff.py`), UTF-8, trailing newline — so identical inputs give byte-identical
output, modulo the caller-supplied `generated_at` and file labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    # blank on the wire). With pk_source "metadata" the per-key retry handled it
    # (ADR-025; see ambiguous_keys) and clean keys kept field-level detail; with
    # pk_source "heuristic" the whole table fell back to full-row identity
    # (too large to hold per-key, or written by a pre-ADR-025 version).
    pk_degenerate: bool = False
    # Distinct masked keys that had more than one row on a side (the ambiguous
    # groups the per-key retry compared by full row). Non-zero only when
    # pk_degenerate and pk_source == "metadata".
    ambiguous_keys: int = 0
    # Maintaining program from the schema metadata (e.g. OCUSMA → "CRS610"),
    # None when unknown. Triage hint: where to fix a drifted table.
    maintained_by: str | None = None
    # Table description from the schema metadata (e.g. "MF: Item master"),
    # None when the table isn't in the schema cache.
    description: str | None = None
    # Column descriptions (MDP) for this table's compared columns, keyed by the
    # export's column casing (e.g. "mmitds" -> "Item description"). Attached only
    # when there's field-level detail to annotate (status "modified"); empty
    # otherwise. Lets the drill-down label change rows and keeps the JSON
    # self-describing for downstream consumers (ADR-023).
    column_descriptions: dict[str, str] = field(default_factory=dict)
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
        "ambiguous_keys": table.ambiguous_keys,
        "maintained_by": table.maintained_by,
        "description": table.description,
        "column_descriptions": table.column_descriptions,
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


# --- deserialization (render RPC, future results-history) --------------------
def _side_from_dict(d: dict[str, Any]) -> SideInfo:
    return SideInfo(file=d["file"], cono=d.get("cono"), tables=d["tables"], rows=d.get("rows"))


def _table_from_dict(d: dict[str, Any]) -> TableDiff:
    return TableDiff(
        table_class=d["class"],
        pk=list(d["pk"]),
        pk_source=d["pk_source"],
        schema_component=d.get("schema_component"),
        component_ambiguous=d["component_ambiguous"],
        schema_match=d["schema_match"],
        rows_a=d["rows_a"],
        rows_b=d["rows_b"],
        status=d["status"],
        counts=ChangeCounts(**d["counts"]),
        added=[RowRef(pk=list(r["pk"]), row=dict(r["row"])) for r in d["added"]],
        removed=[RowRef(pk=list(r["pk"]), row=dict(r["row"])) for r in d["removed"]],
        modified=[
            ModRef(
                pk=list(m["pk"]),
                changes={k: FieldChange(a=c["a"], b=c["b"]) for k, c in m["changes"].items()},
            )
            for m in d["modified"]
        ],
        truncated=d["truncated"],
        global_subset=d["global_subset"],
        modified_detail=d["modified_detail"],
        # .get: additive fields absent from result JSON written by older versions
        pk_degenerate=d.get("pk_degenerate", False),
        ambiguous_keys=d.get("ambiguous_keys", 0),
        maintained_by=d.get("maintained_by"),
        description=d.get("description"),
        column_descriptions=dict(d.get("column_descriptions") or {}),
        error=d.get("error"),
    )


def from_dict(data: dict[str, Any]) -> DiffResult:
    """Rebuild a DiffResult from its ``to_dict`` form. Inverse of ``to_dict``:
    ``to_json(from_dict(d)) == to_json(result)`` for any result this version
    wrote; additive fields missing from older JSON take their defaults."""
    return DiffResult(
        tool_version=data["tool_version"],
        mode=data["mode"],
        generated_at=data["generated_at"],
        a=_side_from_dict(data["a"]),
        b=_side_from_dict(data["b"]),
        settings=SettingsInfo(
            ignored_fields=list(data["settings"]["ignored_fields"]),
            null_equals_empty=data["settings"]["null_equals_empty"],
            pk_mask=list(data["settings"]["pk_mask"]),
        ),
        summary=Summary(**data["summary"]),
        tables={name: _table_from_dict(td) for name, td in data["tables"].items()},
    )
