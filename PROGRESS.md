# PROGRESS — m3diff

Living status file. Update at the end of every working session so a fresh
session can rehydrate with: "read CLAUDE.md and PROGRESS.md, run the tests,
and tell me where things stand."

Last updated: 2026-07-04, by Watson (Claude Code session)

---

## Current status (one-liner)

**Diff now runs table-parallel across processes** (ADR-013), serial-identical
output, with in-process retry so one flaky worker can't sink a long run.
Validated schema-keyed on real data: intra 100 vs 500, 11 metadata-keyed
masters, **19.4s → 5.5s at 6 workers (~3.5×)**, byte-identical to serial. Engine
now **126 tests**. Desktop shell still built (Tauri v2 + React, ADR-012); only
packaging (Phase 7) and a live `tauri dev` smoke remain.

## Next up (the 1–3 things to do next)

- [ ] Phase 7 packaging on the shipping machine (confirm which machine):
      PyInstaller sidecar + NSIS/WebView2. Also: run `npm run tauri dev` to
      click through the live UI; MF-category scope preset (ADR-006).
- [ ] Optional: exercise the parallel path at larger scope once hardware is
      trusted — a full-tenant all-tables all-cores sweep is deliberately NOT run
      yet (it is the max-load scenario in the reboot history). Scale up
      gradually (--workers modest, then higher) and watch Event Viewer.
- [ ] (Closed) The MITBAL segfault is treated as **hardware** (flaky i9), per
      owner — not chased further. The parallel path's in-process retry also
      degrades gracefully if a worker dies mid-run (ADR-013).

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

### Phase 5 — CLI ✅ (schema refresh stubbed → 3b)
- [x] `m3diff compare` (intra/inter/global) + scope filter, strict-null, no-mask-cono
- [x] `m3diff classify` (classification CSV + summary)
- [ ] `m3diff schema refresh` — stubbed pending Phase 3b (MDP client)
- [x] CLI and (eventual) GUI produce identical result JSON (byte-identical test)

### Phase 6 — Desktop shell + UI (built; packaging → Phase 7)
- [x] Tauri v2 shell over NDJSON-over-stdio (ADR-001); dev spawns `python -m
      m3diff.cli serve` via PYTHONPATH. PyInstaller **sidecar deferred to Phase 7**
- [x] Upload + per-export summary (native file dialog + classify); drag-drop TBD
- [x] Mode selection + table scope filter — prefix-glob preset for now; MF
      category (ADR-006) is a follow-up (needs a schema-cache category lookup)
- [x] Progress reporting + cancel + per-table error tolerance (F5/F6, UI-wired)
- [x] Results dashboard + table drill-down + row/field drill-down
- [x] Export renderers JSON/CSV/Markdown in engine + CLI `--format`; UI has
      "Copy result JSON" (CSV/MD save buttons TBD)
- [x] Settings: `.ionapi` path, schema refresh, ignore-fields, null/mask toggles;
      retention + `.ionapi` at-rest storage (ADR-009) TBD
- [ ] Results history (reopen without reprocessing)
- [ ] Live `tauri dev` RPC smoke (opens a window — owner to run locally)

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
- **All committed, nothing lost.** Engine (126 tests) + desktop shell both build.
- **Diff is now table-parallel (ADR-013).** CLI `--workers` (0=auto default in
  the CLI, 1=serial, N=force). Proven byte-identical to serial on real data and
  ~3.5× at 6 workers on a scoped masters run. In-process retry means a flaky
  worker (or a dead one) is re-run locally instead of aborting the whole compare.
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
