"""Classify each table by where its rows live (spec §2.3).

Ports ``reference/classify_export.py`` and keeps its per-row stop-at-CONO
optimization (via ``iter_cono_values``). Improves on it by flagging tables whose
company column is ambiguous — more than one 6-char ``…cono`` column (spec §2.1).

Classes:

- ``NO_CONO``  — no company column at all (tenant-wide by schema)
- ``GLOBAL``   — all rows at CONO 0/blank (a company copy will miss these)
- ``COMPANY``  — all rows at CONO > 0 (moves with a company copy)
- ``MIXED``    — rows at both CONO 0 and CONO > 0
- ``EMPTY``    — no data rows
- ``PARSE_ERROR`` — the table could not be decoded (recorded, not fatal; F6)
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from .cono import GLOBAL_CONO, normalize_cono
from .format import read_header
from .format.reader import iter_cono_values
from .format.types import ExportFormatError
from .source import ExportSource


@dataclass(frozen=True, slots=True)
class TableClassification:
    table: str
    cls: str
    fields: int
    cono_field: str | None
    cono_ambiguous: bool
    rows: int
    rows_global: int  # rows at CONO 0/blank (the "a copy will miss these" count)
    conos: tuple[str, ...]  # distinct non-zero CONOs, numerically sorted
    error: str | None = None


def _cono_sort_key(value: str) -> tuple[int, object]:
    # Numeric CONOs sort numerically; anything odd sorts after, lexically.
    return (0, int(value)) if value.isdigit() else (1, value)


def classify_stream(stream: BinaryIO, table: str) -> TableClassification:
    """Classify one table export read from ``stream``."""
    header = read_header(stream)
    cono_indexes = header.cono_field_indexes()
    cono_index = cono_indexes[0] if cono_indexes else None
    cono_ambiguous = len(cono_indexes) > 1

    counts: Counter[str] = Counter()
    rows = 0
    for raw in iter_cono_values(stream, header, cono_index):
        rows += 1
        if cono_index is not None:
            counts[normalize_cono(raw)] += 1

    global_rows = counts.get(GLOBAL_CONO, 0)
    company_rows = rows - global_rows
    if rows == 0:
        cls = "EMPTY"
    elif cono_index is None:
        cls = "NO_CONO"
    elif global_rows and company_rows:
        cls = "MIXED"
    elif global_rows:
        cls = "GLOBAL"
    else:
        cls = "COMPANY"

    conos = tuple(sorted((c for c in counts if c != GLOBAL_CONO), key=_cono_sort_key))
    return TableClassification(
        table=table,
        cls=cls,
        fields=header.nfields,
        cono_field=(header.names[cono_index] if cono_index is not None else None),
        cono_ambiguous=cono_ambiguous,
        rows=rows,
        rows_global=global_rows,
        conos=conos,
    )


def classify_export(
    source: ExportSource, *, progress: Callable[[int, int, str], None] | None = None
) -> list[TableClassification]:
    """Classify every table in an export; a bad table is recorded, not fatal."""
    names = source.table_names()
    total = len(names)
    results: list[TableClassification] = []
    for index, name in enumerate(names, start=1):
        try:
            with source.open_table(name) as stream:
                results.append(classify_stream(stream, name))
        except ExportFormatError as exc:
            results.append(
                TableClassification(
                    table=name,
                    cls="PARSE_ERROR",
                    fields=0,
                    cono_field=None,
                    cono_ambiguous=False,
                    rows=0,
                    rows_global=0,
                    conos=(),
                    error=str(exc),
                )
            )
        if progress is not None:
            progress(index, total, name)
    return results


def observed_conos(classifications: list[TableClassification]) -> list[str]:
    """Distinct non-zero CONOs actually seen across the export (ADR-008).

    This is the authoritative "which companies have data here" set for the mode
    picker — labels (from CMNCMP) decorate it later; they don't define it.
    """
    seen: set[str] = set()
    for entry in classifications:
        seen.update(entry.conos)
    return sorted(seen, key=_cono_sort_key)
