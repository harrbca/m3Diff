"""Render a DiffResult to CSV and Markdown (spec F14/F15).

JSON is the canonical contract (contract.to_json); these are convenience
renderings for tickets and spreadsheets. All are pure functions of a DiffResult.
"""
from __future__ import annotations

import csv
import io

from .contract import DiffResult, TableDiff


def _pk_str(pk: list[str | None]) -> str:
    return "|".join("" if v is None else v for v in pk)


def _v(value: str | None) -> str:
    return "" if value is None else value


# --- CSV --------------------------------------------------------------------
_SUMMARY_COLUMNS = (
    "table", "class", "status", "pk_source", "schema_component", "schema_match",
    "rows_a", "rows_b", "added", "removed", "modified", "error",
)


def to_summary_csv(result: DiffResult) -> str:
    """One row per table: the at-a-glance dashboard (spec F14 summary CSV)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_SUMMARY_COLUMNS)
    for name, td in sorted(result.tables.items()):
        writer.writerow(
            [
                name,
                td.table_class,
                td.status,
                td.pk_source,
                td.schema_component or "",
                "yes" if td.schema_match else "no",
                td.rows_a,
                td.rows_b,
                td.counts.added,
                td.counts.removed,
                td.counts.modified,
                td.error or "",
            ]
        )
    return buffer.getvalue()


_DETAIL_COLUMNS = ("change", "pk", "field", "a", "b")


def to_table_csv(table: TableDiff) -> str:
    """A per-table detail CSV in long form: one row per changed field (spec F14)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_DETAIL_COLUMNS)
    for ref in table.removed:
        for name, value in sorted(ref.row.items()):
            writer.writerow(["removed", _pk_str(ref.pk), name, value, ""])
    for ref in table.added:
        for name, value in sorted(ref.row.items()):
            writer.writerow(["added", _pk_str(ref.pk), name, "", value])
    for mod in table.modified:
        for name, change in sorted(mod.changes.items()):
            writer.writerow(["modified", _pk_str(mod.pk), name, _v(change.a), _v(change.b)])
    return buffer.getvalue()


# --- Markdown ---------------------------------------------------------------
def to_markdown(result: DiffResult) -> str:
    """A summary + per-table findings report, suitable for a ticket (spec F15)."""
    out: list[str] = []
    out.append("# m3diff report")
    out.append("")
    out.append(f"- **Mode:** {result.mode}")
    out.append(f"- **Generated:** {result.generated_at}")
    out.append(f"- **A:** `{result.a.file}` (CONO {result.a.cono})")
    out.append(f"- **B:** `{result.b.file}` (CONO {result.b.cono})")
    out.append("")

    s = result.summary
    out.append("## Summary")
    out.append("")
    out.append("| Metric | Count |")
    out.append("| --- | --- |")
    out.append(f"| Tables compared | {s.tables_compared} |")
    out.append(f"| Identical | {s.identical} |")
    out.append(f"| Modified | {s.modified} |")
    out.append(f"| Missing in A | {s.missing_in_a} |")
    out.append(f"| Missing in B | {s.missing_in_b} |")
    out.append(f"| Errors | {s.errors} |")
    out.append("")

    out.append("## Tables")
    out.append("")
    out.append("| Table | Class | Status | Added/Removed/Modified | PK source |")
    out.append("| --- | --- | --- | --- | --- |")
    for name, td in sorted(result.tables.items()):
        counts = f"{td.counts.added} / {td.counts.removed} / {td.counts.modified}"
        out.append(f"| {name} | {td.table_class} | {td.status} | {counts} | {td.pk_source} |")
    out.append("")

    changed = [(n, td) for n, td in sorted(result.tables.items()) if td.status != "identical"]
    if changed:
        out.append("## Details")
        out.append("")
        for name, td in changed:
            out.append(f"### {name} — {td.status}")
            out.append("")
            if td.error:
                out.append(f"> error: {td.error}")
                out.append("")
                continue
            _markdown_change_block(out, "Removed", td.counts.removed, td.truncated,
                                    [f"`{_pk_str(r.pk)}`" for r in td.removed])
            _markdown_change_block(out, "Added", td.counts.added, td.truncated,
                                   [f"`{_pk_str(r.pk)}`" for r in td.added])
            if td.modified:
                note = " (field detail unavailable — large table)" if not td.modified_detail else ""
                out.append(f"**Modified: {td.counts.modified}**{note}")
                for mod in td.modified:
                    fields = ", ".join(
                        f"{name}: {_v(ch.a)!r} → {_v(ch.b)!r}" for name, ch in sorted(mod.changes.items())
                    )
                    out.append(f"- `{_pk_str(mod.pk)}`" + (f" — {fields}" if fields else ""))
                out.append("")

    return "\n".join(out) + "\n"


def _markdown_change_block(out: list[str], label: str, total: int, truncated: bool, items: list[str]) -> None:
    if not items:
        return
    note = f" (showing {len(items)} of {total})" if truncated and len(items) < total else ""
    out.append(f"**{label}: {total}**{note}")
    for item in items:
        out.append(f"- {item}")
    out.append("")
