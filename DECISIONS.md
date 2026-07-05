# DECISIONS.md — m3diff

A running log of significant architecture and tooling decisions. Each entry is
a lightweight ADR: the call, why, and when we'd revisit it. Appended the moment
a decision is made, so the rationale is visible before it's questioned — not
reconstructed after.

Status values: **Proposed** (recommended, awaiting owner) · **Accepted** ·
**Superseded by ADR-NNN**.

---

## ADR-001 — Desktop shell: Tauri over Electron

- **Date:** 2026-07-04
- **Status:** Accepted (no spike). Revisit if a sidecar spike surfaces real
  friction, or if WebView rendering variance on macOS/Linux stops being
  acceptable.

**Context.** Need a desktop shell wrapping a pure-Python diff engine. Windows
is the priority target (spec §6.4); macOS/Linux are best-effort. The engine
ships as a bundled subprocess regardless of shell.

**Decision.** Tauri. The Python engine is bundled as a **PyInstaller sidecar**
(`externalBin`, resolved per target-triple), and the shell talks to it over
**NDJSON-over-stdio**, not localhost HTTP.

**Rationale.**
- WebView2 is guaranteed on Win11 — the main reason to prefer Electron
  (deterministic bundled Chromium) is moot on our primary platform.
- ~100–150 MB smaller installer (no bundled Chromium) for a utility app.
- The Python sidecar pattern is first-class in Tauri; Electron's easier Node
  integration buys nothing when the backend is Python either way.
- We use zero Chromium-specific behavior (forms, tables, a diff viewer).
- stdio transport keeps the backend **stdlib-only** (no FastAPI/uvicorn), opens
  no port (no CORS, no port collisions, no firewall prompt), and ties the
  process lifecycle to the sidecar.

**Consequences.**
- Accept WebView rendering variance on macOS/Linux (best-effort anyway).
- NSIS installer embeds the WebView2 bootstrapper (`webviewInstallMode:
  embedBootstrapper`) for rare fresh Windows images.
- Chose to skip a comparative spike; confidence is high enough to build on it.

---

## ADR-002 — Metadata Publisher PK fetch: bulk, on explicit refresh, cached

- **Date:** 2026-07-04
- **Status:** Accepted; endpoint shape **confirmed 2026-07-04** (see
  `METADATA-PUBLISHER-NOTES.md` and ADR-004). No blocking unknowns remain for
  `schema/publisher.py`.

**Context.** Diffing needs a primary key per table; the source of truth is the
M3 Metadata Publisher (table index 00 = PK). Spec F17 requires a comparison to
run fully offline once the schema is cached.

**Decision.** Fetch schema in **bulk** via an explicit user-initiated "Refresh
schema" action, cached in SQLite (`{table → columns, types, pk, fetched_at}`).
**Never lazy-per-table during a compare.** Uncached tables fall back to a
heuristic PK, marked `pk_source: "heuristic"` in the output.

**Rationale.**
- Offline-compare requirement: lazy-during-diff would couple every comparison
  to the network and to `.ionapi` credentials, breaking F17.
- Refresh is resumable via per-table `fetched_at`.
- Credentials come from a user-supplied `.ionapi` path only — never stored in
  app config, never logged.

**Consequences (endpoint shape confirmed).**
- Refresh = `GET /les/getTables` once (bulk, ~5,375 rows, prefix-filterable)
  → `GET /les/getColumnsUsedByTable/{table}/{component}?langId=GB` per table.
  The columns response carries each column's `dataType`, `length`, `decimals`,
  `editCode`, and inline `indexes` membership — so the **PK is the columns
  whose `indexes` contains `00`, in response order**. One call per table yields
  columns *and* PK; no separate index pass on the hot path.
- `GET /les/getIndexKeys/{table}00/{component}` is retained as **fallback only**
  (table with no column reporting `00`); heuristic PK if that is empty too.
- **Considered and rejected:** the bulk `/analytics/getTableColumns` (all
  columns for all tables in one ~11 MB call). Its per-column key info is only
  `keyCount` Y/N — no index-order — so it cannot yield PK ordering, and you'd
  still need a per-table PK pass. Sherlock's per-table columns approach is
  same-or-fewer calls with no giant stateful parse.
- Per-table fetch (~5,375 calls) runs under a **bounded-concurrency pool** with
  a pooled HTTP client; refresh stays resumable via per-table `fetched_at`.
- Cache identity and MVX resolution split out to **ADR-004**; auth to
  **ADR-007**. Offline-after-cache stands.

---

## ADR-003 — DIVI masking: drop-only, no value remap

- **Date:** 2026-07-04
- **Status:** **Superseded by ADR-010** (2026-07-04 — owner corrected:
  divisions *are* renamed). Kept below for the record.

**Context.** The PK-mask model supports two rule kinds: "drop from key" (CONO)
and "remap value in key". The open question (spec §7.3) was whether divisions
(DIVI) get *renamed* across companies in real migrations, which would force the
remap path.

**Decision.** Owner confirms divisions are **not** renamed between companies.
DIVI masking is therefore a plain **"drop from key"** entry — identical
treatment to CONO — with no remap table and no remap UI.

**Rationale.** A division code means the same thing in both companies; only its
presence in the key needs neutralizing so cross-company rows collide. Remap
machinery would be dead weight.

**Consequences.** v1.1 ships `pk_mask: ["CONO", "DIVI"]` as two drop entries.
The mask-as-data abstraction still stands (cheap, keeps the door open if a
future case ever needs remap), but no remap feature is built.

---

## ADR-004 — Schema cache identity: (component, table_name), MVX-preferred

- **Date:** 2026-07-04
- **Status:** Accepted (from `METADATA-PUBLISHER-NOTES.md` §3; ratified by
  Sherlock per the S4 rule — it touches domain semantics).

**Context.** The Metadata Publisher list endpoint returns the same table name
under multiple components — `CSYTAB` under both MVX and MJP, `CSYECT` under
four. Export binary headers identify a table by **name only** (no component).
A cache keyed on table name alone silently overwrites and diffs against the
wrong component's schema.

**Decision.** Key the schema cache on **(component, table_name)**. Refresh
stores all components; lookup **prefers MVX** by default. When an export table
name maps to more than one component, record `schema_component` and
`component_ambiguous: true` in that table's diff result.

**Rationale.** MVX is the standard M3 business/config component in nearly all
cases; being explicit prevents a silent wrong-schema diff a user would trust.

**Consequences.** `schema/cache.py` uses a composite key. The result contract
gains `schema_component` + `component_ambiguous` (additive — see ADR-005). PK
derivation reads index-`00` membership from the columns payload (ADR-002).

---

## ADR-005 — Result-JSON contract is code-owned and version-gated

- **Date:** 2026-07-04
- **Status:** Accepted (Sherlock S1: "extend it — you own it now").

**Context.** Spec §5 gives a starting result-JSON shape, not a locked schema.
The design needs several fields §5 doesn't list (`pk_source`, `schema_match`,
`cono_ambiguous`, `global_subset`, a modified-downgraded-to-hash flag, plus
ADR-004's `schema_component`/`component_ambiguous`) — the "never silently
lossy" markers. CLI/GUI byte-identical output and future AI-analysis both
depend on the contract.

**Decision.** `engine/src/m3diff/contract.py` (the dataclasses) is the **single
source of truth**. TS types are generated from it; a schema-validation test
enforces no drift. Spec §5 becomes descriptive docs that point at
`contract.py`, not the reverse. **Additive** changes (new fields) are free;
**breaking** changes (rename/remove/retype) require a `tool_version` bump and
their own ADR.

**Rationale.** Keeps freedom to evolve without ever silently breaking a
consumer; the version envelope already exists (`tool_version`), so use it.

**Consequences.** All five proposed additions land now. Contract changes are
the class of decision Sherlock ratifies (ADR-009/S4 governance).

---

## ADR-006 — Config-scope preset: metadata category MF, no curated list

- **Date:** 2026-07-04
- **Status:** Accepted (Sherlock S3 recommendation, owner-relayed).

**Context.** Spec F4 defined the "Configuration tables" preset as `CSY*, C*`
prefix globs — written before the MDP data was seen. Prefix globbing both
over-includes (`CSYLOG`, `CSYFUG` are TF/WF noise) and under-includes (config
tables outside `C*`).

**Decision.** Default the config preset to **`tableCategory = MF`**
(master/config) from MDP metadata. Keep prefix/custom globs available for
manual scoping. **No hand-curated allow-list** — it rots the moment Infor adds
tables. If MF proves too broad in practice, layer a small *exclude* list on top
of the category, not a full curated allow-list.

**Rationale.** Category is precise (cleanly separates MF/TF/WF) and
self-maintaining. `tableCategory` is metadata (what a table *is*) and is
orthogonal to our export-derived class (where its rows *live*) — show both.

**Consequences.** Requires the schema cache populated to resolve categories;
with no cache, fall back to prefix globs and flag the degraded scope.

---

## ADR-007 — Metadata Publisher auth in-house; no InforSDK dependency

- **Date:** 2026-07-04
- **Status:** Accepted (implementation call per S4; flagged to owner because it
  diverges from the reference projects and hinges on the publish-intent).

**Context.** The reference projects authenticate via a private `infor-sdk`
package (Azure DevOps feed, not PyPI) that reads the `.ionapi` and manages
tokens. m3diff is intended to be a clean, publishable personal project
(CLAUDE.md) and is stdlib-first.

**Decision.** m3diff does **not** depend on InforSDK. It parses the standard
`.ionapi` (`ci, cs, ti, saak, sask, pu, oa, ot, or, ev`) and performs the ION
OAuth2 password-grant itself (`POST {pu}{ot}`), caches the bearer token in
memory with JWT-`exp` minus skew, and calls `{gateway}/M3/mdprest/les/...`
directly. Base-URL construction is ported (pattern only, no tenant values) from
the reference's `get_auth_base`. One justified third-party dep allowed: a
pooled HTTP client (`requests`/`httpx`) for the ~5,375-call refresh.

**Rationale.** A private-feed dependency makes the project unpublishable and
couples it to employer tooling. The OAuth flow is ~30 lines; no SDK-specific
behavior is needed. The token grant and MDP calls are standard REST.

**Consequences.** We own token refresh/expiry logic (small, testable). If the
owner would rather not publish and prefers SDK expediency, this is the one call
to revisit. Secret handling per ADR-008-adjacent B1 (owner posture pending).

---

## ADR-008 — Company enumeration: observed CONOs from the classify pass, CMNCMP for labels

- **Date:** 2026-07-04
- **Status:** Accepted (Sherlock B2 design, owner-relayed).

**Context.** F2/F3 need the companies present in an export for the mode picker.
Scanning every COMPANY-class table just to enumerate CONOs is wasteful. The
company master is `CMNCMP` (PK `JICONO` alone; `JITX40` description;
`JICMTP` type; ~one row per company).

**Decision.** Derive the **selectable** CONO set from the **union of CONO
values actually observed during the classify pass** (already streaming every
COMPANY/MIXED table — aggregating is nearly free). Use `CMNCMP` **when present**
to *label* those numbers (`{JICONO → JITX40}`, e.g. "500 — Master Config"). If
`CMNCMP` is absent (scoped export), keep the numbers unlabeled.

**Rationale.** The question is "which CONOs actually have rows here," not "which
companies are defined" — an export may list 6 companies in `CMNCMP` but contain
data for only 2. Observed-from-classify is the true answer and robust to
partial exports; `CMNCMP` is decoration, not the source of truth.

**Consequences.** No dependency on `CMNCMP` being in scope. Labels are
best-effort. Ties into the classify pass (`classify.py`) that already
enumerates distinct CONOs per table.

---

## ADR-009 — `.ionapi` at-rest: stored file in `%APPDATA%/m3diff/`, locked-down ACL

- **Date:** 2026-07-04
- **Status:** Accepted (owner chose option (a)).

**Context.** The user uploads an `.ionapi`; it must persist across sessions so
"Refresh schema" doesn't re-prompt. Because we parse the file ourselves
(ADR-007), no SDK on-disk constraint applies — all postures were open. Owner
chose (a).

**Decision.** On upload, copy the `.ionapi` into the config-data root
(`%APPDATA%/m3diff/`, `~/.m3diff/` elsewhere) as a single file with a
**restrictive ACL** (current-user read only; 0600-equivalent). App settings
store only the **path**, never the contents; the contents are never logged.
The file lives outside `settings.json`, so spec §5's "never in app config"
holds.

**Rationale.** Simplest and most convenient; persists across sessions. The
`.ionapi` is read **only** at schema-refresh time — a compare never touches it.

**Consequences.** Secret at rest, mitigated by the ACL. DPAPI/Credential-Manager
encryption (option (c)) is a future hardening follow-up. "Remove credentials"
deletes the file.

---

## ADR-010 — DIVI masking: divisions ARE renamed → v1.1 remap (supersedes ADR-003)

- **Date:** 2026-07-04
- **Status:** Accepted (owner-corrected). **Supersedes ADR-003.**

**Context.** ADR-003 recorded "divisions not renamed → DIVI drop-only." On the
direct either/or question ("do divisions get renamed when you stand up a new
company, or stay the same?"), the owner answered **they get renamed** —
reversing the earlier note. Flagged to the owner in case the reversal was a
slip; treating the later pointed answer as authoritative.

**Decision.** DIVI masking (v1.1) uses the **remap** rule kind, not drop: a
user-supplied division mapping (company-A DIVI ↔ company-B DIVI) normalizes the
DIVI key component before comparison. **v1 is unchanged — masks CONO only.**

**Rationale.** If divisions are renamed, dropping DIVI would wrongly collide
rows from different divisions, and a plain match would wrongly split the same
logical division under two codes — either way, misleading diffs. Remap is
required for correctness.

**Consequences.** The mask-as-data abstraction (deliberately kept in ADR-003)
now has its confirmed consumer. v1.1 adds a small remap UI + mapping input and a
`pk_mask` entry `{column: "DIVI", kind: "remap", map: {…}}`. v1 scaffolding is
unaffected.

---

## ADR-011 — Directory layout: `engine/` + `desktop/` monorepo

- **Date:** 2026-07-04
- **Status:** Accepted (implementation call per S4; owner unblocked the choice).

**Context.** Needed a repo topology; the owner delegated it (S4: layout is the
implementer's call). Greenfield personal project — no upstream constraint.

**Decision.** Single monorepo: **`engine/`** (Python package — diff engine, CLI,
RPC; own `pyproject` + tests) and **`desktop/`** (Tauri shell + React UI), with
`reference/` and `docs/` at root. `engine/` must **never** import up into
`desktop/`; a CI check builds and tests `engine/` in isolation (S5).

**Rationale.** The engine must build/test/ship independently; a monorepo
versions it in lockstep with the shell without a submodule-or-publish dance for
a one-maintainer, two-surface project.

**Consequences.** The sidecar is built from `engine/`; the engine never
references the shell.

---

## ADR-012 — Rust toolchain now; build the shell locally, defer packaging to Phase 7

- **Date:** 2026-07-04
- **Status:** Accepted (owner-directed).

**Context.** The Tauri shell needs Rust (MSVC toolchain on Windows). Probing the
machine: **Visual Studio Community 2022 with the C++ (VC.Tools) workload is
present**, so a local MSVC Rust build links. Owner directed: install Rust now,
build and iterate the shell locally, and defer the Windows-installer packaging.

**Decision.** Install `rustup` (stable-x86_64-pc-windows-msvc). Build and iterate
the Tauri + React shell locally; **dev spawns the backend as
`python -m m3diff.cli serve` with `PYTHONPATH → engine/src`** (no pip install —
the engine is stdlib-only). Defer to a dedicated **Phase 7** on the designated
shipping machine: the **PyInstaller sidecar** (`externalBin`) and the **Windows
installer** (NSIS + embedded WebView2 bootstrapper).

**Rationale.** The UI + Tauri wiring is platform-agnostic and the bulk of the
work; packaging is machine-specific and lands wherever the release is cut.
Spawning from source sidesteps the machine's broken pip index (a private Azure
feed returning HTTP 402) for now.

**Consequences.**
- Confirmed working locally: `cargo build` (47s), frontend `tsc`+`vite`.
- Release will swap the dev python-spawn for a bundled sidecar — **not wired
  yet** (no sidecar binary exists until Phase 7).
- **Open — confirm before Phase 7:** which machine cuts the release artifact.
- The `.ionapi` at-rest storage (ADR-009) is a shell concern still to build.
- **Follow-up:** the config preset in the shell uses prefix globs, not the
  metadata `MF` category (ADR-006) — the category scope needs a schema-cache
  lookup (a small engine addition) before the preset can honor ADR-006.

---

## ADR-013 — Parallel diff: table-parallel across processes, serial-identical output

- **Date:** 2026-07-04
- **Status:** Accepted.

**Context.** The diff is per-table independent but CPU-bound in pure Python
(row decode, dict building, field compares) — threads can't help (GIL); only
zlib/blake2b release it. Real tenants reach ~4,000 tables / ~2M rows, and a
full serial `compare` runs minutes. Tables are the natural parallel unit.

**Decision.** Fan tables out across a **`ProcessPoolExecutor`**. A worker
re-opens the exports and (read-only) schema cache **once** in its initializer
and keeps them in a module global; only **paths** cross the process boundary (a
live `zipfile.ZipFile` / `sqlite3.Connection` is not picklable), and only an
**already-truncated `TableDiff`** comes back (IPC never carries raw rows).
Results are **reassembled in `scoped` order**, so the JSON is **byte-identical**
to the serial path regardless of completion order (blake2b/`repr` are
hash-seed-independent; each change list is sorted in `_build_table_diff`).

Concurrency is a `CompareOptions.workers` field, surfaced as CLI `--workers`:
**1 = serial (library default)**, **0 = auto** (all cores, engages only for
≥`_MIN_PARALLEL_TABLES` file-backed tables), **N = force N** (honored down to 2
tables). Parallel is gated on **re-openable (path-backed) sources + a
file-backed cache**; in-memory `BytesIO`/`SchemaCache(":memory:")` fall back to
serial — which is why the whole existing suite stays on the serial path
untouched (its fixtures are all in-memory).

**Rationale.**
- Processes are the only way to get real speedup for GIL-bound Python work.
- Path-based re-open keeps the pure-library `compare(ExportSource, …)` contract
  intact while sidestepping unpicklable handles.
- Serial default + capability gate means zero behavioral change for tests, the
  determinism golden, and the CLI==GUI byte-identical guarantee.

**Resilience (this machine is flaky — reboots under load, suspected RAM).** A
worker can glitch (a one-off, non-reproducible `TypeError` was seen unpickling a
result) or die outright (a broken pool then fails every pending future). Rather
than lose a long compare, `_resolve_future` **re-runs just the failed table
in-process** using the same `_diff_dispatch`. A *deterministic* bug fails again
there and propagates; only a *transient* is absorbed, and output stays
identical. A dead worker thus degrades that run to in-process for the remainder
instead of aborting — useful given the segfault history on huge tables.

**Consequences.**
- Validated on real data (189 MB, intra 100 vs 500, 11 metadata-keyed masters):
  **19.4s serial → 5.5s at 6 workers (~3.5×)**, byte-identical serial/parallel
  and run-to-run. Schema PKs turn former add/remove noise into real field-level
  diffs (e.g. MITAUN ~34k modified rows).
- RPC/GUI accept an optional `workers` param (default auto), so the shell can
  parallelize too; still gated the same way.
- **Not exercised:** a full-tenant all-tables all-cores sweep — deliberately, as
  that is the max-load scenario in the reboot history. Capability is proven at
  scoped size; scale up deliberately once hardware is trusted.
- **Windows `spawn`:** relies on the CLI/RPC being import-safe (`main()` guarded)
  so children don't re-execute — verified.

---

## ADR-014 — Degenerate metadata PK: detect collisions, fall back to full-row identity

- **Date:** 2026-07-04
- **Status:** Accepted (implemented); **semantics awaiting owner ratification**
  (contract addition + fallback behavior — flag, don't silently decide).

**Context.** Found on real data: MITBAL's export carries `mbwhlo` (a PK column)
**blank on 359,064 of 359,077 rows** for one company. The masked key
`(mbwhlo, mbitno)` collapses to effectively `(mbitno)`: 29,935 distinct keys for
359k rows. The A-side index silently overwrote colliding rows (last-wins), so
`rows_a` said 359k but only 30k were compared; "modified" counted per-B-row
against arbitrary survivors; and — the tell that exposed it — the hash-downgrade
never fired (index stayed under the 200k threshold) so a >200k-row table
reported `modified_detail: true`. The result JSON for such a table was silently
wrong. Any table whose metadata PK isn't unique **in the export's actual data**
(blank PK columns being the observed cause) had this failure mode.

**Decision.** During indexing (metadata PKs only), a repeated masked key raises
an internal `_DegeneratePkError`; side B detects repeats the same way via a key
set. `_diff_one` catches it and **re-runs the table with full-row identity** —
the existing heuristic semantics: set membership, add+remove instead of a
possibly-false "modified", no rows silently dropped. The table is flagged
`pk_degenerate: true` in the result JSON (additive contract change, free per
ADR-005), with `pk_source: "heuristic"`.

**Rationale.**
- A PK that doesn't key the data is a lie; keying on it loses rows silently.
  Full-row identity is the engine's existing honest degradation ("never a false
  modified") — reuse it rather than invent a third semantics.
- Detect-and-restart costs one aborted pass only for degenerate tables; clean
  tables pay a dict-membership check per row.
- Heuristic (full-row) keys deliberately do NOT raise on collision: a full-row
  duplicate is a genuinely indistinguishable row, and raising would recurse.

**Consequences.**
- Golden tests: A-side collision, B-side-only collision, non-degenerate not
  flagged, intra-mode cross-company match not misflagged, fallback across the
  process boundary (parallel path). Suite 126 → 130.
- `_one_sided` does not detect degeneracy (append-only, no keyed index → no
  data loss); its lists may contain repeated masked keys. Acceptable for v1.
- B-side detection holds a key set (~rows_b tuples) for metadata-PK tables —
  bounded by the same scale as the A index.
- The GUI can badge `pk_degenerate` tables ("compared by full row — export's PK
  column(s) blank"); not yet wired.
- Open question for owner: should a degenerate table *also* report which PK
  column(s) were blank? (Diagnosable from added/removed rows; deferred.)

---

## ADR-015 — The "MITBAL segfault" verdict: hardware memory corruption under load

- **Date:** 2026-07-04
- **Status:** Accepted (diagnosis; hardware remediation is outside the repo).

**Context.** A prior session recorded a segfault in `compare --schema-db` on
MITBAL and deferred it pending a hardware check (the machine also spontaneously
rebooted). Tonight's work reproduced a crash *and* explained the whole pattern.

**Evidence.** All failures occurred **only under sustained heavy load**, each in
a *different* place — the signature of memory corruption, not a code bug:
access violation inside `hashlib.blake2b` (AVX2-heavy hot loop, faulthandler
traceback captured); a one-off `'int' object is not an iterator` unpickling a
worker result (never recurred); `TypeError: 'range_iterator' + int` in the row
decoder; `AttributeError: 'function' object has no attribute 'name'` on a
dataclass field list — the last two on back-to-back runs of the *same* probe
over the same data. Plus **three Kernel-Power 41 dirty reboots the same day**
(a userspace bug cannot reboot a machine) and a clean WHEA log. The 130-test
suite and all small/short runs pass consistently.

**Verdict.** The i9 corrupts memory under sustained load (pattern consistent
with the well-known Raptor Lake voltage-degradation defect: AVX-heavy crashes,
random type confusion, spontaneous resets). **The diff engine is exonerated** —
the original "MITBAL segfault" was this hardware being reliably stressed by any
heavy m3diff workload, not a bug in the hash-downgrade path.

**Consequences.**
- No code workaround (e.g., swapping blake2b for sha256) — it would mask a
  hardware fault, not fix it, and blake2b is not at fault.
- Heavy-load validation results from this machine carry an asterisk until the
  hardware is remediated; byte-identical cross-checks that *passed* are
  self-consistent evidence those particular runs were clean.
- Full-tenant / all-cores performance validation (spec §6.2) moves to a healthy
  machine (candidate: the Phase 7 shipping machine, still to be confirmed).
- Owner to-do (outside repo): BIOS/microcode update (Intel 0x12B+), stress
  verify (OCCT / Prime95 small-FFT AVX2; MemTest86 to rule RAM in/out), and an
  Intel RMA claim if degraded — 13th/14th-gen K SKUs carry an extended 5-year
  warranty for exactly this defect.

---

## ADR-016 — Category scoping: metadata table categories as a first-class scope filter

- **Date:** 2026-07-04
- **Status:** Accepted. Implements the engine half of ADR-006's preset.

**Context.** The Metadata Publisher categorizes every table; the cache already
stores it. Real tenant data (5,381 cached tables / 4,171 in the test export,
100% category coverage): **MF** master files 1,944 tables / 92% of rows
(business masters *and* system config — M3 has no separate "configuration"
category), **TF** transaction files 1,329 / 8%, **WF/ST/SF** work, statistics
and join-dynamic tables 898 / ~0% — pure noise for tenant comparison. Owner
asked for master/config/transaction scoping.

**Decision.** `CompareOptions.categories` / CLI `--category MF[,TF…]` / RPC
`categories`. Resolution: `SchemaCache.tables_in_categories()`, taking each
table's category from the same component `resolve()` would pick (MVX-preferred,
ADR-004), case-insensitive. **Unions with `--tables`** (either selects a
table); no filter at all still means everything. Category scoping without a
cache raises (`--schema-db` required); tables absent from the cache have no
category and are selectable only by name/glob.

**Rationale.**
- Union lets the preset compose: `--category MF` for masters, plus globs to
  pull in specific extras; "config only" is expressible today as prefix globs
  (`CSY*,CMN*,CRS*`) since config lives inside MF.
- Erroring without a cache beats silently scoping to nothing.
- MVX-preferred keeps category resolution consistent with PK resolution.

**Consequences.**
- The ADR-006 GUI preset is unblocked: a category picker (MF default) instead
  of the current prefix-glob preset; UI wiring still TBD.
- WF/ST/SF exclusion is the practical win: ~29% of tables that should never be
  diffed no longer generate noise or wasted work.
- Suite 130 → 138 (cache MVX-preference, union semantics, no-cache error, CLI).

---

## ADR-017 — Persist and surface the maintaining program (`tableMaintainedBy`)

- **Date:** 2026-07-04
- **Status:** Accepted (additive contract change per ADR-005).

**Context.** Owner asked whether the metadata can attribute tables to the M3
program that maintains them (OCUSMA → CRS610, OOTYPE → OIS010). MDP's
``getTables`` already returns ``tableMaintainedBy`` (documented in
METADATA-PUBLISHER-NOTES.md §1a) — the original refresh simply didn't persist
it. Verified live: both examples resolve exactly as expected.

**Decision.** Store ``maintained_by`` on the cache's ``tables`` row (with an
additive ALTER-TABLE migration for pre-existing caches), carry it through
``TableSchema`` → ``resolve_pk`` → ``PrimaryKey`` → ``TableDiff`` →
result JSON (``maintained_by``, null when unknown). Populate cheaply via
``m3diff schema refresh --info-only`` — a single ``getTables`` call that
updates category/description/maintained-by for already-cached tables without
re-fetching any columns. The GUI drill-down shows it as a program chip.

**Rationale.**
- It's the natural triage hint: a drifted config table's fix lives in its
  maintenance program — showing "CRS610" turns a diff row into an action.
- Known even when the PK falls back to heuristic (the schema still names the
  program), so it's carried on ``PrimaryKey`` independent of ``pk_source``.
- ``--info-only`` exists because re-fetching 5,381 tables' columns to gain one
  list-endpoint field is wasteful; the list call is a single request.

**Consequences.**
- Coverage is partial by nature: on real data 982 / 5,381 cached tables name a
  maintainer — overwhelmingly the master/config tables, which is exactly the
  set that benefits. Transaction tables (written by many programs) mostly don't.
- Result JSON gains ``maintained_by`` (additive); TS types updated.
- Suite 138 → 144.

---

## ADR-018 — GUI file exports are engine-rendered; settings persist locally

- **Date:** 2026-07-04
- **Status:** Accepted.

**Context.** The GUI needed "Save CSV/MD/JSON" (renderers existed only behind
CLI ``--format``) and kept forgetting the schema-DB / ``.ionapi`` paths on every
launch.

**Decision.**
- **Exports:** a ``render`` RPC takes the result dict + format and returns the
  string produced by the *same* renderers the CLI uses; the shell writes it via
  a minimal ``save_text_file`` Tauri command (path always from the OS save
  dialog). Enabled by ``contract.from_dict()`` — the inverse of ``to_dict``,
  tolerant of additive fields missing from older JSON — which is also the
  groundwork for results history (reopen saved runs).
- **Settings:** persisted in WebView ``localStorage`` — paths and toggles only,
  never file contents. The ``.ionapi`` stays wherever the user keeps it;
  ADR-009's at-rest import/ACL story remains TBD and unchanged.

**Rationale.** Rendering in the engine preserves the CLI==GUI identity
guarantee (a GUI-saved file is byte-identical to ``m3diff compare --format``);
re-implementing renderers in TS would fork it. ``localStorage`` needs no new
Tauri plugin or Rust dependency — proportionate for non-secret preferences.

**Consequences.**
- The RPC gains ``render``; ``schema_refresh`` gains ``info_only`` (the GUI's
  "Update table info (fast)" button).
- Results view: maintained-by column, program-aware search, save buttons.
- Suite 144 → 148; cargo + tsc/vite clean.

---

## ADR-019 — Pool liveness canary: prove the worker pool alive or fall back to serial

- **Date:** 2026-07-04
- **Status:** Accepted. Found by the first live GUI smoke test.

**Context.** With the engine spawned by the Tauri shell (piped stdio, no
console), ``ProcessPoolExecutor``'s spawn handshake **wedges**: all 32 workers
sat at zero CPU with no Python frames (py-spy), the parent waited forever in
``as_completed``, and — second bug — cancellation was never polled because no
future ever completed. The identical code parallelizes correctly when launched
from a terminal (every CLI run that day). Third bug, same neighborhood: workers
from earlier CLI runs were found still alive hours later (``shutdown(wait=False)``
orphaning idle workers on process exit).

**Decision.** Before dispatching real work, submit a trivial **canary** task.
If it doesn't complete within ``_CANARY_GRACE`` (15s) the pool is declared
unusable: shut down without waiting, set a **sticky per-process flag** (only
the first compare pays the grace wait; later compares go straight to serial),
and run the compare **in-process serial** — same results, just slower. The
result loop replaces ``as_completed`` with ``futures.wait(timeout=1)`` polling
so cancellation is responsive in every state, and the success path uses
``shutdown(wait=True)`` (workers are idle then) so none are leaked.

**Rationale.** Environment-dependent deadlock → graceful degradation beats
both hanging (unacceptable in a GUI) and disabling parallelism everywhere
(the CLI's 3.5× win is real). The canary costs milliseconds on a healthy pool.

**Consequences.**
- Verified live: the GUI compare that previously hung forever now completes in
  ~20s (15s canary + serial run); CLI parallel behavior unchanged; zero python
  processes remain after app exit.
- **Open (root cause):** why the spawn handshake wedges under a piped-stdio /
  no-console parent on Windows + Python 3.14 — filed as a follow-up
  investigation; a fix there would restore GUI parallelism.
  **→ Resolved by ADR-020**; the canary stays as a safety net.
- Suite 148 → 150.

---

## ADR-020 — Root cause of the spawn wedge; workers get CREATE_NO_WINDOW

- **Date:** 2026-07-04
- **Status:** Accepted. Closes ADR-019's open question.

**Context.** ADR-019 mitigated a wedge whose cause was unknown. Bisection with
a matrix of minimal probes (real serve under four console configurations; bare
``ProcessPoolExecutor`` across thread × console-flag cells; a serve-*shaped*
probe with ingredients added one at a time) isolated it exactly.

**Root cause.** On Windows + CPython 3.14.3: **a blocking read on *piped
stdin* in one thread deadlocks the multiprocessing spawn handshake of any
console-**sharing** child created from another thread.** The child freezes
while attaching the parent's console, before executing any Python (matching
the empty py-spy stacks). Minimal repro pair: a process whose main thread
loops ``for line in sys.stdin`` (stdin = pipe) while a daemon thread runs
``ProcessPoolExecutor(...).submit(noop)`` → 100% wedge; the identical process
with the main thread *sleeping* instead of reading → 0.2s success. This is
precisely the serve process's shape (main thread reads the NDJSON pipe;
compares run on task threads) — and why the CLI never wedged (its main thread
runs the compare and is never mid-read on stdin). Console *presence* is
necessary but not sufficient; DETACHED_PROCESS parents were healthy only
because their children never attach a shared console. Likely a CPython or
conhost bug — worth reporting upstream with the repro pair.

**Decision.** Start pool workers with **CREATE_NO_WINDOW** so they get their
own hidden console instead of attaching the parent's. Implemented as a
surgical patch (``_patch_worker_console_flags``): ``popen_spawn_win32`` is
given a shim module whose ``CreateProcess`` ORs the flag in — the real
``_winapi`` used by ``subprocess`` and everything else is untouched.
Idempotent, Windows-only, applied just before pool creation; if the stdlib
layout ever changes the patch silently no-ops and the ADR-019 canary still
protects correctness.

**Rationale.** CREATE_NO_WINDOW over DETACHED_PROCESS: workers keep a valid
(hidden) console for std handles — the more conservative change. Both were
proven healthy in the matrix; sharing the parent console is the only wedge
case. There is no public multiprocessing API for worker creation flags, hence
the scoped shim.

**Consequences.**
- The exact previously-wedged configs (piped stdio, with/without console) all
  complete in **0.7s vs 15.5s** (canary fallback) — verified via the repro
  matrix AND live in the GUI (ST-category compare: results in <8s, previously
  ≥15s serial-only).
- GUI compares regain full parallelism; ADR-019's canary + sticky fallback and
  the in-process retry (ADR-013) remain as layered safety nets.
- Regression tests: patch applied-and-surgical (shim only, idempotent), and an
  end-to-end serve-over-pipes compare asserting completion well under the
  canary grace (a wedge cannot pass it). Suite 150 → 152.
- Worker crash tracebacks now go to a hidden console rather than the parent's
  terminal — acceptable; BrokenProcessPool handling covers behavior.

---

## ADR-021 — Packaging (PyInstaller onefile sidecar + NSIS) and post-mortem logging

- **Date:** 2026-07-04
- **Status:** Accepted. Executes the shape fixed in ADR-001/012.

**Context.** Phase 7: ship a Windows installer, and give the field a way to
debug failures after the fact (the engine previously persisted **no** logs and
error frames carried no tracebacks — the cp1252 bug was diagnosed only because
the failure happened while a developer was watching).

**Decisions.**
- **Sidecar = PyInstaller onefile, console subsystem.** One 11.7 MB
  ``m3diff-engine.exe`` doubles as the GUI backend (``serve``) and a standalone
  CLI (``compare``/``classify`` from any terminal). The shell spawns it with
  ``CREATE_NO_WINDOW`` — no console flash, and per ADR-020 the engine must
  never share a console anyway. PyInstaller 6.21 handles Python 3.14.3; httpx
  is frozen in so GUI schema refresh works. ``entry.py`` calls
  ``multiprocessing.freeze_support()`` first — without it every spawned pool
  worker would boot another CLI (fork bomb). Frozen parallel compare verified:
  0.8s end-to-end including worker spawn.
- **Sidecar resolution in the shell:** if ``m3diff-engine.exe`` exists next to
  the app exe (where Tauri's ``externalBin`` places it), spawn it; else fall
  back to ``python -m m3diff.cli serve`` from source (dev). ``M3DIFF_PYTHON``
  forces the dev path. No tauri-plugin-shell dependency.
- **Installer:** NSIS only (``targets: ["nsis"]``; MSI would drag in WiX), with
  the WebView2 ``embedBootstrapper`` per ADR-001. Build pipeline:
  ``scripts/build-sidecar.ps1`` (venv → PyInstaller → triple-suffixed binary)
  then ``npm run tauri build``. Build outputs are gitignored.
- **Logging** (all under ``%APPDATA%/m3diff/logs/``):
  - ``engine.log`` — rotating (2 MB × 3), ``m3diff.*`` logger tree, enabled by
    ``serve`` on real stdio (CLI behavior unchanged). Logs request lifecycle
    with durations and summaries, full tracebacks on task failure (the UI
    frame keeps the short message), canary fallbacks, worker retries, and
    degenerate-PK fallbacks per table. ``.ionapi`` param values are redacted
    to a basename; level via ``M3DIFF_LOG_LEVEL``.
  - ``faulthandler.log`` — armed at serve start with a dedicated open file, so
    hard crashes (access violations) leave a Python traceback.
  - ``shell.log`` — written by the Rust shell: which engine it spawned, every
    engine **stderr** line (PyInstaller bootstrap errors land there; in a
    packaged GUI app inherited stderr goes nowhere), and engine exit.
  - Logging failure degrades to no-op — it must never break a compare.

**Consequences.**
- "Send me the three files in ``%APPDATA%/m3diff/logs``" is now a complete
  post-mortem request.
- Onefile trade-off: ~1s unpack on first sidecar start per boot; acceptable.
- Release artifact still needs a signing story (unsigned NSIS will trip
  SmartScreen) — out of scope for v1, noted for any public distribution.
- License must be finalized before publishing an installer (PROGRESS item).
