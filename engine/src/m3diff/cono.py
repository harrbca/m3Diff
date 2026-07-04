"""Company-number (CONO) rules — centralized because it is, per CLAUDE.md, the
single easiest thing to get subtly wrong.

Two rules matter and are applied consistently everywhere CONO is read:

- **Absent or blank => tenant-global (CONO 0).** A CONO column missing from a
  row's bitmap, or present but blank, denotes a tenant-wide row (spec §2.1).
- **Leading zeros are not normalized.** We match the verified reference: only
  absent/blank collapses to "0"; "100" stays "100". Real exports store global
  rows as absent/blank and real companies as plain numbers, so this is exact.
"""
from __future__ import annotations

from .format.types import Row

GLOBAL_CONO = "0"


def normalize_cono(value: str | None) -> str:
    """Map a raw CONO cell to its company number; absent/blank => "0"."""
    if value is None:
        return GLOBAL_CONO
    trimmed = value.strip()
    return trimmed if trimmed else GLOBAL_CONO


def cono_of_row(row: Row, cono_field: str | None) -> str:
    """The (normalized) CONO of a decoded row.

    ``cono_field`` is the company column's name, or None for a NO_CONO table —
    whose rows are all tenant-global by schema.
    """
    if cono_field is None:
        return GLOBAL_CONO
    return normalize_cono(row.get(cono_field))
