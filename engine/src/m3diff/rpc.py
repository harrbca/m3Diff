"""NDJSON-over-stdio RPC server for the desktop shell (ADR-001, spec §3, F5/F6).

One JSON object per line, both directions. Requests carry an ``id``; the server
replies with matching-``id`` frames:

    request   {"id": 1, "method": "compare", "params": {...}}
    progress  {"id": 1, "type": "progress", "progress": {"done": n, "total": m, "table": "..."}}
    result    {"id": 1, "type": "result", "result": {...}}
    error     {"id": 1, "type": "error", "error": {"message": "..."}}
    cancelled {"id": 1, "type": "cancelled", "result": {"cancelled": true}}

Long tasks (compare / classify / schema_refresh / render) run on worker threads
so a ``cancel`` request can be read and honored while one is in flight. stdout
writes are serialized behind a lock. Methods: ping, compare, classify,
schema_refresh (``info_only`` for the cheap table-info update), render (result
dict → json/csv/md via the CLI's renderers), cancel.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, TextIO

from . import __version__
from .classify import TableClassification, classify_export
from .contract import from_dict, to_dict, to_json
from .diff import DEFAULT_IGNORED_FIELDS, CompareCancelled, CompareOptions, compare
from .report import to_markdown, to_summary_csv
from .schema.cache import SchemaCache
from .source import open_export

_LONG_METHODS = ("compare", "classify", "schema_refresh", "render")


def _classification_to_dict(c: TableClassification) -> dict[str, Any]:
    return {
        "table": c.table,
        "class": c.cls,
        "fields": c.fields,
        "cono_field": c.cono_field,
        "cono_ambiguous": c.cono_ambiguous,
        "rows": c.rows,
        "rows_global": c.rows_global,
        "conos": list(c.conos),
        "error": c.error,
    }


class RpcServer:
    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._write_lock = threading.Lock()
        self._cancels: dict[Any, threading.Event] = {}
        self._cancels_lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # --- transport ----------------------------------------------------------
    def _send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False)
        with self._write_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def _progress(self, rid: Any) -> Any:
        def emit(done: int, total: int, table: str) -> None:
            self._send({"id": rid, "type": "progress", "progress": {"done": done, "total": total, "table": table}})

        return emit

    def run(self, inp: TextIO) -> None:
        for raw in inp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                request = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._send({"id": None, "type": "error", "error": {"message": f"invalid JSON: {exc}"}})
                continue
            self._dispatch(request)
        for thread in list(self._threads):
            thread.join(timeout=5)

    # --- dispatch -----------------------------------------------------------
    def _dispatch(self, request: dict[str, Any]) -> None:
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "ping":
            self._send({"id": rid, "type": "result", "result": {"pong": True, "version": __version__}})
        elif method == "cancel":
            self._cancel(rid, params)
        elif method in _LONG_METHODS:
            self._start_task(rid, method, params)
        else:
            self._send({"id": rid, "type": "error", "error": {"message": f"unknown method: {method!r}"}})

    def _cancel(self, rid: Any, params: dict[str, Any]) -> None:
        target = params.get("target_id")
        with self._cancels_lock:
            event = self._cancels.get(target)
        if event is not None:
            event.set()
        self._send({"id": rid, "type": "result", "result": {"cancelled": event is not None, "target_id": target}})

    def _start_task(self, rid: Any, method: str, params: dict[str, Any]) -> None:
        cancel_event = threading.Event()
        with self._cancels_lock:
            self._cancels[rid] = cancel_event
        thread = threading.Thread(
            target=self._run_task, args=(rid, method, params, cancel_event), daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _run_task(self, rid: Any, method: str, params: dict[str, Any], cancel: threading.Event) -> None:
        try:
            handler = {
                "compare": self._do_compare,
                "classify": self._do_classify,
                "schema_refresh": self._do_schema_refresh,
                "render": self._do_render,
            }[method]
            result = handler(rid, params, cancel)
            self._send({"id": rid, "type": "result", "result": result})
        except CompareCancelled:
            self._send({"id": rid, "type": "cancelled", "result": {"cancelled": True}})
        except Exception as exc:  # any task failure is reported, never crashes the server
            self._send({"id": rid, "type": "error", "error": {"message": str(exc)}})
        finally:
            with self._cancels_lock:
                self._cancels.pop(rid, None)

    # --- handlers -----------------------------------------------------------
    def _do_compare(self, rid: Any, params: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        cache = SchemaCache(params["schema_db"]) if params.get("schema_db") else None
        try:
            a = open_export(params["a"])
            b = open_export(params["b"]) if params.get("b") else None
            options = CompareOptions(
                mode=params["mode"],
                cono_a=params.get("cono_a"),
                cono_b=params.get("cono_b"),
                tables=tuple(params["tables"]) if params.get("tables") else None,
                categories=tuple(params["categories"]) if params.get("categories") else None,
                ignored_fields=(
                    tuple(params["ignored_fields"])
                    if params.get("ignored_fields")
                    else DEFAULT_IGNORED_FIELDS
                ),
                null_equals_empty=params.get("null_equals_empty", True),
                mask_cono=params.get("mask_cono", True),
                cache=cache,
                workers=params.get("workers", 0),
            )
            result = compare(
                a,
                b,
                options,
                generated_at=params.get("generated_at", ""),
                a_label=Path(params["a"]).name,
                b_label=Path(params["b"]).name if params.get("b") else Path(params["a"]).name,
                progress=self._progress(rid),
                cancelled=cancel.is_set,
            )
            return to_dict(result)
        finally:
            if cache is not None:
                cache.close()

    def _do_classify(self, rid: Any, params: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        with open_export(params["export"]) as source:
            results = classify_export(source, progress=self._progress(rid))
        return {"tables": [_classification_to_dict(c) for c in results]}

    def _do_schema_refresh(self, rid: Any, params: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        from datetime import datetime, timezone

        from .schema.ionapi import load_ionapi
        from .schema.publisher import (
            MetadataPublisherClient, httpx_client, refresh_schema, refresh_table_info,
        )

        credentials = load_ionapi(params["ionapi"])
        client = MetadataPublisherClient.from_ionapi(credentials, httpx_client())
        with SchemaCache(params["schema_db"]) as cache:
            if params.get("info_only"):
                total = refresh_table_info(client, cache, progress=self._progress(rid))
                return {"tables": total, "info_only": True}
            fetched_at = datetime.now(timezone.utc).isoformat()
            total = refresh_schema(
                client, cache, prefix=params.get("prefix"), fetched_at=fetched_at,
                progress=self._progress(rid),
            )
        return {"tables": total}

    def _do_render(self, rid: Any, params: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
        """Render a result dict to json/csv/md — the same renderers the CLI uses,
        so a GUI-saved file is byte-identical to `m3diff compare --format` output."""
        renderers = {"json": to_json, "csv": to_summary_csv, "md": to_markdown}
        fmt = params.get("format", "json")
        if fmt not in renderers:
            raise ValueError(f"unknown format: {fmt!r}")
        result = from_dict(params["result"])
        return {"format": fmt, "content": renderers[fmt](result)}


def _reconfigure_utf8(stream: TextIO) -> None:
    """Force UTF-8 on a real stdio stream (no-op for test doubles).

    On Windows a piped stdin/stdout defaults to the locale codepage (cp1252):
    result JSON with real M3 data (accented descriptions etc., sent with
    ``ensure_ascii=False``) then dies with 'charmap' codec errors, and inbound
    non-ASCII paths would mis-decode. The NDJSON transport is UTF-8 (the shell
    reads/writes UTF-8), so pin both directions.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover - exotic stream; keep serving
            pass


def serve(inp: TextIO | None = None, out: TextIO | None = None) -> int:
    """Run the RPC server over the given streams (default stdin/stdout)."""
    if out is None:
        out = sys.stdout
        _reconfigure_utf8(out)
    if inp is None:
        inp = sys.stdin
        _reconfigure_utf8(inp)
    RpcServer(out).run(inp)
    return 0
