"""Metadata Publisher REST client + schema refresh (ADR-002, ADR-007).

Auth is done in-house: an ION OAuth2 password grant against the token endpoint
in the ``.ionapi``, bearer token cached in memory. Schema fetch is the confirmed
shape (METADATA-PUBLISHER-NOTES.md): ``getTables`` once, then
``getColumnsUsedByTable`` per table — the PK is the columns whose ``indexes``
includes ``00``, in response order; the index-keys endpoint is a fallback only.

The HTTP layer is injectable (``HttpClient``) so this is testable without the
network or a live instance. The httpx-backed client is imported lazily so the
core install stays dependency-free (httpx is the ``[schema]`` extra).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .cache import SchemaCache
from .ionapi import IonApiCredentials
from .models import Column, TableSchema

ProgressFn = Callable[[int, int, str], None]


class PublisherError(Exception):
    """A Metadata Publisher call (auth or fetch) failed."""


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HttpClient(Protocol):
    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> HttpResponse: ...

    def post(self, url: str, *, data: dict | None = None, headers: dict | None = None) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class TableMeta:
    table_name: str
    description: str
    component: str
    category: str
    maintained_by: str = ""  # tableMaintainedBy: maintaining program (may be "")


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def mdp_base_url(cred: IonApiCredentials) -> str:
    """Build the Metadata Publisher base URL from the .ionapi.

    The MDP is served from the ION API *gateway*, not the SSO host used for the
    token. On Infor CloudSuite the gateway host is the authorization host with
    ``mingle-sso`` swapped for ``mingle-ionapi`` (matches the reference
    ``get_auth_base``). Only the host is kept from ``pu`` — the tenant + path are
    appended fresh.
    """
    parsed = urlparse(cred.platform_url)
    host = (parsed.hostname or "").replace("mingle-sso", "mingle-ionapi")
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}/{cred.tenant}/M3/mdprest"


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def _parse_indexes(value: Any) -> tuple[str, ...]:
    return tuple(code.strip() for code in str(value or "").split(",") if code.strip())


class TokenProvider:
    """Fetches and caches an OAuth2 bearer token via the ION password grant."""

    def __init__(
        self,
        credentials: IonApiCredentials,
        http: HttpClient,
        *,
        skew_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cred = credentials
        self._http = http
        self._skew = skew_seconds
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    def bearer(self) -> str:
        if self._token is None or self._clock() >= self._expires_at:
            self._refresh()
        assert self._token is not None
        return self._token

    def _refresh(self) -> None:
        url = _join(self._cred.platform_url, self._cred.token_path)
        response = self._http.post(
            url,
            data={
                "grant_type": "password",
                "username": self._cred.service_access_key,
                "password": self._cred.service_secret_key,
                "client_id": self._cred.client_id,
                "client_secret": self._cred.client_secret,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise PublisherError(f"OAuth token request failed (HTTP {response.status_code})")
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise PublisherError("OAuth response contained no access_token")
        self._token = token
        self._expires_at = self._clock() + float(body.get("expires_in", 3600)) - self._skew


class MetadataPublisherClient:
    """Typed wrapper over the M3 Metadata Publisher (``/M3/mdprest``)."""

    def __init__(
        self, base_url: str, token_provider: TokenProvider, http: HttpClient, *, langid: str = "GB"
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token_provider
        self._http = http
        self._langid = langid

    @classmethod
    def from_ionapi(
        cls, credentials: IonApiCredentials, http: HttpClient, *, base_url: str | None = None, langid: str = "GB"
    ) -> "MetadataPublisherClient":
        base = base_url or mdp_base_url(credentials)
        return cls(base, TokenProvider(credentials, http), http, langid=langid)

    def _get_list(self, path: str, params: dict | None = None) -> list[dict]:
        headers = {"Authorization": f"Bearer {self._token.bearer()}", "Accept": "application/json"}
        response = self._http.get(self._base + path, params=params, headers=headers)
        if response.status_code != 200:
            raise PublisherError(f"GET {path} failed (HTTP {response.status_code})")
        return response.json().get("list", []) or []

    def list_tables(self, prefix: str | None = None) -> list[TableMeta]:
        params = {"filter": prefix} if prefix else None
        return [
            TableMeta(
                table_name=row.get("tableName", ""),
                description=row.get("tableDescription", ""),
                component=row.get("tableComponent", ""),
                category=row.get("tableCategory", ""),
                maintained_by=row.get("tableMaintainedBy", "") or "",
            )
            for row in self._get_list("/les/getTables", params)
        ]

    def get_columns(self, table: str, component: str) -> list[Column]:
        rows = self._get_list(
            f"/les/getColumnsUsedByTable/{table}/{component}", {"langId": self._langid}
        )
        return [
            Column(
                name=row.get("columnName", ""),
                data_type=row.get("dataType", ""),
                length=_parse_int(row.get("length")),
                decimals=_parse_int(row.get("decimals")),
                edit_code=row.get("editCode", ""),
                indexes=_parse_indexes(row.get("indexes", "")),
                description=row.get("description", ""),
            )
            for row in rows
        ]

    def get_index_keys(self, table: str, component: str) -> list[str]:
        rows = self._get_list(f"/les/getIndexKeys/{table}00/{component}", {"langId": self._langid})
        return [row.get("columnName", "") for row in rows if row.get("columnName")]


def _inject_pk(column: Column, pk_names: list[str]) -> Column:
    if column.name in pk_names and "00" not in column.indexes:
        return replace(column, indexes=("00", *column.indexes))
    return column


def refresh_schema(
    client: MetadataPublisherClient,
    cache: SchemaCache,
    *,
    prefix: str | None = None,
    fetched_at: str = "",
    progress: ProgressFn | None = None,
) -> int:
    """Refresh the cache from the Metadata Publisher; returns the table count.

    Bulk ``getTables`` then per-table ``getColumnsUsedByTable``; PK derived from
    index-00 membership, with the index-keys endpoint as a fallback.
    """
    tables = client.list_tables(prefix)
    total = len(tables)
    for i, meta in enumerate(tables, start=1):
        columns = client.get_columns(meta.table_name, meta.component)
        if not any(column.is_pk for column in columns):
            pk_names = client.get_index_keys(meta.table_name, meta.component)
            if pk_names:
                columns = [_inject_pk(column, pk_names) for column in columns]
        cache.upsert_table(
            TableSchema(
                component=meta.component,
                table_name=meta.table_name,
                category=meta.category,
                description=meta.description,
                columns=tuple(columns),
                fetched_at=fetched_at,
                maintained_by=meta.maintained_by,
            )
        )
        if progress is not None:
            progress(i, total, meta.table_name)
    return total


def refresh_table_info(
    client: MetadataPublisherClient,
    cache: SchemaCache,
    *,
    prefix: str | None = None,
    progress: ProgressFn | None = None,
) -> int:
    """Update cached tables' list-endpoint metadata (category, description,
    maintained-by) from a single ``getTables`` call — no per-table column
    fetches. Returns how many cached tables were updated; tables not already
    in the cache are skipped (they have no columns to diff with anyway).
    """
    tables = client.list_tables(prefix)
    total = len(tables)
    updated = 0
    for i, meta in enumerate(tables, start=1):
        if cache.set_table_info(
            meta.component,
            meta.table_name,
            category=meta.category,
            description=meta.description,
            maintained_by=meta.maintained_by,
        ):
            updated += 1
        if progress is not None:
            progress(i, total, meta.table_name)
    return updated


def httpx_client(*, timeout: float = 30.0) -> HttpClient:
    """A pooled httpx-backed HttpClient. Requires the ``[schema]`` extra."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise PublisherError("httpx is required for schema refresh; install m3diff[schema]") from exc

    class _HttpxClient:
        def __init__(self) -> None:
            self._client = httpx.Client(timeout=timeout)

        def get(self, url: str, *, params=None, headers=None) -> HttpResponse:
            return self._client.get(url, params=params, headers=headers)

        def post(self, url: str, *, data=None, headers=None) -> HttpResponse:
            return self._client.post(url, data=data, headers=headers)

    return _HttpxClient()
