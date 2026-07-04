# m3diff engine

The m3diff diff engine — a pure Python library (3.11+, stdlib-first) with a CLI.
It builds, tests, and ships independently of the desktop shell, and never
imports up into it: the shell bundles the engine as a sidecar, not the reverse.

```sh
pip install -e .[dev]
pytest
```

See the repository-root [`PLAN.md`](../PLAN.md) and [`DECISIONS.md`](../DECISIONS.md)
for design and rationale.
