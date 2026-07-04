"""The diff engine (spec §3.3) — a pure library over ExportSource.

``compare()`` returns a ``DiffResult`` (contract.py); the CLI and GUI both call
it and serialize identically. Per table it:

- resolves the PK (metadata, else heuristic) and masks the CONO column,
- selects each side's in-scope rows (by company, or the global subset),
- indexes one side and streams the other, diffing by masked key,
- compares fields on the schema intersection, skipping the ignore-list.

Memory is bounded per table: only the indexed side is held. For a table whose
in-scope side exceeds ``hash_downgrade_threshold``, the index switches to
key→signature-hash — added/removed stay exact, field-level "modified" detail is
dropped (``modified_detail: false``), and peak memory stays near the threshold.
"""
from __future__ import annotations

import fnmatch
import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import BinaryIO

from . import __version__
from .cono import GLOBAL_CONO, cono_of_row, normalize_cono
from .contract import (
    ChangeCounts,
    DiffResult,
    FieldChange,
    ModRef,
    RowRef,
    SettingsInfo,
    SideInfo,
    Summary,
    TableDiff,
)
from .format.types import ExportFormatError, Row, TableHeader
from .format.reader import read_table
from .pk import PrimaryKey, cono_column, masked_key, resolve_pk
from .schema.cache import SchemaCache
from .source import ExportSource

# Change-timestamp / bookkeeping fields that otherwise generate 100% noise (spec §3.3).
DEFAULT_IGNORED_FIELDS: tuple[str, ...] = ("*lmdt", "*rgdt", "*rgtm", "*lmts", "*chno", "*chid")

MODE_INTRA = "intra"
MODE_INTER = "inter"
MODE_GLOBAL = "global"


class CompareCancelled(Exception):
    """Raised when a compare is cancelled between tables (spec F5)."""


@dataclass(frozen=True, slots=True)
class CompareOptions:
    mode: str
    cono_a: str | None = None
    cono_b: str | None = None
    tables: tuple[str, ...] | None = None  # names/globs; None = every table present
    ignored_fields: tuple[str, ...] = DEFAULT_IGNORED_FIELDS
    null_equals_empty: bool = True
    mask_cono: bool = True
    cache: SchemaCache | None = None
    max_rows_per_change: int = 1000
    hash_downgrade_threshold: int = 200_000


# --- scoping and CONO filtering ---------------------------------------------
def _resolve_scope(patterns: tuple[str, ...] | None, names: Iterable[str]) -> list[str]:
    all_names = sorted(set(names))
    if patterns is None:
        return all_names
    selected: set[str] = set()
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            selected.update(n for n in all_names if fnmatch.fnmatchcase(n, pattern))
        elif pattern in all_names:
            selected.add(pattern)
    return sorted(selected)


def _in_scope(cono: str, cono_field: str | None, target: str | None, mode: str) -> bool:
    if mode == MODE_GLOBAL:
        return cono_field is None or cono == GLOBAL_CONO
    if cono_field is None:
        return True  # NO_CONO table: tenant-wide rows belong to both sides
    return cono == target


def _classify(cono_tally: Counter[str], cono_field: str | None, rows: int) -> str:
    if rows == 0:
        return "EMPTY"
    if cono_field is None:
        return "NO_CONO"
    global_rows = cono_tally.get(GLOBAL_CONO, 0)
    company_rows = rows - global_rows
    if global_rows and company_rows:
        return "MIXED"
    return "GLOBAL" if global_rows else "COMPANY"


# --- field comparison -------------------------------------------------------
def _ignored(name: str, patterns: tuple[str, ...], cono_fields: frozenset[str]) -> bool:
    if name in cono_fields:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _values_equal(a: str | None, b: str | None, null_equals_empty: bool) -> bool:
    if a == b:
        return True
    if null_equals_empty:
        return a in (None, "") and b in (None, "")
    return False


def _normalize(value: str | None, null_equals_empty: bool) -> str | None:
    if null_equals_empty and (value is None or value == ""):
        return ""
    return value


def _field_diff(
    row_a: Row, row_b: Row, columns: list[str], null_equals_empty: bool
) -> dict[str, FieldChange]:
    changes: dict[str, FieldChange] = {}
    for col in columns:
        va, vb = row_a.get(col), row_b.get(col)
        if not _values_equal(va, vb, null_equals_empty):
            changes[col] = FieldChange(a=va, b=vb)
    return changes


def _signature(row: Row, columns: list[str], null_equals_empty: bool) -> bytes:
    payload = repr([_normalize(row.get(c), null_equals_empty) for c in columns]).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).digest()


def _pk_sort_key(pk: tuple[str | None, ...]) -> tuple[str, ...]:
    return tuple("" if v is None else v for v in pk)


# --- per-table diff ---------------------------------------------------------
@dataclass(slots=True)
class _Accumulator:
    added: list[tuple[tuple[str | None, ...], Row]] = field(default_factory=list)
    removed: list[tuple[tuple[str | None, ...], Row]] = field(default_factory=list)
    modified: list[tuple[tuple[str | None, ...], dict[str, FieldChange]]] = field(default_factory=list)


def _index_side_a(
    rows: Iterator[Row],
    pk: PrimaryKey,
    drop: frozenset[str],
    cono_field: str | None,
    target: str | None,
    mode: str,
    compare_cols: list[str],
    null_equals_empty: bool,
    threshold: int,
) -> tuple[dict, bool, Counter, int]:
    """Index side A's in-scope rows. Returns (index, downgraded, cono_tally, rows_in_scope).

    ``index`` maps masked key -> Row (full mode) or -> signature bytes (downgraded).
    """
    index: dict = {}
    downgraded = False
    cono_tally: Counter[str] = Counter()
    in_scope = 0
    for row in rows:
        cono = cono_of_row(row, cono_field)
        cono_tally[cono] += 1
        if not _in_scope(cono, cono_field, target, mode):
            continue
        in_scope += 1
        key = masked_key(row, pk, drop)
        if downgraded:
            index[key] = _signature(row, compare_cols, null_equals_empty)
        else:
            index[key] = row
            if len(index) > threshold:
                index = {k: _signature(r, compare_cols, null_equals_empty) for k, r in index.items()}
                downgraded = True
    return index, downgraded, cono_tally, in_scope


def _diff_one(name: str, a: ExportSource, b: ExportSource, opt: CompareOptions) -> TableDiff | None:
    with a.open_table(name) as stream_a, b.open_table(name) as stream_b:
        header_a, rows_a_iter = read_table(stream_a)
        header_b, rows_b_iter = read_table(stream_b)

        cono_field_a = cono_column(header_a)
        cono_field_b = cono_column(header_b)
        pk = resolve_pk(name, header_a, opt.cache)
        drop_a = frozenset({cono_field_a} - {None}) if opt.mask_cono else frozenset()
        drop_b = frozenset({cono_field_b} - {None}) if opt.mask_cono else frozenset()

        names_b = set(header_b.names)
        schema_match = set(header_a.names) == names_b
        cono_fields = frozenset({cono_field_a, cono_field_b} - {None})
        compare_cols = [
            c
            for c in header_a.names
            if c in names_b and not _ignored(c, opt.ignored_fields, cono_fields)
        ]

        index, downgraded, cono_tally, rows_a = _index_side_a(
            rows_a_iter, pk, drop_a, cono_field_a, opt.cono_a, opt.mode,
            compare_cols, opt.null_equals_empty, opt.hash_downgrade_threshold,
        )
        table_class = _classify(cono_tally, cono_field_a, sum(cono_tally.values()))

        # Global mode only compares tenant-wide data; skip pure company tables.
        if opt.mode == MODE_GLOBAL and table_class == "COMPANY":
            return None

        acc = _Accumulator()
        seen: set = set()
        rows_b = 0
        for row in rows_b_iter:
            cono = cono_of_row(row, cono_field_b)
            if not _in_scope(cono, cono_field_b, opt.cono_b, opt.mode):
                continue
            rows_b += 1
            key = masked_key(row, pk, drop_b)
            if key not in index:
                acc.added.append((key, row))
                continue
            seen.add(key)
            if downgraded:
                if index[key] != _signature(row, compare_cols, opt.null_equals_empty):
                    acc.modified.append((key, {}))
            else:
                changes = _field_diff(index[key], row, compare_cols, opt.null_equals_empty)
                if changes:
                    acc.modified.append((key, changes))

        for key, value in index.items():
            if key not in seen:
                removed_row: Row = {} if downgraded else value
                acc.removed.append((key, removed_row))

    return _build_table_diff(
        name, pk, header_a, table_class, schema_match, rows_a, rows_b, acc, downgraded, opt
    )


def _build_table_diff(
    name: str,
    pk: PrimaryKey,
    header_a: TableHeader,
    table_class: str,
    schema_match: bool,
    rows_a: int,
    rows_b: int,
    acc: _Accumulator,
    downgraded: bool,
    opt: CompareOptions,
) -> TableDiff:
    acc.added.sort(key=lambda e: _pk_sort_key(e[0]))
    acc.removed.sort(key=lambda e: _pk_sort_key(e[0]))
    acc.modified.sort(key=lambda e: _pk_sort_key(e[0]))

    counts = ChangeCounts(len(acc.added), len(acc.removed), len(acc.modified))
    cap = opt.max_rows_per_change
    truncated = counts.added > cap or counts.removed > cap or counts.modified > cap

    added = [RowRef(pk=list(k), row=r) for k, r in acc.added[:cap]]
    removed = [RowRef(pk=list(k), row=r) for k, r in acc.removed[:cap]]
    modified = [ModRef(pk=list(k), changes=ch) for k, ch in acc.modified[:cap]]

    if rows_a and not rows_b:
        status = "missing_in_b"
    elif rows_b and not rows_a:
        status = "missing_in_a"
    elif acc.added or acc.removed or acc.modified:
        status = "modified"
    else:
        status = "identical"

    return TableDiff(
        table_class=table_class,
        pk=list(pk.columns),
        pk_source=pk.source,
        schema_component=pk.component,
        component_ambiguous=pk.component_ambiguous,
        schema_match=schema_match,
        rows_a=rows_a,
        rows_b=rows_b,
        status=status,
        counts=counts,
        added=added,
        removed=removed,
        modified=modified,
        truncated=truncated,
        global_subset=(opt.mode == MODE_GLOBAL and table_class == "MIXED"),
        modified_detail=not downgraded,
        error=None,
    )


def _one_sided(name: str, source: ExportSource, present: str, opt: CompareOptions) -> TableDiff:
    """A table present on only one side: everything is removed (A-only) or added (B-only)."""
    with source.open_table(name) as stream:
        header, rows_iter = read_table(stream)
        cono_field = cono_column(header)
        pk = resolve_pk(name, header, opt.cache)
        drop = frozenset({cono_field} - {None}) if opt.mask_cono else frozenset()
        target = opt.cono_a if present == "a" else opt.cono_b
        acc = _Accumulator()
        cono_tally: Counter[str] = Counter()
        rows = 0
        for row in rows_iter:
            cono = cono_of_row(row, cono_field)
            cono_tally[cono] += 1
            if not _in_scope(cono, cono_field, target, opt.mode):
                continue
            rows += 1
            key = masked_key(row, pk, drop)
            (acc.removed if present == "a" else acc.added).append((key, row))
        table_class = _classify(cono_tally, cono_field, sum(cono_tally.values()))
    rows_a, rows_b = (rows, 0) if present == "a" else (0, rows)
    return _build_table_diff(
        name, pk, header, table_class, True, rows_a, rows_b, acc, False, opt
    )


def _error_table(name: str, exc: Exception) -> TableDiff:
    return TableDiff(
        table_class="", pk=[], pk_source="", schema_component=None, component_ambiguous=False,
        schema_match=False, rows_a=0, rows_b=0, status="error",
        counts=ChangeCounts(0, 0, 0), added=[], removed=[], modified=[],
        truncated=False, global_subset=False, modified_detail=True, error=str(exc),
    )


# --- public entry point -----------------------------------------------------
def _side_info(source: ExportSource, label: str, cono: str | None) -> SideInfo:
    manifest = source.table_info()
    rows = sum(e.record_count for e in manifest) if manifest is not None else None
    return SideInfo(file=label, cono=cono, tables=len(source.table_names()), rows=rows)


def _summarize(tables: dict[str, TableDiff]) -> Summary:
    status = Counter(td.status for td in tables.values())
    return Summary(
        tables_compared=len(tables),
        identical=status.get("identical", 0),
        modified=status.get("modified", 0),
        missing_in_a=status.get("missing_in_a", 0),
        missing_in_b=status.get("missing_in_b", 0),
        errors=status.get("error", 0),
    )


def compare(
    a: ExportSource,
    b: ExportSource | None,
    options: CompareOptions,
    *,
    tool_version: str = __version__,
    generated_at: str = "",
    a_label: str = "a",
    b_label: str = "b",
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> DiffResult:
    """Compare two exports (or two companies within one) into a DiffResult.

    ``progress(done, total, table)`` is called after each table; ``cancelled()``
    is polled before each table and, if it returns True, raises CompareCancelled.
    """
    b_source = b if b is not None else a
    a_names = set(a.table_names())
    b_names = set(b_source.table_names())
    scoped = _resolve_scope(options.tables, a_names | b_names)
    total = len(scoped)

    tables: dict[str, TableDiff] = {}
    for index, name in enumerate(scoped, start=1):
        if cancelled is not None and cancelled():
            raise CompareCancelled(f"cancelled after {index - 1}/{total} tables")
        try:
            if name in a_names and name in b_names:
                diff = _diff_one(name, a, b_source, options)  # None => skipped in global mode
            elif name in a_names:
                diff = _one_sided(name, a, "a", options)
            else:
                diff = _one_sided(name, b_source, "b", options)
        except ExportFormatError as exc:
            diff = _error_table(name, exc)
        if diff is not None:
            tables[name] = diff
        if progress is not None:
            progress(index, total, name)

    cono_a = None if options.mode == MODE_GLOBAL else options.cono_a
    cono_b = None if options.mode == MODE_GLOBAL else options.cono_b
    return DiffResult(
        tool_version=tool_version,
        mode=options.mode,
        generated_at=generated_at,
        a=_side_info(a, a_label, cono_a),
        b=_side_info(b_source, b_label, cono_b),
        settings=SettingsInfo(
            ignored_fields=list(options.ignored_fields),
            null_equals_empty=options.null_equals_empty,
            pk_mask=["CONO"] if options.mask_cono else [],
        ),
        summary=_summarize(tables),
        tables=tables,
    )
