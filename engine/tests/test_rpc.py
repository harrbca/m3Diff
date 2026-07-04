"""Tests for the NDJSON-over-stdio RPC server (spec F5/F6)."""
from __future__ import annotations

import io
import json
import threading

from fixtures.builder import build_export_zip, field

from m3diff import __version__
from m3diff.rpc import RpcServer

_MM = [field("mmcono", "4"), field("mmitno", maxlen="15"), field("mmitds", maxlen="30")]


def _run(lines):
    inp = io.StringIO("\n".join(lines) + "\n")
    out = io.StringIO()
    RpcServer(out).run(inp)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_ping():
    msgs = _run(['{"id": 1, "method": "ping"}'])
    assert msgs == [{"id": 1, "type": "result", "result": {"pong": True, "version": __version__}}]


def test_unknown_method_errors():
    msgs = _run(['{"id": 2, "method": "frobnicate"}'])
    assert msgs[0]["type"] == "error"
    assert "unknown method" in msgs[0]["error"]["message"]


def test_invalid_json_errors_without_crashing():
    msgs = _run(["this is not json", '{"id": 3, "method": "ping"}'])
    assert msgs[0]["type"] == "error" and "invalid JSON" in msgs[0]["error"]["message"]
    assert msgs[1]["result"]["pong"] is True  # server kept going


def test_compare_emits_progress_then_result(tmp_path):
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}))
    b.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}))
    req = json.dumps({
        "id": 5, "method": "compare",
        "params": {"mode": "inter", "a": str(a), "b": str(b), "cono_a": "100", "cono_b": "100",
                   "generated_at": "t"},
    })
    msgs = _run([req])
    progress = [m for m in msgs if m.get("type") == "progress"]
    result = [m for m in msgs if m.get("type") == "result"][0]
    assert progress and progress[0]["progress"] == {"done": 1, "total": 1, "table": "MITMAS"}
    assert result["result"]["mode"] == "inter"
    assert result["result"]["tables"]["MITMAS"]["status"] == "modified"


def test_classify_over_rpc(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(build_export_zip({"MITMAS": ([field("mmcono", "4"), field("mmitno", maxlen="15")],
                                               [{"mmcono": "100", "mmitno": "A"}])}))
    msgs = _run([json.dumps({"id": 6, "method": "classify", "params": {"export": str(z)}})])
    result = [m for m in msgs if m.get("type") == "result"][0]
    tables = result["result"]["tables"]
    assert any(t["table"] == "MITMAS" and t["class"] == "COMPANY" for t in tables)


def test_task_failure_is_reported_not_fatal(tmp_path):
    # A missing export path makes the task fail; the server should report an error frame.
    req = json.dumps({
        "id": 7, "method": "compare",
        "params": {"mode": "inter", "a": str(tmp_path / "nope.zip"), "b": str(tmp_path / "nope.zip"),
                   "cono_a": "1", "cono_b": "1"},
    })
    msgs = _run([req])
    assert [m for m in msgs if m.get("id") == 7][0]["type"] == "error"


def test_cancel_unknown_target():
    msgs = _run(['{"id": 9, "method": "cancel", "params": {"target_id": 999}}'])
    assert msgs[0]["result"] == {"cancelled": False, "target_id": 999}


def test_cancel_sets_the_task_event():
    # Deterministic unit test of the cancel path (no thread race).
    out = io.StringIO()
    server = RpcServer(out)
    event = threading.Event()
    server._cancels[42] = event
    server._dispatch({"id": 10, "method": "cancel", "params": {"target_id": 42}})
    assert event.is_set()
    assert json.loads(out.getvalue().splitlines()[0])["result"] == {"cancelled": True, "target_id": 42}
