"""The ``m3diff`` command-line interface (spec §4.6).

A thin shell over the same engine the GUI uses, so both emit identical result
JSON. Subcommands:

    m3diff compare --mode {intra|inter|global} --a A [--b B] [--cono-a ..] [--cono-b ..]
                   [--tables "CSY*,MITMAS"] [--schema-db DB] [--out result.json]
    m3diff classify EXPORT [--out classification.csv]
    m3diff schema refresh --ionapi FILE [--schema-db DB]   (Phase 3b)
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from . import __version__
from .classify import classify_export
from .contract import to_json
from .diff import CompareOptions, compare
from .report import to_markdown, to_summary_csv
from .schema.cache import SchemaCache
from .source import open_export


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_tables(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    return names or None


def _write_text(out: str | None, text: str) -> None:
    if out is None or out == "-":
        sys.stdout.write(text)
    else:
        Path(out).write_text(text, encoding="utf-8", newline="")


def _cmd_compare(args: argparse.Namespace) -> int:
    if args.mode in ("inter", "global") and not args.b:
        print(f"error: --b is required for --mode {args.mode}", file=sys.stderr)
        return 2
    if args.mode in ("intra", "inter") and (not args.cono_a or not args.cono_b):
        print(f"error: --cono-a and --cono-b are required for --mode {args.mode}", file=sys.stderr)
        return 2

    cache = SchemaCache(args.schema_db) if args.schema_db else None
    try:
        a = open_export(args.a)
        b = open_export(args.b) if args.b else None
        options = CompareOptions(
            mode=args.mode,
            cono_a=args.cono_a,
            cono_b=args.cono_b,
            tables=_parse_tables(args.tables),
            categories=_parse_tables(args.category),
            null_equals_empty=not args.strict_null,
            mask_cono=not args.no_mask_cono,
            cache=cache,
            workers=args.workers,
        )
        result = compare(
            a,
            b,
            options,
            tool_version=__version__,
            generated_at=args.generated_at or _now_iso(),
            a_label=Path(args.a).name,
            # intra mode compares two companies in one export, so B is that same file.
            b_label=Path(args.b).name if args.b else Path(args.a).name,
        )
        renderers = {"json": to_json, "csv": to_summary_csv, "md": to_markdown}
        _write_text(args.out, renderers[args.format](result))
    finally:
        if cache is not None:
            cache.close()
    return 0


_CLASSIFY_COLUMNS = (
    "table", "class", "rows", "rows_global", "conos", "cono_field", "cono_ambiguous", "fields", "error",
)


def _cmd_classify(args: argparse.Namespace) -> int:
    with open_export(args.export) as source:
        results = classify_export(source)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CLASSIFY_COLUMNS)
    for r in sorted(results, key=lambda r: (r.cls, r.table)):
        writer.writerow(
            [
                r.table,
                r.cls,
                r.rows,
                r.rows_global,
                " ".join(r.conos),
                r.cono_field or "",
                "yes" if r.cono_ambiguous else "",
                r.fields,
                r.error or "",
            ]
        )
    _write_text(args.out, buffer.getvalue())

    from collections import Counter

    summary = Counter(r.cls for r in results)
    print(f"{len(results)} tables classified", file=sys.stderr)
    for cls, count in summary.most_common():
        print(f"  {cls:<12} {count}", file=sys.stderr)
    return 0


def _default_schema_db() -> str:
    root = os.environ.get("APPDATA")
    base = Path(root) / "m3diff" if root else Path.home() / ".m3diff"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "schema.db")


def _cmd_schema_refresh(args: argparse.Namespace) -> int:
    from .schema.ionapi import load_ionapi
    from .schema.publisher import (
        MetadataPublisherClient, PublisherError, httpx_client, refresh_schema, refresh_table_info,
    )

    db_path = args.schema_db or _default_schema_db()
    try:
        credentials = load_ionapi(args.ionapi)
        client = MetadataPublisherClient.from_ionapi(credentials, httpx_client())
    except PublisherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def _progress(done: int, total: int, name: str) -> None:
        print(f"\r[{done}/{total}] {name:<30}", end="", file=sys.stderr)

    with SchemaCache(db_path) as cache:
        if args.info_only:
            total = refresh_table_info(client, cache, progress=_progress)
            print(f"\nupdated table info (category/maintained-by) for {total} tables in {db_path}",
                  file=sys.stderr)
        else:
            total = refresh_schema(client, cache, fetched_at=_now_iso(), progress=_progress)
            print(f"\nrefreshed {total} tables into {db_path}", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .rpc import serve

    return serve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="m3diff", description="Compare Infor M3 table exports.")
    parser.add_argument("--version", action="version", version=f"m3diff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_compare = sub.add_parser("compare", help="Compare two exports (or two companies).")
    p_compare.add_argument("--mode", required=True, choices=("intra", "inter", "global"))
    p_compare.add_argument("--a", required=True, help="Export zip or directory (side A).")
    p_compare.add_argument("--b", help="Export zip or directory (side B); required for inter/global.")
    p_compare.add_argument("--cono-a", dest="cono_a", help="Company on side A.")
    p_compare.add_argument("--cono-b", dest="cono_b", help="Company on side B.")
    p_compare.add_argument("--tables", help='Scope filter, e.g. "CSY*,MITMAS,OCUSMA".')
    p_compare.add_argument("--category", help='Scope to metadata categories, e.g. "MF" or '
                           '"MF,TF" (master/transaction/work/stats). Needs --schema-db; '
                           "unions with --tables.")
    p_compare.add_argument("--schema-db", dest="schema_db", help="SQLite schema cache for PKs.")
    p_compare.add_argument("--out", help="Output file (default: stdout).")
    p_compare.add_argument("--format", choices=("json", "csv", "md"), default="json",
                           help="Output format: json (default), csv summary, or md report.")
    p_compare.add_argument("--strict-null", dest="strict_null", action="store_true",
                           help="Treat null and empty string as different.")
    p_compare.add_argument("--no-mask-cono", dest="no_mask_cono", action="store_true",
                           help="Do not mask the CONO column in the key.")
    p_compare.add_argument("--workers", type=int, default=0,
                           help="Diff worker processes: 0 = auto/all cores (default), "
                                "1 = serial, N = force N. Parallel needs file/dir inputs.")
    p_compare.add_argument("--generated-at", dest="generated_at",
                           help="ISO timestamp to stamp (default: now); set for reproducible output.")
    p_compare.set_defaults(func=_cmd_compare)

    p_classify = sub.add_parser("classify", help="Classify an export's tables.")
    p_classify.add_argument("export", help="Export zip or directory.")
    p_classify.add_argument("--out", help="Output CSV file (default: stdout).")
    p_classify.set_defaults(func=_cmd_classify)

    p_serve = sub.add_parser("serve", help="Run the JSON-over-stdio RPC server (for the GUI).")
    p_serve.set_defaults(func=_cmd_serve)

    p_schema = sub.add_parser("schema", help="Schema cache operations.")
    schema_sub = p_schema.add_subparsers(dest="schema_command", required=True)
    p_refresh = schema_sub.add_parser("refresh", help="Refresh the schema cache from M3.")
    p_refresh.add_argument("--ionapi", required=True, help="Path to the .ionapi credential file.")
    p_refresh.add_argument("--schema-db", dest="schema_db", help="SQLite schema cache to write.")
    p_refresh.add_argument("--info-only", dest="info_only", action="store_true",
                           help="Only update category/description/maintained-by from the table "
                                "list (one call) — no per-table column re-fetch.")
    p_refresh.set_defaults(func=_cmd_schema_refresh)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
