# PROGRESS — m3diff

Living status file. Update at the end of every working session so a fresh
session can rehydrate with: "read CLAUDE.md and PROGRESS.md, run the tests,
and tell me where things stand."

Last updated: 2026-07-04, by Watson (Claude Code session)

---

## Current status (one-liner)

**Diff is table-parallel (ADR-013) AND a silent-correctness bug is fixed
(ADR-014):** a metadata PK column blank on the wire (MITBAL: `mbwhlo` empty on
359,064/359,077 rows) made masked keys collide and silently overwrite rows —
now detected, with an honest full-row-identity fallback flagged
`pk_degenerate`. Hardware verdict is in (ADR-015): the i9 **corrupts memory
under sustained load** — the old "MITBAL segfault" was hardware being stressed,
the engine is exonerated, and heavy validation moves to a healthy machine.
Engine **130 tests**. Only packaging (Phase 7) + a live `tauri dev` smoke remain.

## Next up (the 1–3 things to do next)

- [ ] **Hardware first (owner, outside repo):** BIOS/microcode 0x12B+, OCCT /
      Prime95 AVX2 stress + MemTest86, Intel RMA if degraded (5-yr extended
      warranty on 13th/14th-gen K). Until then: no heavy compares on this box.
- [ ] Phase 7 packaging on the shipping machine (confirm which machine):
      PyInstaller sidecar + NSIS/WebView2. Also: run `npm run tauri dev` to
      click through the live UI; MF-category scope preset (ADR-006).
- [ ] On a healthy machine: full-tenant all-tables parallel sweep (perf
      validation vs spec §6.2) + survey how many tables report `pk_degenerate`
      on real exports (informs whether the GUI needs a badge/filter for it).

---

## Build phases

Check items as they land. Each feature ships with tests (see spec §6.3).

### Phase 0 — Planning
- [x] Read spec, CLAUDE.md, reference scripts
- [x] Implementation plan approved by owner (`PLAN.md`)
- [x] Directory structure agreed (`engine/` + `desktop/`, ADR-011)
- [x] Tauri vs Electron decided (ADR-001: Tauri)
- [x] Spec §7 open questions answered (ADR-002/004/006/008/010, `PLAN.md` §5)
- [x] Repo scaffolded: package skeleton, license, README, `.gitignore`

### Phase 1 — Format reader (library core) ✅
- [x] Binary export reader (header + rows per spec §2.1), streaming
- [x] Row-length invariant asserted (per-row checksum)
- [x] Absent-from-bitmap CONO preserved as absent (⇒0 mapping lands in classifier)
- [x] null vs empty-string distinction preserved
- [x] TABLE_INFO deserializer (§2.2), with a faithful serializer for fixtures
- [x] Fixture generator (synthetic exports built in-test; no real data)
- [x] Tests: round-trip across bitmap boundaries, invariant, truncation,
      CONO detection, 268-column wide table, zip-stream read (26 tests)

### Phase 2 — Classifier + ExportSource ✅
- [x] NO_CONO / GLOBAL / COMPANY / MIXED / EMPTY classification (stop-at-CONO scan)
- [x] Distinct-CONO enumeration per export (observed_conos, ADR-008)
- [x] Multi-match CONO-field heuristic flagging (cono_ambiguous)
- [x] `ExportSource` (zip + directory, table enumeration, TABLE_INFO manifest)
- [x] Centralized CONO rules (`cono.py`: absent/blank ⇒ 0, no leading-zero norm)
- [x] Per-table error tolerance (PARSE_ERROR, spec F6)
- [x] Golden tests vs hand-built fixtures (17 tests)

### Phase 3 — Schema cache + PK resolution ✅
- [x] SQLite schema store, keyed on (component, table_name), MVX-preferred (ADR-004)
- [x] PK resolution with heuristic fallback + pk_source tagging
- [x] CONO masking helper (`masked_key`) — the danger zone, golden-tested
- [x] Metadata Publisher fetch: getTables → getColumnsUsedByTable; PK from
      index-00 membership; index-keys fallback; in-house ION OAuth (ADR-002/007)
- [x] .ionapi parse (secrets redacted in repr); `schema refresh` CLI wired
- [x] Offline operation when cached / heuristic (engine tests + refresh-into-cache)
      Note: .ionapi at-rest storage/ACL (ADR-009) is a GUI-upload concern (Phase 6);
      the live MDP base-URL join still needs verification against a real instance.

### Phase 4 — Diff engine ✅
- [x] CONO masking in PK comparison (mask-as-data; v1 = CONO drop only)
- [x] Set membership (added / removed / both), index-one-side/stream-the-other
- [x] Field-level diff with ignore-list (default *lmdt/*rgdt/… + CONO)
- [x] Schema-mismatch handling (compare on column intersection)
- [x] Truncation caps + counts; hash-downgrade for huge tables (modified_detail)
- [x] Result JSON per contract (`contract.py` is source of truth, ADR-005)
- [x] Golden tests: identical/added/removed/modified/CONO-mask/null-vs-empty/
      schema-mismatch/error-tolerance/NO_CONO/global-subset/determinism
- [x] **Table-parallel across processes (ADR-013):** `CompareOptions.workers` /
      CLI `--workers` (1=serial default, 0=auto, N=force). Byte-identical to
      serial (results reassembled in scoped order); gated on path-backed sources
      + file cache so in-memory fixtures stay serial. In-process retry recovers
      a glitched/dead worker. Tests: gate decisions, serial==parallel on-disk,
      corrupt-table tolerance across the process boundary, cancellation, retry
      seam (`test_parallel.py`, 12 tests).
- [x] **Degenerate metadata PK detection + fallback (ADR-014):** a repeated
      masked key (PK column blank on the wire) aborts the keyed pass and re-runs
      the table on full-row identity; result flagged `pk_degenerate: true`
      (additive contract field). Found via MITBAL on real data — previously
      silently overwrote 329k rows and reported plausible-looking garbage.
      Golden tests: A-side, B-side-only, non-degenerate unflagged, intra-mode
      not misflagged, degenerate table through the parallel path.

### Phase 5 — CLI ✅ (schema refresh stubbed → 3b)
- [x] `m3diff compare` (intra/inter/global) + scope filter, strict-null, no-mask-cono
- [x] `--category MF[,TF…]` scope from metadata categories (ADR-016); unions
      with `--tables`, requires `--schema-db`. Real export: MF=1,944 tables/92%
      of rows, TF=1,329/8%, WF+ST+SF=898/~0% (excludable noise)
- [x] `maintained_by` (ADR-017): MDP's `tableMaintainedBy` persisted in the
      cache (auto-migration), surfaced in result JSON, populated via
      `schema refresh --info-only` (one call, no column re-fetch). Real cache:
      982/5,381 tables named (OCUSMA→CRS610, OOTYPE→OIS010)
- [x] `m3diff classify` (classification CSV + summary)
- [ ] `m3diff schema refresh` — stubbed pending Phase 3b (MDP client)
- [x] CLI and (eventual) GUI produce identical result JSON (byte-identical test)

### Phase 6 — Desktop shell + UI (built; packaging → Phase 7)
- [x] Tauri v2 shell over NDJSON-over-stdio (ADR-001); dev spawns `python -m
      m3diff.cli serve` via PYTHONPATH. PyInstaller **sidecar deferred to Phase 7**
- [x] Upload + per-export summary (native file dialog + classify); drag-drop TBD
- [x] Mode selection + table scope filter — **category preset wired** (ADR-006
      done end-to-end): Scope view has a "By metadata category" preset with
      MF/TF/WF/ST/SF checkboxes (MF default), warns when no schema DB is set;
      custom globs retained. Drill-down shows the maintaining-program chip
      (ADR-017) and a `pk_degenerate` warning badge
- [x] Progress reporting + cancel + per-table error tolerance (F5/F6, UI-wired)
- [x] Results dashboard + table drill-down + row/field drill-down
- [x] Export renderers JSON/CSV/Markdown in engine + CLI `--format`; UI saves
      all three **engine-rendered** via the `render` RPC (ADR-018) — byte-
      identical to CLI output. Results table: maintained-by column + program-
      aware search; pk_degenerate marked
- [x] Settings: `.ionapi` path, schema refresh (+ "Update table info (fast)" =
      info-only), ignore-fields, null/mask toggles; **persisted across launches**
      (localStorage, paths only — ADR-018). `.ionapi` at-rest import/ACL
      (ADR-009) still TBD
- [ ] Results history (reopen without reprocessing)
- [x] **Live `tauri dev` smoke — PASSED (driven end-to-end via screen control):**
      backend spawn + ping; Settings (paths persisted across an app restart;
      info-only refresh ran OAuth+MDP live); load + classify (matches CLI:
      46 GLOBAL / 27 MIXED, 11 companies); category preset; compare; Results
      grid incl. maintained-by (CCM111→CRS014) and a real degenerate-PK marker
      (MNSCRE); drill-down; engine-rendered CSV save verified on disk.
      **Found + fixed:** pool wedge under Tauri-spawned engine → canary +
      serial fallback (ADR-019); cancel unresponsive while wedged → wait-loop
      polling; worker leaks → shutdown(wait=True) on success; drill-down
      buried under long tables → capped grid height + sticky header

### Phase 7 — Packaging + load test
- [ ] Windows installer (priority)
- [ ] macOS / Linux (best-effort)
- [ ] Performance validation vs spec §6.2 (full-tenant zips, local only)
- [ ] README + usage docs
- [ ] License finalized before any publish

### Post-MVP (not now)
- [ ] "Analyze with AI" over diff JSON
- [ ] DIVI masking / division remap (v1.1 — remap, not drop; ADR-010)

---

## Decisions log

Detail lives in `DECISIONS.md`; headlines here.

- 2026-07-04 ADR-001 → shell = Tauri (Python sidecar, NDJSON-over-stdio)
- 2026-07-04 ADR-002 → MDP schema fetch = bulk on explicit refresh; endpoint confirmed
- 2026-07-04 ADR-003 → DIVI drop-only — **superseded by ADR-010**
- 2026-07-04 ADR-004 → schema cache keyed (component, table_name), MVX-preferred
- 2026-07-04 ADR-005 → result-JSON contract code-owned (`contract.py`), version-gated
- 2026-07-04 ADR-006 → config preset = metadata category MF, no curated list
- 2026-07-04 ADR-007 → MDP auth in-house; no InforSDK dependency (publishability)
- 2026-07-04 ADR-008 → company enum from classify pass; CMNCMP for labels
- 2026-07-04 ADR-009 → .ionapi stored in `%APPDATA%/m3diff/`, ACL-locked (option a)
- 2026-07-04 ADR-010 → DIVI **remap** in v1.1 (divisions are renamed); supersedes ADR-003
- 2026-07-04 ADR-011 → directory layout = `engine/` + `desktop/` monorepo
- 2026-07-04 ADR-012 → Rust toolchain now; build shell locally, defer packaging
- 2026-07-04 ADR-013 → diff table-parallel across processes; serial-identical
  output; in-process retry for flaky workers
- 2026-07-04 ADR-014 → degenerate metadata PK ⇒ full-row fallback +
  `pk_degenerate` flag (semantics awaiting owner ratification)
- 2026-07-04 ADR-015 → "MITBAL segfault" = hardware memory corruption under
  load; engine exonerated; heavy validation moves to a healthy machine
- 2026-07-04 ADR-016 → `--category` scope from metadata categories (MF/TF/WF/
  ST/SF), MVX-preferred, unions with `--tables`; engine half of ADR-006
- 2026-07-04 ADR-017 → persist + surface `tableMaintainedBy` (OCUSMA→CRS610);
  `schema refresh --info-only`; result JSON gains `maintained_by`
- 2026-07-04 ADR-018 → GUI exports engine-rendered (`render` RPC + `from_dict`);
  settings persisted via localStorage (paths only, never contents)
- 2026-07-04 ADR-019 → pool liveness canary; wedged pool ⇒ sticky serial
  fallback; cancel-responsive wait loop; no worker leaks

## Open questions / blockers

- None blocking chunks 1–6.
- DIVI reversal (ADR-010) is flagged to the owner for a sanity double-check —
  it contradicts an earlier "divisions should not be renamed" note. Non-blocking
  (v1.1 only).
- Commit-author identity resolved: commits use a personal email (not the
  employer domain), set repo-locally.

## Notes for next session

- Phase 6 committed as chunk 10: `desktop/` (Tauri v2 + React). Rust 1.96.1
  installed via rustup (MSVC); cargo bin added to the user PATH. `cargo build`
  → 47s clean; frontend `tsc`+`vite` clean; engine still 113 tests.
- Backend spawn in dev = `python -m m3diff.cli serve` via PYTHONPATH (no pip).
  **The machine's pip is pointed at a private Azure feed returning HTTP 402** —
  it can't install even `hatchling`, so an editable install fails; this will
  matter for Phase 7 PyInstaller packaging (needs pip for pyinstaller/httpx).
- NOT yet done: a live `tauri dev` RPC round-trip (needs the GUI window, owner
  runs it); the sidecar packaging; the MF-category preset; results history; the
  `.ionapi` at-rest storage (ADR-009).
- MDP base-URL: **confirmed live** and fixed (ADR-002/publisher `mdp_base_url`:
  SSO host `mingle-sso` → gateway `mingle-ionapi`). Schema refresh streams.
- **Real-data validation (189 MB / 4,171 tables / ~2M rows, local fixture):**
  - `classify`: 17 s, **0 parse errors**; 46 GLOBAL + 27 MIXED tenant-global
    tables found (the copy-gap the tool exists for); companies 1/50/100/125/150/
    400/500/550/600/750/900.
  - `compare` intra 100 vs 500, all tables: 289 s, 0 errors, 282 modified /
    155 missing-in-B. Correct end-to-end.
  - Observations → follow-ups: (a) heuristic PKs make big tables read as mostly
    add/remove (MITBAL ~725k "changes") — the schema cache turns those into real
    field-level diffs; (b) all-tables + heuristic → a 168 MB result JSON, so the
    config-preset scope (or schema PKs) is the intended default for speed + a
    sane payload, especially for the GUI webview.
- Other caveats: httpx is the `[schema]` extra; ruff/mypy not installed here.

### Session-end handoff (read this first in a fresh session)
- **All committed, nothing lost.** Engine (138 tests) + desktop shell both build.
- **Category scoping shipped end-to-end (ADR-016 + UI):** `--category MF` in
  CLI/RPC and a "By metadata category" preset in the Scope view (MF default).
  On the real export every table categorizes: MF 1,944 (92% of rows), TF 1,329
  (8%), WF/ST/SF 898 (~0%, noise).
- **Maintaining program shipped (ADR-017):** `maintained_by` in cache + result
  JSON + drill-down chip + Results column; real cache populated via
  `schema refresh --info-only` (982/5,381 tables named; OCUSMA→CRS610,
  OOTYPE→OIS010).
- **GUI polish round (ADR-018):** Save JSON/CSV/MD engine-rendered (`render`
  RPC + `contract.from_dict` — the groundwork for results history), settings
  persisted across launches, "Update table info (fast)" button, program-aware
  results search.
- **LIVE SMOKE PASSED (ADR-019):** the whole UI was driven end-to-end against
  the real export via screen control — every view works. It caught and fixed
  a real ship-blocker: the worker pool wedges when the engine is spawned by
  Tauri (piped stdio/no console; fine from a terminal) → liveness canary +
  sticky serial fallback + responsive cancel + no worker leaks. Root cause of
  the wedge (Win + Py 3.14 spawn handshake under a no-console parent) is an
  open follow-up — fixing it would restore GUI parallelism. Engine **150
  tests**; cargo + tsc/vite clean.
- **Diff is now table-parallel (ADR-013).** CLI `--workers` (0=auto default in
  the CLI, 1=serial, N=force). Proven byte-identical to serial on real data and
  ~3.5× at 6 workers on a scoped masters run. In-process retry means a flaky
  worker (or a dead one) is re-run locally instead of aborting the whole compare.
- **Silent-correctness bug fixed (ADR-014):** blank PK columns on the wire made
  masked keys collide → rows silently overwritten → plausible-looking wrong
  results (MITBAL: only 29,935 of 359,077 rows actually compared). Now: detect,
  fall back to full-row identity, flag `pk_degenerate: true`. Owner should
  ratify the semantics (it's a contract addition).
- **HARDWARE VERDICT (ADR-015): this machine corrupts memory under sustained
  load.** Four distinct impossible-type-confusion/access-violation failures in
  four different code sites in one evening, all under load, plus 3× Kernel-Power
  41 reboots the same day; 130-test suite and small runs always clean. Do NOT
  run heavy compares here until BIOS microcode 0x12B+ / stress-verify / RMA.
  Real-data numbers recorded this session (MITBAL counts etc.) carry an
  asterisk; re-validate on a healthy machine.
- **Schema cache BUILT:** `C:\Projects\m3Diff\schema.db`, 5,381 tables,
  MVX-preferred; PK resolution verified on real data (e.g. MITBAL →
  MBCONO/MBWHLO/MBITNO; CSYTAB resolves MVX, ambiguous). No need to re-download.
- **pip FIXED:** removed a private Azure DevOps `extra-index-url` from the user's
  `pip.ini` (backup `pip.ini.bak` still holds the OLD PAT — user should delete
  it and **rotate that Azure PAT**, which was echoed to a terminal). httpx
  installed from PyPI.
- **MITBAL segfault → treated as HARDWARE (owner call, 2026-07-04).** The flaky
  i9 reboots under load — a userspace segfault can't cause that, so it's
  hardware/RAM, not a `diff.py` bug. Not chased further. A one-off, unreproducible
  worker `TypeError` (unpickling a result) was seen once during the parallel real
  run and did not recur across repeated reruns — consistent with the same flaky
  hardware; ADR-013's in-process retry absorbs exactly this. Still prudent before
  a big all-cores sweep: check Event Viewer (WHEA-Logger / BugCheck / Kernel-Power
  41) and run MemTest86. Schema-keyed path is now validated on real masters (see
  above), not just a small table.
- Real full-tenant export (gitignored) at `C:\Projects\m3Diff\fixtures\*.zip` —
  never commit it or leak its values.
