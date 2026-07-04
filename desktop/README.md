# m3diff desktop

A Tauri (v2) + React desktop shell over the m3diff engine. The shell is thin: it
spawns the Python engine as an NDJSON-over-stdio subprocess (`m3diff serve`) and
renders the results. All comparison logic lives in `../engine`.

## Prerequisites

- **Node** (18+) and **npm**
- **Rust** (stable, MSVC toolchain on Windows) — install via <https://rustup.rs>
- **Python** 3.11+ on `PATH` (for the backend). No pip install needed — the
  engine is stdlib-only and is run from source via `PYTHONPATH`.

## Run (dev)

```sh
npm install
npm run tauri dev
```

The Rust shell spawns the backend as `python -m m3diff.cli serve` with
`PYTHONPATH` pointed at `../engine/src` (resolved from `CARGO_MANIFEST_DIR`).
Override with env vars if needed:

- `M3DIFF_PYTHON` — python executable (default `python`)
- `M3DIFF_ENGINE_SRC` — path to `engine/src`

The frontend talks to the backend through the Rust `rpc_send` command and the
`rpc://message` event (see `src/rpc.ts` ↔ `src-tauri/src/lib.rs` ↔
`engine/src/m3diff/rpc.py`).

## Architecture

- `src/` — React UI: `rpc.ts` (NDJSON client), `types.ts` (contract mirror),
  and five views (`Upload`, `Scope`, `Results`, `Drilldown`, `Settings`).
- `src-tauri/` — the Rust shell: spawns the backend, pumps stdio, exposes
  `rpc_send`.

## Packaging (deferred — Phase 7)

Windows installer (NSIS + embedded WebView2 bootstrapper) and bundling the
engine as a PyInstaller **sidecar** (`externalBin`) are a separate final step,
done on the designated release machine. Dev does not need them.
