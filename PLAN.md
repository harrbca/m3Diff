# PLAN.md — m3diff implementation plan

Working implementation plan. Read alongside:

- `SPEC-m3diff.md` — the authoritative functional specification.
- `CLAUDE.md` — conventions and ground rules.
- `DECISIONS.md` — the ADR log; decisions summarized here point back to it.

This is a living document. Decisions land in `DECISIONS.md` the moment they're
made; this file is refined as the shape firms up. Nothing here is code —
it's the shape to confirm before building.

---

## 1. Format invariants confirmed from the prototypes

Behaviors treated as load-bearing and pinned with tests:

- **Absent-from-bitmap CONO ⇒ CONO 0 ⇒ tenant-global row.** Distinct from an
  empty-string value that is present with length 0. These three states stay
  separate through the whole pipeline.
- **Row-length is the format's checksum.** `parse_export.py`'s
  `assert pos == rowend` becomes a typed `DecodeError`, enforced per row but
  caught at *table* granularity so one bad table records an error and the run
  continues (F6).
- **Stop-at-CONO optimization** (from `classify_export.py`) is preserved.
  Improvement over the prototype: detect *all* columns matching the 6-char
  `??cono` heuristic and flag `cono_ambiguous` when more than one matches
  (spec §2.1), rather than silently taking the first.
- **TABLE_INFO counts are snapshot/all-company totals** — used as a manifest
  (names, non-empty, sizes), never for per-company validation. The marker-scan
  approach from `parse_tableinfo.py` ports cleanly but needs hardening (drop
  the hardcoded path; tolerate missing markers).
- **Null vs empty-string are distinct on the wire** and are only collapsed at
  compare time, under the `null_equals_empty` toggle (default on).

Scaffold-time hygiene: the ignore file is currently named `gitignore` (no dot —
inert; rename to `.gitignore`), and the directory is not yet a git repo.

---

## 2. Directory structure (proposed)

```
m3diff/
├── engine/                        # Python package — installable, CLI, RPC
│   ├── pyproject.toml             # console_scripts: m3diff ; ruff/pytest config
│   ├── src/m3diff/
│   │   ├── format/
│   │   │   ├── reader.py          # streaming table decoder (ports parse_export)
│   │   │   ├── tableinfo.py       # TABLE_INFO deserializer (ports parse_tableinfo)
│   │   │   └── types.py           # Field, decoded-row types
│   │   ├── source.py              # ExportSource: zip|dir, lazy per-table streams,
│   │   │                          #   CONO filtering, table enumeration
│   │   ├── classify.py            # NO_CONO/GLOBAL/COMPANY/MIXED/EMPTY + CONO enum
│   │   ├── pk.py                  # PK resolution + CONO masking (the danger zone)
│   │   ├── schema/
│   │   │   ├── cache.py           # SQLite: {table→columns,types,pk,fetched_at}
│   │   │   ├── publisher.py       # Metadata Publisher REST client
│   │   │   └── ionapi.py          # .ionapi parse (secret; path-only, never logged)
│   │   ├── diff.py                # pure diff engine → DiffResult dataclasses
│   │   ├── contract.py            # §5 JSON contract dataclasses + deterministic serializer
│   │   ├── report.py              # CSV + Markdown exporters
│   │   ├── cli.py                 # argparse: compare | classify | schema refresh
│   │   └── rpc.py                 # NDJSON-over-stdio server for the GUI
│   └── tests/
│       ├── fixtures/builder.py    # programmatic writer (inverse of reader) + zip/TABLE_INFO
│       ├── golden/                # expected diff JSON per §6.3 case
│       └── test_*.py
├── desktop/                       # Tauri shell + React UI
│   ├── package.json  vite.config.ts  index.html
│   ├── src/  (api/ components/ views/ types/)   # views: Upload Scope Results Drilldown Settings
│   └── src-tauri/  (tauri.conf.json  Cargo.toml  src/main.rs)   # spawns bundled engine sidecar
├── reference/                     # kept as-is (authoritative), never shelled out to
├── docs/format.md                 # §2 written up as prose
├── .gitignore                     # renamed from gitignore
├── LICENSE                        # MIT placeholder
├── DECISIONS.md
├── PLAN.md
├── SPEC-m3diff.md
└── README.md
```

Two top-level halves — `engine/` (Python) and `desktop/` (Tauri+React) —
because the engine must build, test, and ship independently of the shell.
(Directory naming is still open; see §8.)

---

## 3. Stack and shell decisions

Full rationale in `DECISIONS.md`. Summary:

- **Shell: Tauri** (ADR-001). Python engine bundled as a PyInstaller sidecar
  (`externalBin`); shell ↔ engine over **NDJSON-over-stdio**, not HTTP. Keeps
  the backend stdlib-only, opens no port, ties process lifecycle to the
  sidecar. Progress (F5) is a stream of `progress` frames; cancel flips a flag
  the diff loop checks between tables.
- **UI:** React + TypeScript + Tailwind, Vite build.
- **Backend:** Python 3.11+, stdlib-first, SQLite via stdlib.
- **Packaging:** Windows NSIS installer is the priority; installer embeds the
  WebView2 bootstrapper. macOS/Linux best-effort.

---

## 4. Diff engine as library + CLI

**Pure core.** `diff.compare(a, b, opts) -> DiffResult` reads from
`ExportSource`s and returns dataclasses — no printing, no file writes.
Serialization (`contract.to_json`), CSV, and Markdown are separate. The CLI,
the RPC server, and the tests all call this one function; that is how CLI and
GUI stay identical (F20).

**Byte-identical JSON — with one honest caveat.** The serializer uses
`sort_keys=True`, UTF-8, `\n`, `ensure_ascii=False`, and sorts every
`added`/`removed`/`modified` list by masked-PK tuple. Everything is then
deterministic **except** `generated_at` and the displayed input filenames —
those are *inputs* supplied by the caller. Both front-ends pass the same
values; golden tests inject a frozen clock and use basenames. Guarantee:
"byte-identical given identical inputs, where the timestamp is one of the
inputs."

**Streaming + memory (spec §6.2).** The unit of memory is a *table pair*, never
a whole export:

- **Index one side, stream the other.** Build `dict[masked_pk → row]` for one
  side; stream the other row-by-row — hit ⇒ field-diff immediately and mark
  seen; miss ⇒ added; leftover unseen index entries ⇒ removed. Peak memory ≈
  one side of one table.
- Default to indexing side A; index the smaller side when a cheap count exists
  from the classify pass.
- Escape hatch for pathological single tables (millions of rows): above a row
  threshold, index `masked_pk → row_hash` only — keeps added/removed exact,
  drops field-level "modified" detail, and records that downgrade in the
  table's result so it is never silently lossy.

**CONO masking — the danger zone.** Centralized in `pk.py`. The CONO value is
normalized identically to the classifier (absent⇒`0`, blank⇒`0`) and then
*dropped from the key tuple*, so `(500, ITEM001)` and `(100, ITEM001)` collide.
CONO also stays in the default ignore-fields list so it never surfaces as a
"modified" field. In intra mode, `ExportSource` filters one table stream to
CONO-A for side A and CONO-B for side B out of the same file.

**Mask as data, not a CONO special-case.** The mask is a list of
key-normalizing rules — each either "drop from key" (CONO) or "remap value in
key". v1 wires only `["CONO"]`; v1.1 DIVI drops in without a rewrite (§7.3).

---

## 5. Answers to spec §7 open questions

1. **Tauri vs Electron → Tauri**, no spike. See ADR-001.
2. **Metadata Publisher PK fetch → bulk, on explicit refresh, cached; never
   lazy during a compare.** Endpoint shape **confirmed** (see
   `METADATA-PUBLISHER-NOTES.md`, ADR-002/004/007). Refresh = `getTables` →
   `getColumnsUsedByTable` per table; PK = columns whose `indexes` contains
   `00`, in response order. Cache keyed on (component, table_name),
   MVX-preferred. Auth done in-house — no InforSDK dependency.
3. **DIVI masking (v1.1) → remap, not drop.** Owner *corrected* (2026-07-04):
   divisions **are** renamed when a new company is stood up, so DIVI needs the
   **remap** rule (a user-supplied division mapping), not a plain drop. See
   ADR-010 (supersedes ADR-003). v1 is unaffected (masks CONO only); the remap
   rule + a small mapping UI land in v1.1. The mask-as-data model now has its
   confirmed consumer.
4. **MIXED tables in global mode → include only their CONO-0 subset**, next to
   all NO_CONO tables. Each such table's result carries `global_subset: true`
   and row counts reflecting only the CONO-0 rows, so it is never misread as a
   full-table compare.

---

## 5a. Cross-cutting resolutions (Sherlock S1–S5, B2)

- **Contract governance (S1 → ADR-005).** `contract.py` is the single source of
  truth; TS types generated from it; a drift test enforces parity. Additive
  fields are free; breaking changes bump `tool_version` and get an ADR. All five
  proposed additions land (`pk_source`, `schema_match`, `cono_ambiguous`,
  `global_subset`, hash-downgrade flag) plus ADR-004's `schema_component` /
  `component_ambiguous`.
- **"Self-describing enough" (S2).** Target: a downstream LLM can explain and
  prioritize a diff from the JSON *alone*, no source exports. Concretely, each
  table result carries MDP `description` + `tableCategory` next to the class,
  PK, and human-meaningful field names. **No** analysis/severity/recommendation
  fields — that's the post-MVP feature's job. Just let the descriptive metadata
  ride along (cheap; we fetch it anyway).
- **Config preset (S3 → ADR-006).** Category `MF`; no curated allow-list; globs
  as manual fallback.
- **Monorepo isolation (S5).** Layout confirmed. Add a CI check that builds and
  tests `engine/` in isolation; `engine/` must never import up into `desktop/`
  — the sidecar is built from the engine; the engine never knows the shell
  exists.
- **Company enumeration (B2 → ADR-008).** Selectable CONOs = the union observed
  during the classify pass; `CMNCMP` (`JICONO → JITX40`) labels them when
  present; unlabeled if absent (robust to scoped exports).

---

## 6. Testing backbone

The keystone is `tests/fixtures/builder.py` — a programmatic **writer** that is
the exact inverse of the reader (header + length-prefixed bitmap rows, plus zip
and synthetic TABLE_INFO). It gives the round-trip property test
(`reader(writer(x)) == x`; row-length invariant holds) and lets every golden
test hand-build A/B exports with **no real data**.

Golden cases (spec §6.3): identical; added/removed/modified; CONO masking;
null-vs-empty; absent-from-bitmap CONO; schema mismatch (compare on column
intersection); truncated file (per-table error, run continues); multi-CONO
export; NO_CONO table. A schema-validation test keeps the Python contract and
the generated TS types from drifting.

---

## 7. Build order (reviewable chunks)

1. **Scaffold** — git init, `.gitignore` rename, `engine/` package,
   `pyproject`, LICENSE, README.
2. **Format layer** — reader + tableinfo + fixture builder + round-trip test.
3. **Classifier + ExportSource** — ported/improved (multi-CONO flag), CONO
   enumeration, zip/dir streaming, CONO filtering.
4. **PK + schema cache** — masking, SQLite cache (heuristic PKs first).
5. **Diff engine + contract** — index-one-stream-other, deterministic
   serializer, **all golden tests**.
6. **CLI** — `compare` / `classify` / `schema refresh`.
7. **Metadata Publisher + `.ionapi`** — in-house ION OAuth (no InforSDK);
   `getTables` → `getColumnsUsedByTable` refresh under a bounded-concurrency
   pool; PK from index-`00` membership; cache keyed (component, table_name).
8. **Reporters** — CSV + Markdown.
9. **RPC stdio server** — progress + cancel.
10. **Tauri shell + PyInstaller sidecar** — Windows installer.
11. **React UI** — Upload → Scope → Results → Drilldown → Settings.
12. **Results history cache + packaging + docs.**

Steps 1–6 deliver a fully testable, scriptable engine before any desktop code
exists — matching "the GUI is a thin shell."

---

## 8. Open items

All resolved: DIVI (now renamed) → ADR-010 (supersedes ADR-003). MDP endpoint →
ADR-002/004. Contract governance → ADR-005. Config preset → ADR-006. Auth →
ADR-007. Company enumeration → ADR-008. `.ionapi` at-rest → ADR-009 (option a).
Directory layout → ADR-011 (`engine/` + `desktop/`).

**Test-data policy (owner).** No scrubbed sample is available — the owner can
supply only *real* exports. Consequence: the committed test suite is **100%
programmatic fixtures**, including a synthetic **268-column** table to exercise
the wide-bitmap path — no real data is ever committed. Real exports, if
provided, are used **only** for local smoke-testing under the git-ignored
`fixtures/real/`, and never leak values, identifiers, or employer specifics into
committed code, tests, docs, or chat (policy per CLAUDE.md).

**No open blockers remain for chunks 1–6.**

### Reference projects (read-only, pattern only)

Two developer-local projects (not part of this repo) were used as pattern
references:

- one for MDP metadata dump + M3 API calls;
- one for `.ionapi` / Infor SDK usage.

No employer-specific values (hostnames, tenant IDs, credentials, real data) are
copied from these into m3diff — they inform *patterns* only, per CLAUDE.md.

---

## 9. Decision ownership (Sherlock S4)

Rule of thumb: **if a wrong call only costs a refactor, I decide it and log it;
if a wrong call would produce misleading diff output a user would trust, I flag
it for ratification first.**

- **Mine to decide + log (no pre-approval):** shell, transport, cache structure,
  build order, directory layout, library internals, packaging, auth mechanism.
  (ADR-001, ADR-002, ADR-007.)
- **Ratify before building on:** the result-JSON contract shape, CONO/DIVI
  masking semantics, classification rules, and the config-scope definition.
  (ADR-003, ADR-004, ADR-005, ADR-006, ADR-008 — all owner/Sherlock-relayed.)
