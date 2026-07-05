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
import logging
import os
import sys
import time
import types
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field, replace
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
from .source import ExportSource, open_export

# Change-timestamp / bookkeeping fields that otherwise generate 100% noise (spec §3.3).
DEFAULT_IGNORED_FIELDS: tuple[str, ...] = ("*lmdt", "*rgdt", "*rgtm", "*lmts", "*chno", "*chid")

_log = logging.getLogger("m3diff.diff")

MODE_INTRA = "intra"
MODE_INTER = "inter"
MODE_GLOBAL = "global"

# Below this many in-scope tables the process-pool startup cost outweighs the
# win, so ``workers=0`` (auto) stays serial. An explicit ``workers>1`` overrides.
_MIN_PARALLEL_TABLES = 4

# How long a healthy pool gets to run the liveness canary (a trivial no-op
# task). Worker spawn + zip re-open takes a few seconds; 15s is comfortable.
_CANARY_GRACE = 15.0

# Sticky per-process flag: set when a canary times out, after which this
# process never tries a pool again (only the first compare pays the grace
# wait). Root cause of the wedge the canary guards against is fixed by
# _patch_worker_console_flags below; the canary stays as a safety net.
_pool_unavailable = False

_CREATE_NO_WINDOW = 0x08000000
_worker_flags_patched = False


def _patch_worker_console_flags() -> None:
    """Windows: start pool workers with CREATE_NO_WINDOW (own hidden console).

    Root cause (ADR-020, minimal repro in the ADR): on Windows + CPython 3.14 a
    blocking read on *piped stdin* in one thread deadlocks the multiprocessing
    spawn handshake of a console-SHARING child created from another thread —
    the child freezes attaching the parent's console before it executes any
    Python. That is exactly the serve process's shape (main thread reads the
    NDJSON stdin pipe; compares run on a task thread). Children given their own
    console lifecycle start normally, so add CREATE_NO_WINDOW to the worker
    CreateProcess call.

    The patch is surgical: ``popen_spawn_win32`` gets a shim module whose
    ``CreateProcess`` ORs the flag in — the real ``_winapi`` used by
    ``subprocess`` and everyone else is untouched. Idempotent; a failure leaves
    things unpatched (the liveness canary still protects correctness then).
    """
    global _worker_flags_patched
    if _worker_flags_patched or sys.platform != "win32":
        return
    try:
        import _winapi
        import multiprocessing.popen_spawn_win32 as psw

        original = psw._winapi.CreateProcess

        def _create_process_no_window(app, cmd, pattr, tattr, inherit, flags, env, cwd, si):
            return original(app, cmd, pattr, tattr, inherit, flags | _CREATE_NO_WINDOW, env, cwd, si)

        shim = types.ModuleType("m3diff._winapi_no_window_shim")
        shim.__dict__.update(_winapi.__dict__)
        shim.CreateProcess = _create_process_no_window
        psw._winapi = shim
        _worker_flags_patched = True
    except Exception:  # stdlib layout changed: stay unpatched, canary covers us
        pass


class CompareCancelled(Exception):
    """Raised when a compare is cancelled between tables (spec F5)."""


class _DegeneratePkError(Exception):
    """The metadata PK failed to uniquely key this export's rows.

    Happens when a PK column is blank on the wire (seen in the field: MITBAL
    exports with ``mbwhlo`` empty; CUGEX1/CSYTAB rows with blank key columns),
    collapsing the masked key so distinct rows collide. Keying on it would
    silently overwrite rows and produce a wrong diff — the table is retried
    per key (ADR-025), or with whole-table full-row identity when too large.
    """


class _GroupedRetryTooLarge(Exception):
    """The per-key retry would hold too many rows; use full-row identity."""


@dataclass(frozen=True, slots=True)
class CompareOptions:
    mode: str
    cono_a: str | None = None
    cono_b: str | None = None
    tables: tuple[str, ...] | None = None  # names/globs; None = every table present
    # Metadata categories to scope to (e.g. ("MF",) per ADR-006); requires a
    # cache. Unions with ``tables``: a table is in scope if either selects it.
    categories: tuple[str, ...] | None = None
    ignored_fields: tuple[str, ...] = DEFAULT_IGNORED_FIELDS
    null_equals_empty: bool = True
    mask_cono: bool = True
    cache: SchemaCache | None = None
    max_rows_per_change: int = 1000
    hash_downgrade_threshold: int = 200_000
    # Diff worker processes. 1 = serial (default); 0 = auto (all cores, when the
    # inputs are file-backed and there are enough tables); N>1 = force N.
    workers: int = 1


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


def _scope_tables(options: CompareOptions, names: set[str]) -> list[str]:
    """The in-scope table names: name/glob patterns ∪ metadata categories (ADR-016).

    No filter at all means every table present. Category scoping needs the
    schema cache (categories live there); tables absent from the cache have no
    category and can only be selected by name/glob.
    """
    if options.tables is None and not options.categories:
        return sorted(names)
    selected: set[str] = set()
    if options.tables is not None:
        selected.update(_resolve_scope(options.tables, names))
    if options.categories:
        if options.cache is None:
            raise ValueError("category scoping requires a schema cache (--schema-db)")
        selected.update(options.cache.tables_in_categories(options.categories) & names)
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
    detect_collisions: bool,
) -> tuple[dict, bool, Counter, int]:
    """Index side A's in-scope rows. Returns (index, downgraded, cono_tally, rows_in_scope).

    ``index`` maps masked key -> Row (full mode) or -> signature bytes (downgraded).
    With ``detect_collisions`` (metadata PKs), a repeated key means the PK does not
    key this data — raises _DegeneratePkError rather than silently overwriting rows.
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
        if detect_collisions and key in index:
            raise _DegeneratePkError(f"duplicate masked key {key!r}")
        if downgraded:
            index[key] = _signature(row, compare_cols, null_equals_empty)
        else:
            index[key] = row
            if len(index) > threshold:
                index = {k: _signature(r, compare_cols, null_equals_empty) for k, r in index.items()}
                downgraded = True
    return index, downgraded, cono_tally, in_scope


def _diff_one(name: str, a: ExportSource, b: ExportSource, opt: CompareOptions) -> TableDiff | None:
    try:
        return _diff_one_pass(name, a, b, opt, force_heuristic=False)
    except _DegeneratePkError as exc:
        # The metadata PK collided on real rows (a PK column blank on the wire).
        # Retry per key (ADR-025): clean keys keep field-level detail; only the
        # ambiguous key groups degrade to set membership. A table too large to
        # hold both sides falls back to whole-table full-row identity, which
        # has no field detail either way.
        _log.info("table %s: degenerate metadata PK (%s); retrying per-key", name, exc)
        try:
            return _diff_one_grouped(name, a, b, opt)
        except _GroupedRetryTooLarge:
            _log.info("table %s: per-key retry over threshold; using full-row identity", name)
            return _diff_one_pass(name, a, b, opt, force_heuristic=True)


def _diff_one_pass(
    name: str, a: ExportSource, b: ExportSource, opt: CompareOptions, force_heuristic: bool
) -> TableDiff | None:
    with a.open_table(name) as stream_a, b.open_table(name) as stream_b:
        header_a, rows_a_iter = read_table(stream_a)
        header_b, rows_b_iter = read_table(stream_b)

        cono_field_a = cono_column(header_a)
        cono_field_b = cono_column(header_b)
        pk = resolve_pk(name, header_a, opt.cache)
        degenerate = False
        if force_heuristic and pk.source == "metadata":
            # Fall back to full-row identity, but keep every schema-derived field
            # (description, column_descriptions, maintained_by, component); only
            # the key columns and source change. Rebuilding from scratch here is
            # what dropped the description for degenerate-PK tables (ADR-022/023).
            pk = replace(pk, columns=header_a.names, source="heuristic")
            degenerate = True
        # Only a metadata PK claims uniqueness; full-row identity treats exact
        # duplicate rows as one, which cannot recurse into another fallback.
        detect = pk.source == "metadata"
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
            detect,
        )
        table_class = _classify(cono_tally, cono_field_a, sum(cono_tally.values()))

        # Global mode only compares tenant-wide data; skip pure company tables.
        if opt.mode == MODE_GLOBAL and table_class == "COMPANY":
            return None

        acc = _Accumulator()
        seen: set = set()
        b_keys: set = set()  # B-side collision detection (metadata PKs only)
        rows_b = 0
        for row in rows_b_iter:
            cono = cono_of_row(row, cono_field_b)
            if not _in_scope(cono, cono_field_b, opt.cono_b, opt.mode):
                continue
            rows_b += 1
            key = masked_key(row, pk, drop_b)
            if detect:
                if key in b_keys:
                    raise _DegeneratePkError(f"duplicate masked key {key!r} on side B")
                b_keys.add(key)
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

    col_desc = {c: pk.column_descriptions[c] for c in compare_cols if c in pk.column_descriptions}
    return _build_table_diff(
        name, pk, header_a, table_class, schema_match, rows_a, rows_b, acc, downgraded, opt,
        pk_degenerate=degenerate, column_descriptions=col_desc,
    )


def _diff_one_grouped(name: str, a: ExportSource, b: ExportSource, opt: CompareOptions) -> TableDiff | None:
    """Per-key retry for a degenerate metadata PK (ADR-025).

    The metadata PK stays the row identity. A key selecting exactly one row per
    side gets the normal field-level diff; an *ambiguous* key (more than one row
    on a side — PK columns blank on the wire) is compared by set membership
    within its group, matching rows on the compared-columns signature so
    ignored fields cannot fabricate adds/removes. Only the ambiguous groups
    lose "modified" detail, not the whole table.

    Both sides' in-scope rows are held in memory (key → [rows]), so the retry
    refuses tables beyond ``hash_downgrade_threshold`` rows per side
    (_GroupedRetryTooLarge) — the caller then uses whole-table full-row
    identity, which drops field detail past that size anyway.
    """
    with a.open_table(name) as stream_a, b.open_table(name) as stream_b:
        header_a, rows_a_iter = read_table(stream_a)
        header_b, rows_b_iter = read_table(stream_b)

        cono_field_a = cono_column(header_a)
        cono_field_b = cono_column(header_b)
        pk = resolve_pk(name, header_a, opt.cache)  # metadata — it just collided
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
        limit = opt.hash_downgrade_threshold

        def collect(rows, cono_field, target, drop):
            groups: dict[tuple[str | None, ...], list[Row]] = {}
            tally: Counter[str] = Counter()
            in_scope = 0
            for row in rows:
                cono = cono_of_row(row, cono_field)
                tally[cono] += 1
                if not _in_scope(cono, cono_field, target, opt.mode):
                    continue
                in_scope += 1
                if in_scope > limit:
                    raise _GroupedRetryTooLarge(name)
                groups.setdefault(masked_key(row, pk, drop), []).append(row)
            return groups, tally, in_scope

        groups_a, cono_tally, rows_a = collect(rows_a_iter, cono_field_a, opt.cono_a, drop_a)
        table_class = _classify(cono_tally, cono_field_a, sum(cono_tally.values()))
        if opt.mode == MODE_GLOBAL and table_class == "COMPANY":
            return None
        groups_b, _, rows_b = collect(rows_b_iter, cono_field_b, opt.cono_b, drop_b)

    acc = _Accumulator()
    ambiguous = 0
    for key, group_a in groups_a.items():
        group_b = groups_b.get(key)
        if group_b is None:
            if len(group_a) > 1:
                ambiguous += 1
            acc.removed.extend((key, row) for row in group_a)
            continue
        if len(group_a) == 1 and len(group_b) == 1:  # clean key: full field diff
            changes = _field_diff(group_a[0], group_b[0], compare_cols, opt.null_equals_empty)
            if changes:
                acc.modified.append((key, changes))
            continue
        # Ambiguous group: multiset match on the compared-columns signature.
        ambiguous += 1
        unmatched: dict[bytes, list[Row]] = {}
        for row in group_b:
            unmatched.setdefault(_signature(row, compare_cols, opt.null_equals_empty), []).append(row)
        for row in group_a:
            sig = _signature(row, compare_cols, opt.null_equals_empty)
            bucket = unmatched.get(sig)
            if bucket:
                bucket.pop()
                if not bucket:
                    del unmatched[sig]
            else:
                acc.removed.append((key, row))
        for bucket in unmatched.values():
            acc.added.extend((key, row) for row in bucket)
    for key, group_b in groups_b.items():
        if key in groups_a:
            continue
        if len(group_b) > 1:
            ambiguous += 1
        acc.added.extend((key, row) for row in group_b)

    col_desc = {c: pk.column_descriptions[c] for c in compare_cols if c in pk.column_descriptions}
    return _build_table_diff(
        name, pk, header_a, table_class, schema_match, rows_a, rows_b, acc, False, opt,
        pk_degenerate=True, ambiguous_keys=ambiguous, column_descriptions=col_desc,
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
    *,
    pk_degenerate: bool = False,
    ambiguous_keys: int = 0,
    column_descriptions: dict[str, str] | None = None,
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

    # Column descriptions annotate field-level changes, which only "modified"
    # tables carry — so attach them there and nowhere else, keeping the identical
    # majority lean in the result JSON.
    col_desc = column_descriptions if (column_descriptions and status == "modified") else {}

    return TableDiff(
        table_class=table_class,
        pk=list(pk.columns),
        pk_source=pk.source,
        schema_component=pk.component,
        component_ambiguous=pk.component_ambiguous,
        maintained_by=pk.maintained_by,
        description=pk.description,
        column_descriptions=col_desc,
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
        pk_degenerate=pk_degenerate,
        ambiguous_keys=ambiguous_keys,
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
        truncated=False, global_subset=False, modified_detail=True, pk_degenerate=False,
        error=str(exc),
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


def _diff_dispatch(
    name: str,
    a: ExportSource,
    b_source: ExportSource,
    a_names: set[str],
    b_names: set[str],
    options: CompareOptions,
) -> TableDiff | None:
    """Diff one table. Returns None when the table is skipped (global-mode COMPANY).

    A parse error becomes an ``error`` TableDiff (spec F6); any other exception
    propagates, so a real bug fails the run instead of being silently swallowed.
    Shared verbatim by the serial and parallel drivers so both behave identically.
    """
    try:
        if name in a_names and name in b_names:
            return _diff_one(name, a, b_source, options)
        if name in a_names:
            return _one_sided(name, a, "a", options)
        return _one_sided(name, b_source, "b", options)
    except ExportFormatError as exc:
        return _error_table(name, exc)


def _compare_serial(
    scoped: list[str],
    a: ExportSource,
    b_source: ExportSource,
    a_names: set[str],
    b_names: set[str],
    options: CompareOptions,
    total: int,
    progress: Callable[[int, int, str], None] | None,
    cancelled: Callable[[], bool] | None,
) -> dict[str, TableDiff]:
    tables: dict[str, TableDiff] = {}
    for index, name in enumerate(scoped, start=1):
        if cancelled is not None and cancelled():
            raise CompareCancelled(f"cancelled after {index - 1}/{total} tables")
        diff = _diff_dispatch(name, a, b_source, a_names, b_names, options)
        if diff is not None:
            tables[name] = diff
        if progress is not None:
            progress(index, total, name)
    return tables


# --- parallel driver --------------------------------------------------------
# Each worker process re-opens the exports and (read-only) schema cache once, in
# its initializer, and keeps them here. Only paths cross the process boundary —
# a live zip handle or a SQLite connection is not picklable. What comes back is
# an already-truncated TableDiff, so IPC never carries raw rows.
_WORKER: dict[str, object] = {}


def _init_worker(
    a_origin: str, b_origin: str, cache_path: str | None, options: CompareOptions
) -> None:  # pragma: no cover - runs in a child process
    a_src = open_export(a_origin)
    b_src = a_src if b_origin == a_origin else open_export(b_origin)
    cache = SchemaCache(cache_path) if cache_path is not None else None
    _WORKER["a"] = a_src
    _WORKER["b"] = b_src
    _WORKER["opt"] = replace(options, cache=cache)
    _WORKER["a_names"] = set(a_src.table_names())
    _WORKER["b_names"] = set(b_src.table_names())


def _worker_one(name: str) -> TableDiff | None:  # pragma: no cover - runs in a child process
    return _diff_dispatch(
        name,
        _WORKER["a"],  # type: ignore[arg-type]
        _WORKER["b"],  # type: ignore[arg-type]
        _WORKER["a_names"],  # type: ignore[arg-type]
        _WORKER["b_names"],  # type: ignore[arg-type]
        _WORKER["opt"],  # type: ignore[arg-type]
    )


def _worker_canary() -> bool:  # pragma: no cover - runs in a child process
    return True


def _pool_is_live(
    executor: ProcessPoolExecutor, cancelled: Callable[[], bool] | None, grace: float
) -> bool:
    """True if the pool runs a trivial task within ``grace`` seconds.

    Guards against the wedged-spawn-handshake environment (see
    ``_pool_unavailable``): rather than hang forever on a pool that will never
    produce a result, prove it alive first. Polls ``cancelled`` while waiting.
    """
    canary = executor.submit(_worker_canary)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            raise CompareCancelled("cancelled during worker startup")
        try:
            return bool(canary.result(timeout=1.0))
        except TimeoutError:
            continue
        except Exception:  # BrokenProcessPool and friends: pool is not usable
            return False
    return False


def _resolve_future(
    future: object,
    name: str,
    a: ExportSource,
    b_source: ExportSource,
    a_names: set[str],
    b_names: set[str],
    options: CompareOptions,
) -> TableDiff | None:
    """Take a worker's result, or re-run the table in-process if the worker failed.

    This machine's hardware is flaky (transient worker glitches, and a worker can
    die outright — a broken pool then fails every pending future). Rather than
    lose the whole compare, re-run just that table in the main process. The retry
    uses the exact same code, so a *deterministic* bug fails again here and
    propagates; only a transient is absorbed, and the output stays identical.
    """
    try:
        return future.result()  # type: ignore[attr-defined]
    except Exception as exc:
        _log.warning("worker failed for table %s (%s: %s); re-running in-process",
                     name, type(exc).__name__, exc)
        return _diff_dispatch(name, a, b_source, a_names, b_names, options)


def _compare_parallel(
    scoped: list[str],
    a: ExportSource,
    b_source: ExportSource,
    a_names: set[str],
    b_names: set[str],
    options: CompareOptions,
    workers: int,
    total: int,
    progress: Callable[[int, int, str], None] | None,
    cancelled: Callable[[], bool] | None,
) -> dict[str, TableDiff]:
    a_origin = os.fspath(a.origin)  # type: ignore[arg-type]  # guarded by _resolve_workers
    b_origin = os.fspath(b_source.origin)  # type: ignore[arg-type]
    cache_path = options.cache.path if options.cache is not None else None
    options_no_cache = replace(options, cache=None)  # the live cache is not picklable

    results: dict[str, TableDiff] = {}
    done_count = 0
    _patch_worker_console_flags()  # ADR-020: pre-empt the console-attach wedge
    executor: ProcessPoolExecutor | None = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(a_origin, b_origin, cache_path, options_no_cache),
    )
    try:
        if not _pool_is_live(executor, cancelled, _CANARY_GRACE):
            # Pool never came up (wedged spawn handshake or broken): remember it
            # for this process and degrade to the serial path instead of hanging.
            global _pool_unavailable
            _pool_unavailable = True
            _log.warning(
                "worker pool failed the %.0fs liveness canary; falling back to "
                "in-process serial for this and all later compares in this process",
                _CANARY_GRACE,
            )
            executor.shutdown(wait=False, cancel_futures=True)
            executor = None
            return _compare_serial(
                scoped, a, b_source, a_names, b_names, options, total, progress, cancelled
            )
        _log.info("pool live: %d workers over %d tables", workers, total)

        futures = {executor.submit(_worker_one, name): name for name in scoped}
        pending: set = set(futures)
        # wait() with a short timeout instead of as_completed: cancellation stays
        # responsive even when no future completes (e.g. one huge table).
        while pending:
            finished, pending = futures_wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            if cancelled is not None and cancelled():
                raise CompareCancelled(f"cancelled after {done_count}/{total} tables")
            for future in finished:
                name = futures[future]
                diff = _resolve_future(future, name, a, b_source, a_names, b_names, options)
                done_count += 1
                if diff is not None:
                    results[name] = diff
                if progress is not None:
                    progress(done_count, total, name)
        # All work drained and workers idle: join them so none are leaked (stray
        # worker processes were observed lingering after wait=False here).
        executor.shutdown(wait=True)
        executor = None
    finally:
        if executor is not None:
            # Error/cancel unwind: never block on in-flight workers; drop queued.
            executor.shutdown(wait=False, cancel_futures=True)

    # Reassemble in scoped order so the JSON is byte-identical to the serial path,
    # regardless of the order workers happened to finish in.
    return {name: results[name] for name in scoped if name in results}


def _reopenable(source: ExportSource) -> bool:
    return getattr(source, "origin", None) is not None


def _cache_shareable(cache: SchemaCache | None) -> bool:
    """True if the cache can be re-opened in a worker (file-backed, not in-memory)."""
    return cache is None or getattr(cache, "path", ":memory:") != ":memory:"


def _resolve_workers(
    options: CompareOptions, scoped_len: int, a: ExportSource, b_source: ExportSource
) -> int:
    """Decide the worker count, falling back to serial (1) when parallel can't apply.

    Parallel needs re-openable (path-backed) sources and a shareable cache, since
    workers rebuild both from paths. ``workers<=0`` is auto; it only engages past
    ``_MIN_PARALLEL_TABLES``. An explicit ``workers>1`` is honored down to 2 tables.
    """
    if options.workers == 1 or scoped_len < 2:
        return 1
    if _pool_unavailable:  # a canary already timed out in this process
        return 1
    if not (_reopenable(a) and _reopenable(b_source) and _cache_shareable(options.cache)):
        return 1
    cpu = os.cpu_count() or 1
    if options.workers <= 0:  # auto
        if cpu <= 1 or scoped_len < _MIN_PARALLEL_TABLES:
            return 1
        return min(cpu, scoped_len)
    return min(options.workers, scoped_len)  # explicit override


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
    is polled and, if it returns True, raises CompareCancelled. With more than one
    worker the diff runs table-parallel across processes (see ``_resolve_workers``);
    the result JSON is byte-identical to the serial path either way.
    """
    b_source = b if b is not None else a
    a_names = set(a.table_names())
    b_names = set(b_source.table_names())
    scoped = _scope_tables(options, a_names | b_names)
    total = len(scoped)

    workers = _resolve_workers(options, total, a, b_source)
    if workers > 1:
        tables = _compare_parallel(
            scoped, a, b_source, a_names, b_names, options, workers, total, progress, cancelled
        )
    else:
        tables = _compare_serial(
            scoped, a, b_source, a_names, b_names, options, total, progress, cancelled
        )

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
