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


def test_render_round_trips_through_the_cli_renderers(tmp_path):
    """render returns exactly what the CLI --format would write for the same result."""
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "OLD"}])}))
    b.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": "NEW"}])}))
    req = json.dumps({
        "id": 10, "method": "compare",
        "params": {"mode": "inter", "a": str(a), "b": str(b), "cono_a": "100", "cono_b": "100",
                   "generated_at": "t"},
    })
    msgs = _run([req])
    result_dict = [m for m in msgs if m.get("type") == "result"][0]["result"]

    for fmt, marker in (("json", '"tool_version"'), ("csv", "table,"), ("md", "# m3diff")):
        render_req = json.dumps({"id": 11, "method": "render",
                                 "params": {"result": result_dict, "format": fmt}})
        out = [m for m in _run([render_req]) if m.get("type") == "result"][0]["result"]
        assert out["format"] == fmt
        assert marker in out["content"]

    # json render == canonical to_json of the same result (byte-identical path)
    from m3diff.contract import from_dict, to_json

    render_req = json.dumps({"id": 12, "method": "render",
                             "params": {"result": result_dict, "format": "json"}})
    content = [m for m in _run([render_req]) if m.get("type") == "result"][0]["result"]["content"]
    assert content == to_json(from_dict(result_dict))


def test_render_unknown_format_errors():
    msgs = _run([json.dumps({"id": 13, "method": "render",
                             "params": {"result": {}, "format": "xml"}})])
    err = [m for m in msgs if m.get("type") == "error"][0]
    assert "unknown format" in err["error"]["message"]


def test_serve_pipes_survive_non_ascii_data(tmp_path):
    """Regression for the field failure: an MF compare died with «'charmap'
    codec can't encode characters» because Windows pipes default to cp1252 and
    result JSON is written ensure_ascii=False. serve() must pin its stdio to
    UTF-8 regardless of how the parent spawned it."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import m3diff

    desc_a = "Größe 10µm 高强度™"
    desc_b = "Größe 12µm 高强度™"
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    a.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": desc_a}])}))
    b.write_bytes(build_export_zip({"MITMAS": (_MM, [{"mmcono": "100", "mmitno": "A", "mmitds": desc_b}])}))

    # Strip any UTF-8 overrides so the child gets the hostile cp1252 default.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONPATH"] = str(Path(m3diff.__file__).parents[1])

    req = json.dumps({
        "id": 1, "method": "compare",
        "params": {"mode": "inter", "a": str(a), "b": str(b), "cono_a": "100", "cono_b": "100",
                   "generated_at": "t"},
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "m3diff.cli", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
    )
    out, _ = proc.communicate((req + "\n").encode("utf-8"), timeout=60)

    frames = [json.loads(line) for line in out.decode("utf-8").splitlines() if line.strip()]
    errors = [f for f in frames if f.get("type") == "error"]
    assert not errors, f"serve errored: {errors[0]['error']['message']}"
    results = [f for f in frames if f.get("type") == "result"]
    assert results, "no result frame from serve"
    # No schema cache => heuristic PK => the changed row reads as remove+add.
    td = results[0]["result"]["tables"]["MITMAS"]
    assert td["removed"][0]["row"]["mmitds"] == desc_a  # values intact through the pipe
    assert td["added"][0]["row"]["mmitds"] == desc_b
