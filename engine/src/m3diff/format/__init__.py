"""Binary M3 export format: a streaming table reader and TABLE_INFO deserializer."""
from __future__ import annotations

from .reader import iter_cono_values, iter_rows, read_header, read_table
from .tableinfo import TableInfoEntry, TableInfoError, parse_table_info
from .types import (
    CompressionError,
    ExportFormatError,
    Field,
    HeaderError,
    Row,
    RowLengthError,
    TableHeader,
    TruncatedExportError,
)

__all__ = [
    "Field",
    "TableHeader",
    "Row",
    "ExportFormatError",
    "HeaderError",
    "TruncatedExportError",
    "RowLengthError",
    "CompressionError",
    "read_header",
    "iter_rows",
    "iter_cono_values",
    "read_table",
    "TableInfoEntry",
    "TableInfoError",
    "parse_table_info",
]
