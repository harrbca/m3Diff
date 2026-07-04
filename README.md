# m3diff

A desktop tool for comparing Infor M3 table exports across tenants and
companies — surfacing configuration drift between companies and the tenant-wide
data that company copies and tenant-to-tenant migrations silently miss.

> **Status:** early development. The design is settled; implementation is
> underway. See [`PLAN.md`](PLAN.md) for the build order and
> [`DECISIONS.md`](DECISIONS.md) for the architecture-decision log.

## Why

M3 tenants hold multiple companies (CONO). Most data is company-scoped, but some
tables are tenant-wide — rows stored at CONO 0, or tables with no company column
at all. Company copies and tenant-to-tenant migrations miss the tenant-wide
data, and companies drift from their master over time. m3diff makes those gaps
visible.

## Layout

- **`engine/`** — the diff engine: a pure Python library with a CLI (`m3diff`).
  The desktop shell is a thin wrapper over it; both produce identical result
  JSON, so the tool is scriptable and testable without the GUI.
- **`desktop/`** — the desktop shell (Tauri + React) over the engine's RPC
  server. Runs in dev; Windows-installer packaging is a final step. See
  [`desktop/README.md`](desktop/README.md).
- **`reference/`** — verified prototype scripts documenting the binary export
  format; the authoritative reference for the reader.
- [`SPEC-m3diff.md`](SPEC-m3diff.md), [`PLAN.md`](PLAN.md),
  [`DECISIONS.md`](DECISIONS.md) — specification, implementation plan, and
  decision log.

## License

MIT (placeholder — see [`LICENSE`](LICENSE)).
