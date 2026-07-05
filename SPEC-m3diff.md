# Specification: m3diff — M3 Table Export Comparison Tool

**Version:** 1.0
**Date:** 2026-07-04
**Target:** Claude Code (Opus)
**Status:** Ready for implementation
**License intent:** Personal project, potentially open source. No employer-specific references anywhere in code, comments, paths, or docs.

---

## 1. Overview

A desktop application for Infor M3 CloudSuite administrators to compare table
exports between tenants and companies. M3 tenants contain multiple companies
(CONO); data is segregated by company, but some tables are tenant-wide (rows
stored at CONO 0/blank, or no CONO column at all). Company copies and
tenant-to-tenant migrations silently miss the tenant-wide data, and companies
drift from their master over time. This tool makes those gaps visible.

**Comparison modes:**

1. **Intra-tenant:** one export zip, compare company A vs company B (config drift detection)
2. **Inter-tenant:** two export zips, compare company X in tenant 1 vs company Y in tenant 2 (migration validation)
3. **Global:** two export zips, compare only tenant-wide data (CONO 0 rows and CONO-less tables)

**Form factor:** Desktop app (Electron or Tauri — see §6.1), React UI, Python
backend subprocess. All processing is local. No data leaves the machine except
an optional, user-initiated schema fetch from the M3 Metadata Publisher API.

**Post-MVP (out of scope for v1, but design for it):** an "Analyze with AI"
feature that sends the diff JSON to an LLM for interpretation. v1 requirement
is only that the diff JSON be complete and self-describing enough to support
this later, and that the diff engine be callable from a CLI so an LLM agent
can drive it headlessly.

---

## 2. Input file formats (reverse-engineered, verified against real exports)

The Infor grid data-management tool exports a set of files: one binary file
per table plus a `TABLE_INFO` catalog (typically zipped together).

### 2.1 Per-table export files (custom binary format)

```
[4B big-endian uint32: header length]
[header: UTF-8 string; one column descriptor per field, separated by 0x01]
    descriptor = "type;name;maxlen;flag"
    type  = JDBC SQL type code (4 = INTEGER, 12 = VARCHAR)
    name  = lowercase prefixed field name (e.g. "okcono", "svsiid")
[rows, repeated until EOF]:
    [4B big-endian uint32: row payload length (bitmap + values)]
    [null bitmap: ceil(nfields/8) bytes, MSB-first, one bit per column
     in header order; bit set = value present in payload]
    [for each set bit, in column order:
        4B big-endian uint32 value length, then UTF-8 bytes;
        a length of 0 in a STRING column is a carry-forward marker meaning
        "same as this column's last present value" — see the compression rules]
```

Verified behaviors that MUST be preserved:

- **All values are strings**, including INTEGER-typed columns. Timestamps are
  epoch-millis-as-ASCII; dates are `YYYYMMDD` strings.
- **A column absent from the bitmap is null/default.** For the company column
  specifically: absent-from-bitmap ⇒ CONO 0 ⇒ tenant-global row. This is how
  global config tables (e.g. COSRVI, output service definitions) actually
  store their rows. A present value of length 0 in a string column is **not** an
  empty string — it is a carry-forward marker (below); a genuine blank string is
  bitmap-absent, so after decompression a decoded row never contains `""`.
- **Row-boundary invariant:** bytes consumed per row must exactly equal the
  declared row length. Assert this; it is the format's built-in checksum.
- **Company column identification:** a 6-character field name ending in
  `cono` (case-insensitive), e.g. `mmcono`, `okcono`. Not necessarily the
  first column. Some tables have none (tenant-wide by schema). Flag tables
  where more than one column matches the heuristic.
- Null bitmap width scales with column count (34 cols → 5 bytes,
  268 cols → 34 bytes). Verified against real files up to 268 columns.

**String-column carry-forward compression (verified 2026-07-05).** String
columns are run-length compressed against the previous row. Observed over 15.3M
rows / ~2,600 non-empty tables across two real exports with zero
counterexamples; decompressed values match live-DB rows exactly and restore
primary-key uniqueness in every table (see ADR-026). Six normative rules:

1. A present, zero-length value occurs **only in string columns** (JDBC type
   `12` VARCHAR; type `1` CHAR would also be a string type). It means: **this
   column has the same value as its last present value**, in file order.
2. The carried value is the **last present effective value** for that column in
   the stream (after that value's own decompression).
3. Non-string columns (`4` INTEGER, `2` NUMERIC, `-5` BIGINT) are **never
   compressed**; identical consecutive values repeat verbatim. A zero-length
   value in a non-string column is a format violation.
4. Genuine blank/null strings are **bitmap-absent** (the existing null
   semantic), so a zero-length value is unambiguous and a **decompressed row
   never contains an empty string**.
5. A column's **first present occurrence is never zero-length** (nothing to
   carry); a zero-length value with no prior value is a format violation
   ("orphan carry").
6. Carry state is **per table stream, per column, purely positional**. It does
   not reset at CONO/DIVI boundaries or anywhere else; nothing in the header or
   TABLE_INFO signals the behavior.

Rules 3 and 5 are treated like the row-length invariant — as checksums: the
reader raises a typed `CompressionError` (an `ExportFormatError`, so the
per-table containment of F6 turns it into an `error` result) rather than
silently absorbing it.

### 2.2 TABLE_INFO (standard Java serialization)

`ArrayList` of objects `{long noRecords; String tableName}` (class
`gridaccess.client.tools.proxy.ToolProxy$TableInfo`), magic `AC ED 00 05`.
First element carries the full class descriptor; subsequent elements use
back-references (`73 71 00 7E 00 02` + 8-byte long + `74` + 2-byte length +
name bytes). Use it as the manifest/scoping catalog (which tables exist,
which are non-empty). Do not trust its counts for per-company validation —
they are snapshot-time, all-companies totals.

### 2.3 Existing reference implementations

Three working prototype scripts are provided with this spec and are the
authoritative reference for the format:

| File | Purpose |
|------|---------|
| `parse_export.py` | Generic table decoder (header + rows → dicts). |
| `parse_tableinfo.py` | TABLE_INFO deserializer → table/count CSV. |
| `classify_export.py` | Streams a zip/directory of exports; classifies each table as NO_CONO / GLOBAL / COMPANY / MIXED / EMPTY; writes CSV. Optimization worth keeping: per row, stop decoding at the CONO field. |

Port their logic into the backend package with tests; do not shell out to them.

They remain the authoritative reference for framing, header, bitmap, and
row-length behavior, but they **predate the discovery of string carry-forward
compression** (§2.1, ADR-026) and do not implement it — so on real exports they
under-report string values (returning zero-length markers verbatim). The engine
reader is now the authoritative decoder for value semantics.

---

## 3. Architecture

```
┌──────────────────────────────────────┐
│ Electron/Tauri main process          │  window mgmt, native file dialogs,
│                                      │  backend subprocess lifecycle
├──────────────────────────────────────┤
│ React + TypeScript UI                │  upload, mode select, results,
│                                      │  drill-down, export
└──────────────┬───────────────────────┘
               │ IPC → localhost HTTP or stdio JSON-RPC
               ▼
┌──────────────────────────────────────┐
│ Python backend (bundled subprocess)  │
│  ├─ export reader (format §2)        │
│  ├─ classifier (NO_CONO/GLOBAL/...)  │
│  ├─ indexer (rows keyed by PK)       │
│  ├─ diff engine                      │
│  ├─ schema cache (SQLite)            │
│  └─ CLI entry point (same engine)    │
└──────────────┬───────────────────────┘
        ┌──────┴───────┬────────────────┐
        ▼              ▼                ▼
  M3 Metadata     ~/.m3diff/       ~/.m3diff/work/
  Publisher API   schema.db        (scratch, auto-clean)
  (optional)
```

**Key principle:** the diff engine is a pure Python library with a CLI
(`m3diff compare ...`). The GUI is a thin shell over it. This keeps the tool
scriptable/LLM-drivable and makes the engine testable without Electron.

### 3.1 Backend responsibilities

- Extract uploaded zips to `~/.m3diff/work/{uuid}/` (stream from zip where
  possible; never require pre-unzipping by the user).
- Classify all tables (reuse classifier logic).
- Enumerate distinct CONOs found in an export so the UI can offer real
  choices ("this export contains companies: …").
- For each table in scope: parse rows, index by primary key, diff.
- Return structured JSON (see §5).
- Manage schema cache; optionally refresh from Metadata Publisher.

### 3.2 Primary key resolution (row identity)

Diffing requires a PK per table. Resolution order:

1. **Schema cache** (SQLite), pre-populated from the M3 Metadata Publisher
   REST API (table index 00 = primary key). Ship a "Refresh schema" action
   that fetches and caches `{table → columns, types, PK column list}` for all
   tables. Credentials come from a standard Infor `.ionapi` file the user
   points the app at in Settings; never bundled, never stored in app config.
2. **Fallback heuristic** when a table is missing from the cache: use
   CONO + all columns of the first unique combination available, or if
   unknown, hash the full row as identity and diff on set membership only.
   Mark such tables `pk_source: "heuristic"` in output so results are
   interpretable.

For the comparison itself, the CONO component of the PK must be **masked**:
when comparing company 500 vs company 100, row (500, ITEM001) must match
row (100, ITEM001). Same for DIVI-keyed tables when divisions are remapped —
v1: mask CONO only, note DIVI masking as a config toggle.

### 3.3 Diff semantics

- Set membership by masked PK: rows only in A ("removed" from B's
  perspective), only in B ("added"), in both.
- For rows in both: field-by-field compare, excluding an ignorable-fields
  list. Default ignore list (configurable in Settings): change timestamp
  fields (`*lmdt`, `*rgdt`, `*rgtm`, `*lmts`), change number (`*chno`),
  changed-by (`*chid`), and the CONO field itself. These otherwise generate
  100% noise.
- Row order is irrelevant; comparisons are set-based.
- Empty string vs null: treat as equal by default (config toggle for strict
  mode), since the export format distinguishes them but M3 semantics usually
  don't.
- Schema mismatch between the two exports (different column sets for the same
  table): report as a schema diff, then compare on the intersection of
  columns.

---

## 4. Functional requirements

### 4.1 Upload and scoping
- F1: Drag-drop or native dialog to select one or two export zips. No manual
  unzipping ever.
- F2: After ingest, show per-export summary: table count, non-empty tables,
  companies present, total rows, classification breakdown.
- F3: Mode selection: intra-tenant (one zip, pick two CONOs from those
  detected), inter-tenant (two zips, pick a CONO from each; they may differ),
  global (two zips, CONO-0/NO_CONO tables only).
- F4: Table scope filter: preset "Configuration tables" (CSY*, C* system/config
  prefixes), preset "All tables", and a custom glob/list input
  (e.g. `CSY*,MITMAS,OCUSMA`). Show the resolved table list before running.

### 4.2 Processing
- F5: Progress reporting per table (n of m, current table name) over IPC;
  cancellable.
- F6: Per-table errors (truncated file, undecodable bytes) must not abort the
  run — record the error against that table and continue.
- F7: Results cached on disk so a completed comparison can be reopened from a
  history list without re-processing.

### 4.3 Results UI
- F8: Summary dashboard: tables compared / identical / with diffs / missing
  from A / missing from B / errored, plus total row deltas.
- F9: Sortable, filterable table list with class (NO_CONO/GLOBAL/COMPANY/MIXED),
  row counts per side, added/removed/modified counts, status color
  (green identical, yellow modified, red missing/large gaps).
- F10: Table drill-down: schema info (columns, PK, pk_source), row diff lists
  grouped by added/removed/modified, paginated (tables can have 300k+ rows;
  never render unbounded lists).
- F11: Row drill-down for modified rows: side-by-side field diff, changed
  fields highlighted, prev/next navigation, copy-value buttons.
- F12: Search within results (by table name, by PK value).

### 4.4 Export
- F13: Export full results as JSON (the §5 structure, suitable for scripting
  and future AI analysis).
- F14: Export as CSV (one summary CSV + optional per-table detail CSVs).
- F15: Export as Markdown report (summary + per-table findings), suitable for
  attaching to a ticket or change record.

### 4.5 Schema and settings
- F16: Settings panel: ionapi file path, schema refresh action with progress,
  cache location display, ignore-field list editor, work-directory retention
  (default 7 days), strict null/empty toggle.
- F17: Fully offline operation when schema is cached (or via heuristic PKs);
  clear indication when running on heuristics.

### 4.6 CLI
- F18: `m3diff compare --mode {intra|inter|global} --a export1.zip [--b export2.zip]
  --cono-a 500 [--cono-b 100] [--tables "CSY*,MITMAS"] --out result.json`
- F19: `m3diff classify export.zip --out classification.csv`
- F20: `m3diff schema refresh --ionapi path/to/file.ionapi`
- CLI and GUI must produce byte-identical result JSON for the same inputs.

---

## 5. Diff result JSON (contract)

```json
{
  "tool_version": "1.0.0",
  "mode": "inter",
  "generated_at": "ISO-8601",
  "a": {"file": "tenant1_export.zip", "cono": "500", "tables": 676, "rows": 1900000},
  "b": {"file": "tenant2_export.zip", "cono": "100", "tables": 640, "rows": 1850000},
  "settings": {"ignored_fields": ["*lmdt", "*chno", "*chid", "*rgdt", "*rgtm", "*lmts"],
                "null_equals_empty": true, "pk_mask": ["CONO"]},
  "summary": {"tables_compared": 52, "identical": 48, "modified": 2,
               "missing_in_b": 1, "missing_in_a": 0, "errors": 1},
  "tables": {
    "CSYTAB": {
      "class": "COMPANY",
      "pk": ["cono", "divi", "stco", "stky", "lncd"],
      "pk_source": "metadata",
      "schema_match": true,
      "rows_a": 15000, "rows_b": 14950,
      "status": "modified",
      "added": [{"pk": ["", "SDST", "01"], "row": {"...": "..."}}],
      "removed": [{"pk": ["", "SDST", "02"], "row": {"...": "..."}}],
      "modified": [{"pk": ["", "CUA1", "US"],
                     "changes": {"parm": {"a": "old", "b": "new"}}}],
      "error": null
    }
  }
}
```

Large diff sets: cap embedded rows per table (default 1,000 per change type)
with a `truncated: true` flag and total counts; full detail available via
per-table CSV export.

---

## 6. Implementation notes

### 6.1 Stack decisions (recommendations, implementer may adjust with rationale)

- **Shell:** Tauri preferred (smaller binaries, no bundled Chromium) if the
  Python-subprocess sidecar pattern proves clean; Electron acceptable.
  Either way the backend is a bundled Python (PyInstaller/briefcase) or a
  vendored CPython — the user must not need Python installed.
- **UI:** React + TypeScript + Tailwind. Vite build.
- **Backend:** Python 3.11+, stdlib-first. SQLite via stdlib. FastAPI only if
  HTTP transport is chosen; otherwise plain JSON-over-stdio.
- **Packaging:** Windows installer is the priority target; macOS/Linux
  best-effort.

### 6.2 Performance targets

- 2 MB / ~4k-row / 268-column table: diff in ≤ 2 s.
- Full-tenant zips (~2M rows, ~700 tables) with config-preset scope
  (~100 tables): ≤ 2 min, ≤ 1 GB RAM.
- Stream rows from the zip; index only tables in scope; never materialize an
  entire export in memory at once.

### 6.3 Testing

- Fixture-based unit tests for the format reader: build tiny synthetic
  exports in tests (the format is fully specified in §2 — generate fixtures
  programmatically rather than committing employer data).
- Golden tests: classifier output, diff JSON for hand-built A/B fixtures
  covering: identical tables, added/removed/modified rows, CONO masking,
  null-vs-empty, absent-from-bitmap CONO, schema mismatch, truncated file,
  multi-CONO exports, NO_CONO table. Carry-forward compression (ADR-026):
  (8) a value carried across a CONO boundary that, with CONO masked, makes the
  intra diff identical; (9) a PK column carried down runs that would degenerate
  the metadata PK if read literally but keys cleanly once decompressed;
  (10) same logical data on one uncompressed and one compressed side → identical.
- Property test: round-trip a generated export through the reader; row-length
  invariant must hold. Compressed fixtures (builder `compress=True`) round-trip
  through the decompressing reader to their original logical rows.

### 6.4 Naming and hygiene

- Project name: `m3diff` (working title; final name TBD by owner).
- No employer names, tenant IDs, hostnames, internal paths, or real business
  data in code, tests, fixtures, comments, or docs.
- Config/data root: `~/.m3diff/` (Windows: `%APPDATA%/m3diff/`).
- MIT license placeholder; owner decides before publishing.

---

## 7. Open questions for the implementer to surface (don't guess silently)

1. Tauri sidecar vs Electron subprocess — spike both briefly if unsure.
2. Whether Metadata Publisher PK fetch for ~4,000 tables should be one bulk
   call or lazy per-table on first use (bulk preferred; verify API shape).
3. DIVI masking design for v1.1 (config remaps divisions between companies).
4. How to present MIXED tables in global mode (include their CONO-0 subset).
