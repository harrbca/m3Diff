# Metadata Publisher — confirmed API shapes (resolves ADR-002 open item)

Probed against a live M3 16.x instance (MVX component) via the Metadata
Publisher REST API. This closes the "verify endpoint shape before writing
`publisher.py`" caveat in ADR-002. Sample payloads below are real responses,
trimmed. No employer/tenant identifiers included.

---

## 1. Three endpoints, and which ones we actually need

### 1a. List tables (bulk discovery)
Filterable by table-name prefix (empty = all). One row per table **per
component**. Fields:

```
tableName          e.g. "CSYTAB"
tableDescription   e.g. "MF: System tables file"
tableComponent     e.g. "MVX"   ← part of identity, see §3
tableCategory      "MF" | "TF" | "WF"  ← useful, see §4
tableMaintainedBy  maintaining program, e.g. "CMS240" (may be "")
tablePgmHeading    e.g. "XCS1000" (may be "")
```

### 1b. Get columns for a table (**the workhorse call**)
One row per column, in key/definition order. Fields:

```
columnName   e.g. "SVCONO"
description  e.g. "Company"
dataType     "String" | "Decimal"
length       e.g. "3"      (string in the payload)
decimals     e.g. ""       (string; blank when N/A)
editCode     e.g. "4","Z"  (formatting hint; blank when N/A)
indexes      e.g. "00,10,20,30"  ← comma-separated index membership
```

### 1c. Get index key columns (**fallback only**)
Keyed by index name = table + 2-digit number (`MITMAS00`, `CSYTAB00`; `00` =
PK). Returns ordered key columns with sortOrder. We mostly don't need this —
see §2.

---

## 2. Primary key is derivable from the columns call — no separate index fetch

Every column in the 1b response carries its index membership inline
(`indexes` field). To get a table's PK:

> Take the columns whose `indexes` string contains `00`, **in response order**.
> That order *is* the primary-key column order.

Verified: COSRVI columns report `SVCONO (indexes "00,10,20,30")` then
`SVSIID (indexes "00,...")` — the dedicated `COSRVI00` index endpoint returns
exactly `[SVCONO, SVSIID]` in that order. Cross-checked on MITMAS00
(`MMCONO, MMITNO`) and CSYTAB00 (`CTCONO, CTDIVI, CTSTCO, CTSTKY, CTLNCD`).

**Consequence for the schema refresh (ADR-002 update):**
- Bulk refresh = *list tables* (1a) → *columns per table* (1b). That single
  1b call yields columns, types, lengths, decimals, edit codes, **and the PK**.
- The index endpoint (1c) is retained in `publisher.py` as a **fallback only**,
  for the rare table where no column reports index `00`. Keep it off the hot
  path.
- This roughly halves the per-table call count vs. the original assumption
  (no separate bulk-index pass to design).

**Guard to implement:** if zero columns report `00` for a table, fall back to
1c for that table; if 1c is also empty, mark `pk_source: "heuristic"` and use
the heuristic PK path (already specced).

---

## 3. Table names are NOT unique — identity is (component, tableName)

The list endpoint returns the same table name under multiple components:

- `CSYTAB` → MVX **and** MJP
- `CSYPER` → MVX **and** MCZ
- `CSYCAL` → MVX **and** MIN
- `CSYECT` → MVX, MPL, MIT, MFI (four rows)

For standard M3 business/config tables, **MVX is the component we want** almost
always. But a cache keyed on table name alone will collide and silently
overwrite.

**Design requirements:**
- Schema cache primary key = **(component, table_name)**, not table_name.
- Refresh stores all components; **lookup prefers MVX**.
- Export files identify tables by **name only** (the binary header has no
  component — e.g. `COSRVI`, `OCUSMA`). So at diff time, resolve an export's
  table name to the **MVX** schema row by default.
- When a name maps to multiple components, add a one-line note in that table's
  diff result (e.g. `schema_component: "MVX"`, `component_ambiguous: true`) so
  a user is never unknowingly diffing against the wrong component's schema.

---

## 4. tableCategory gives a clean "config vs transaction" signal

The list endpoint's `tableCategory` classifies every table:

- **MF** = master / configuration file (the drift-prone config + master data)
- **TF** = transaction file
- **WF** = work / scratch file

This is a better basis for the F4 "Configuration tables" scope preset than
prefix globbing (`CSY*,C*`). Suggested UI: offer **"All MF (master/config)
tables"** as the config-scope default, with TF/WF opt-in. Prefix globs remain
available for custom scoping. (Flag to owner as a filter-design choice, not a
hard requirement.)

Note this is orthogonal to our export-derived NO_CONO/GLOBAL/COMPANY/MIXED
class: `tableCategory` comes from metadata (what the table *is*), our class
comes from the export data (where its rows *live*). Both are useful; show both.

---

## 5. Net effect on ADR-002

Update the ADR to "Accepted (endpoint shape confirmed)":

- Bulk refresh path = list-tables + columns-per-table; PK from index-00
  membership in the columns payload.
- Index endpoint = fallback only.
- Cache keyed on (component, table_name), MVX-preferred at lookup.
- No blocking unknowns remain for `publisher.py`.

Field types to persist in the cache from 1b: `columnName, dataType, length,
decimals, editCode, indexes` plus derived `is_pk` and `pk_order`. From 1a:
`component, category, description` for display/scoping.
