# CLAUDE.md — m3diff

Working notes for Claude Code on this project. Read `SPEC-m3diff.md` for the
full specification; this file covers conventions and how to work here.

## What this is

A desktop tool for comparing Infor M3 table exports across tenants and
companies. Personal open-source project (working title `m3diff`). See the spec
for functional detail.

## Ground rules

- **No employer references anywhere.** No company names, tenant IDs,
  hostnames, internal file paths, usernames, or real business data in code,
  comments, tests, fixtures, commit messages, or docs. This is a clean
  personal project intended to be publishable.
- **No real M3 data in the repo.** Test fixtures are generated
  programmatically (the binary format is fully specified in the spec §2).
  Any real exports used for local smoke-testing live in `fixtures/real/`,
  which is git-ignored, and are never committed.
- **`.ionapi` files are secrets.** Never commit them, never hardcode
  credentials, never log their contents. They are supplied by the user at
  runtime via Settings.

## How to work

- The `reference/` folder holds three prototype scripts (`classify_export.py`,
  `parse_export.py`, `parse_tableinfo.py`). They are the **authoritative,
  verified implementation of the binary export format**. Port their logic into
  the real package with tests; do not shell out to them and do not re-derive
  the format from scratch — but do improve on their structure.
- **Diff engine is a pure library with a CLI.** The GUI is a thin shell over
  it. CLI and GUI must produce identical result JSON for identical inputs.
  This keeps the tool scriptable and testable without the desktop shell.
- **Stdlib-first for Python.** Reach for third-party deps only with a reason.
  Target Python 3.11+.
- **Stream, don't slurp.** Real exports reach ~2M rows / ~700 tables. Never
  load a whole export into memory; index only in-scope tables. Honor the
  performance and memory targets in spec §6.2.
- **Preserve the format invariants** called out in spec §2, especially:
  absent-from-bitmap CONO ⇒ CONO 0 ⇒ global row; the per-row length invariant
  (treat it as a checksum and assert it); null vs empty-string are distinct on
  the wire.
- **Mask CONO in primary keys** when comparing across companies — row
  (500, ITEM001) must match (100, ITEM001). This is the single easiest thing
  to get subtly wrong; cover it with a golden test.

## Decisions delegated to you (surface your reasoning, don't guess silently)

- Tauri vs Electron for the shell (spec leans Tauri; spike if unsure).
- Metadata Publisher PK fetch: bulk vs lazy-per-table.
- Anything in spec §7 (open questions) — raise these with me rather than
  quietly picking an answer.

## Out of scope for v1

- The "Analyze with AI" feature. v1 only needs the diff JSON to be complete
  and self-describing enough to support it later. Do not build it yet.
- DIVI (division) masking beyond a noted config toggle — v1 masks CONO only.

## Workflow

- Propose an implementation plan and directory structure before writing a
  large volume of code; let me confirm the shape first.
- Write tests alongside features, not after. Golden tests for the diff JSON
  are the backbone — see spec §6.3 for the required cases.
- Commit in logical, reviewable chunks with clear messages.
