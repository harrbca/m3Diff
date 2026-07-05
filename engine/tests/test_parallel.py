"""Parallel diff: the worker-count gate, and byte-identical output vs serial.

The correctness guarantee is that ``workers>1`` must produce exactly the same
result JSON as the serial path (CLAUDE.md: CLI and GUI emit identical JSON). The
end-to-end tests below spawn real worker processes on-disk exports and diff the
serialized results against a serial run.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fixtures.builder import build_export_zip, field

from m3diff.contract import to_json
from m3diff.diff import (
    CompareCancelled,
    CompareOptions,
    _resolve_future,
    _resolve_workers,
    compare,
)
from m3diff.schema import Column, SchemaCache, TableSchema
from m3diff.source import ZipExportSource, open_export

_MM = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]


def _rows(pairs):
    return [{"mmcono": "100", "mmitno": itno, "mmitds": desc} for itno, desc in pairs]


def _side_a():
    return {
        "CIDMAS": (_MM, _rows([("F", "M")])),                 # identical
        "DUPKEY": (_MM, _rows([("K", "V1"), ("K", "V2")])),   # degenerate metadata PK
        "MITMAS": (_MM, _rows([("A", "W"), ("B", "X")])),     # identical
        "MPDMAT": (_MM, _rows([("D", "Z"), ("E", "Q")])),     # E removed in B
        "OCUSMA": (_MM, _rows([("C", "Y")])),                 # modified in B
    }


def _side_b():
    return {
        "CIDMAS": (_MM, _rows([("F", "M"), ("G", "NEW")])),   # G added
        "DUPKEY": (_MM, _rows([("K", "V1"), ("K", "V3")])),   # degenerate + drifted
        "MITMAS": (_MM, _rows([("A", "W"), ("B", "X")])),     # identical
        "MPDMAT": (_MM, _rows([("D", "Z")])),                 # E removed
        "OCUSMA": (_MM, _rows([("C", "CHANGED")])),           # modified
    }


def _write_zip(path, tables):
    path.write_bytes(build_export_zip(tables))
    return str(path)


def _write_schema_db(path, table_names):
    with SchemaCache(path) as cache:
        cols = tuple(
            Column(name, "String", None, None, "", ("00",) if name in ("MMCONO", "MMITNO") else ())
            for name in ("MMCONO", "MMITNO", "MMITDS")
        )
        for name in table_names:
            cache.upsert_table(TableSchema("MVX", name, "MF", name, cols, "2026-07-04"))
    return str(path)


def _compare(a_path, b_path, db, workers):
    """Fresh sources + cache per run so serial and parallel don't share handles."""
    cache = SchemaCache(db) if db else None
    try:
        return compare(
            open_export(a_path),
            open_export(b_path),
            CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=cache, workers=workers),
            tool_version="0.1.0",
            generated_at="2026-07-04T00:00:00Z",
            a_label="a.zip",
            b_label="b.zip",
        )
    finally:
        if cache is not None:
            cache.close()


# --- the gate ---------------------------------------------------------------
def _memory_src(tables):
    return ZipExportSource(io.BytesIO(build_export_zip(tables)))


def test_gate_serial_when_workers_is_one(tmp_path):
    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=1)
    assert _resolve_workers(opt, 4, a, b) == 1


def test_gate_parallel_for_disk_sources_on_auto(tmp_path, monkeypatch):
    import m3diff.diff as diffmod

    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    monkeypatch.setattr(diffmod.os, "cpu_count", lambda: 4)
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=0)
    assert _resolve_workers(opt, 4, a, b) == 4  # min(cpu, tables)


def test_gate_serial_below_min_tables_on_auto(tmp_path, monkeypatch):
    import m3diff.diff as diffmod

    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    monkeypatch.setattr(diffmod.os, "cpu_count", lambda: 8)
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=0)
    assert _resolve_workers(opt, 3, a, b) == 1  # 3 < _MIN_PARALLEL_TABLES


def test_gate_serial_for_memory_sources_even_when_forced():
    a = _memory_src(_side_a())
    b = _memory_src(_side_b())
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=8)
    assert _resolve_workers(opt, 4, a, b) == 1  # not re-openable


def test_gate_serial_for_in_memory_cache_even_when_forced(tmp_path):
    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=SchemaCache(), workers=8)
    assert _resolve_workers(opt, 4, a, b) == 1  # ":memory:" cache can't be shared


def test_gate_explicit_override_honored_down_to_two_tables(tmp_path):
    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=2)
    assert _resolve_workers(opt, 2, a, b) == 2


# --- end to end: parallel == serial -----------------------------------------
def test_parallel_matches_serial_with_metadata_pk(tmp_path):
    a = _write_zip(tmp_path / "a.zip", _side_a())
    b = _write_zip(tmp_path / "b.zip", _side_b())
    db = _write_schema_db(tmp_path / "schema.db", ("CIDMAS", "DUPKEY", "MITMAS", "MPDMAT", "OCUSMA"))

    serial_result = _compare(a, b, db, workers=1)
    serial = to_json(serial_result)
    parallel = to_json(_compare(a, b, db, workers=2))
    assert parallel == serial  # byte-identical

    # ...and it is a real diff, not two empty runs that trivially match.
    assert '"status": "modified"' in serial
    assert '"CHANGED"' in serial
    # the degenerate-PK fallback fired identically inside a worker process
    assert serial_result.tables["DUPKEY"].pk_degenerate is True


def test_parallel_matches_serial_with_heuristic_pk(tmp_path):
    a = _write_zip(tmp_path / "a.zip", _side_a())
    b = _write_zip(tmp_path / "b.zip", _side_b())
    serial = to_json(_compare(a, b, None, workers=1))
    parallel = to_json(_compare(a, b, None, workers=2))
    assert parallel == serial


def test_parallel_tolerates_a_corrupt_table(tmp_path):
    """A parse error in one table becomes an error row in both serial and parallel."""
    a_path = tmp_path / "a.zip"
    a_path.write_bytes(build_export_zip(_side_a()))
    with zipfile.ZipFile(a_path, "a") as zf:
        zf.writestr("BROKEN", b"\x00\x00\x00\x05short")  # header claims 5 bytes: undecodable
    b_path = tmp_path / "b.zip"
    b_tables = dict(_side_b())
    b_tables["BROKEN"] = (_MM, _rows([("Z", "z")]))
    b_path.write_bytes(build_export_zip(b_tables))
    db = _write_schema_db(tmp_path / "schema.db", ("CIDMAS", "MITMAS", "MPDMAT", "OCUSMA", "BROKEN"))

    serial = _compare(str(a_path), str(b_path), db, workers=1)
    parallel = _compare(str(a_path), str(b_path), db, workers=2)
    assert parallel.tables["BROKEN"].status == "error"
    assert to_json(parallel) == to_json(serial)


# --- resilience: a glitched worker is re-run in-process -----------------------
class _OkFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _BrokenFuture:
    def result(self):
        raise RuntimeError("simulated worker glitch / dead process")


def test_resolve_future_passes_through_a_good_result(tmp_path):
    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    sentinel = object()
    got = _resolve_future(_OkFuture(sentinel), "MITMAS", a, b, set(), set(), CompareOptions(mode="inter"))
    assert got is sentinel  # a healthy worker result is used as-is


def test_resolve_future_reruns_failed_table_in_process(tmp_path):
    """A glitched worker future is recovered by re-diffing that table locally."""
    a = open_export(_write_zip(tmp_path / "a.zip", _side_a()))
    b = open_export(_write_zip(tmp_path / "b.zip", _side_b()))
    a_names, b_names = set(a.table_names()), set(b.table_names())
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100")
    recovered = _resolve_future(_BrokenFuture(), "OCUSMA", a, b, a_names, b_names, opt)
    # identical to a direct in-process diff of that one table
    from m3diff.diff import _diff_dispatch

    expected = _diff_dispatch("OCUSMA", a, b, a_names, b_names, opt)
    assert recovered is not None
    assert to_json_of(recovered) == to_json_of(expected)


def to_json_of(table_diff):
    import dataclasses

    return dataclasses.asdict(table_diff)


def test_parallel_honors_cancellation(tmp_path):
    a = _write_zip(tmp_path / "a.zip", _side_a())
    b = _write_zip(tmp_path / "b.zip", _side_b())
    with pytest.raises(CompareCancelled):
        compare(
            open_export(a),
            open_export(b),
            CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=2),
            cancelled=lambda: True,
        )


# --- pool liveness canary (wedged-spawn environments) --------------------------
def test_canary_timeout_falls_back_to_serial_and_sticks(tmp_path, monkeypatch):
    """Grace 0 simulates a pool that never comes up: the compare must still
    produce the full (byte-identical) result via the serial path, and the
    process remembers the wedge so later compares skip the pool entirely."""
    import m3diff.diff as diffmod

    monkeypatch.setattr(diffmod, "_CANARY_GRACE", 0.0)
    monkeypatch.setattr(diffmod, "_pool_unavailable", False)  # restored after test
    a = _write_zip(tmp_path / "a.zip", _side_a())
    b = _write_zip(tmp_path / "b.zip", _side_b())
    db = _write_schema_db(tmp_path / "schema.db", ("CIDMAS", "DUPKEY", "MITMAS", "MPDMAT", "OCUSMA"))

    seen: list[tuple[int, int, str]] = []
    cache = SchemaCache(db)
    try:
        result = compare(
            open_export(a), open_export(b),
            CompareOptions(mode="inter", cono_a="100", cono_b="100", cache=cache, workers=2),
            tool_version="0.1.0", generated_at="2026-07-04T00:00:00Z",
            a_label="a.zip", b_label="b.zip",
            progress=lambda d, t, n: seen.append((d, t, n)),
        )
    finally:
        cache.close()
    assert to_json(result) == to_json(_compare(a, b, db, workers=1))  # fell back, identical
    assert len(seen) == result.summary.tables_compared  # serial progress still emitted
    assert diffmod._pool_unavailable is True  # sticky: no pool retry this process

    # ...and the sticky flag short-circuits worker resolution immediately.
    opt = CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=8)
    assert _resolve_workers(opt, 10, open_export(a), open_export(b)) == 1


def test_cancel_during_worker_startup_is_prompt(tmp_path):
    """Cancellation must interrupt the canary wait, not sit out the grace period."""
    import time as _time

    a = _write_zip(tmp_path / "a.zip", _side_a())
    b = _write_zip(tmp_path / "b.zip", _side_b())
    start = _time.monotonic()
    with pytest.raises(CompareCancelled):
        compare(
            open_export(a), open_export(b),
            CompareOptions(mode="inter", cono_a="100", cono_b="100", workers=2),
            cancelled=lambda: True,
        )
    assert _time.monotonic() - start < 10  # well under the 15s grace
