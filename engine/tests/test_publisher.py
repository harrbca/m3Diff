"""Tests for the Metadata Publisher client + refresh, against a fake HTTP client."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from m3diff.schema import SchemaCache
from m3diff.schema.ionapi import IonApiCredentials
from m3diff.schema.publisher import (
    MetadataPublisherClient,
    PublisherError,
    TokenProvider,
    mdp_base_url,
    refresh_schema,
)

_CREDS = IonApiCredentials(
    client_id="ci", client_secret="cs", tenant="T", service_access_key="saak",
    service_secret_key="sask", platform_url="https://ion.example", token_path="/token",
)


def test_mdp_base_url_targets_the_gateway_host_not_sso():
    creds = IonApiCredentials(
        client_id="ci", client_secret="cs", tenant="TENANT_X",
        service_access_key="s", service_secret_key="s",
        platform_url="https://mingle-sso.inforcloudsuite.com:443/TENANT_X/as/",
        token_path="/token",
    )
    # SSO host (used for the token) -> ION API gateway host (used for MDP).
    assert mdp_base_url(creds) == "https://mingle-ionapi.inforcloudsuite.com/TENANT_X/M3/mdprest"


@dataclass
class FakeResponse:
    status_code: int
    body: Any

    def json(self) -> Any:
        return self.body


class FakeHttp:
    """Routes GETs by substring; POSTs always return a token."""

    def __init__(self) -> None:
        self.routes: dict[str, list[dict]] = {}
        self.post_calls = 0
        self.token_body: dict[str, Any] = {"access_token": "TOKEN", "expires_in": 3600}

    def post(self, url, *, data=None, headers=None) -> FakeResponse:
        self.post_calls += 1
        return FakeResponse(200, self.token_body)

    def get(self, url, *, params=None, headers=None) -> FakeResponse:
        for key, rows in self.routes.items():
            if key in url:
                return FakeResponse(200, {"errorMsg": "OK", "list": rows})
        return FakeResponse(404, {"errorMsg": "not found"})


def _client(http, base="https://mdp/M3/mdprest"):
    return MetadataPublisherClient(base, TokenProvider(_CREDS, http), http)


def test_token_is_fetched_once_and_cached():
    clock = [1000.0]
    http = FakeHttp()
    provider = TokenProvider(_CREDS, http, skew_seconds=30, clock=lambda: clock[0])
    assert provider.bearer() == "TOKEN"
    assert provider.bearer() == "TOKEN"
    assert http.post_calls == 1  # cached
    clock[0] += 4000  # past expiry (3600 - 30 skew)
    assert provider.bearer() == "TOKEN"
    assert http.post_calls == 2  # refreshed


def test_token_error_raises():
    http = FakeHttp()
    http.token_body = {}  # no access_token
    with pytest.raises(PublisherError):
        TokenProvider(_CREDS, http).bearer()


def test_list_tables_parses_metadata():
    http = FakeHttp()
    http.routes["/les/getTables"] = [
        {"tableName": "MITMAS", "tableComponent": "MVX", "tableCategory": "MF",
         "tableDescription": "Item Master"},
    ]
    tables = _client(http).list_tables()
    assert tables[0].table_name == "MITMAS"
    assert tables[0].component == "MVX"
    assert tables[0].category == "MF"


def test_get_columns_parses_types_and_indexes():
    http = FakeHttp()
    http.routes["/les/getColumnsUsedByTable/MITMAS/MVX"] = [
        {"columnName": "MMCONO", "dataType": "Decimal", "length": "3", "decimals": "",
         "editCode": "4", "indexes": "00,01,10"},
        {"columnName": "MMITDS", "dataType": "String", "length": "30", "indexes": ""},
    ]
    columns = _client(http).get_columns("MITMAS", "MVX")
    assert columns[0].length == 3 and columns[0].indexes == ("00", "01", "10")
    assert columns[0].is_pk is True
    assert columns[1].length == 30 and columns[1].indexes == () and columns[1].is_pk is False


def test_refresh_derives_pk_from_index_00():
    http = FakeHttp()
    http.routes["/les/getTables"] = [
        {"tableName": "MITMAS", "tableComponent": "MVX", "tableCategory": "MF",
         "tableDescription": "Item Master"},
    ]
    http.routes["/les/getColumnsUsedByTable/MITMAS/MVX"] = [
        {"columnName": "MMCONO", "dataType": "Decimal", "length": "3", "indexes": "00,01"},
        {"columnName": "MMITNO", "dataType": "String", "length": "15", "indexes": "00,01"},
        {"columnName": "MMITDS", "dataType": "String", "length": "30", "indexes": ""},
    ]
    with SchemaCache() as cache:
        count = refresh_schema(_client(http), cache, fetched_at="2026-07-04")
        assert count == 1
        schema = cache.get("MITMAS", "MVX")
        assert schema is not None
        assert schema.primary_key == ("MMCONO", "MMITNO")
        assert schema.category == "MF"


def test_refresh_falls_back_to_index_keys_when_no_column_reports_00():
    http = FakeHttp()
    http.routes["/les/getTables"] = [
        {"tableName": "WEIRD", "tableComponent": "MVX", "tableCategory": "MF", "tableDescription": ""},
    ]
    # No column reports index 00...
    http.routes["/les/getColumnsUsedByTable/WEIRD/MVX"] = [
        {"columnName": "WWKEY", "dataType": "String", "length": "5", "indexes": "10"},
        {"columnName": "WWVAL", "dataType": "String", "length": "5", "indexes": ""},
    ]
    # ...so the index-keys endpoint supplies the PK.
    http.routes["/les/getIndexKeys/WEIRD00/MVX"] = [{"columnName": "WWKEY", "sortOrder": "asc"}]
    with SchemaCache() as cache:
        refresh_schema(_client(http), cache, fetched_at="t")
        schema = cache.get("WEIRD", "MVX")
        assert schema is not None
        assert schema.primary_key == ("WWKEY",)


def test_refresh_reports_progress():
    http = FakeHttp()
    http.routes["/les/getTables"] = [
        {"tableName": "A", "tableComponent": "MVX", "tableCategory": "MF", "tableDescription": ""},
        {"tableName": "B", "tableComponent": "MVX", "tableCategory": "TF", "tableDescription": ""},
    ]
    http.routes["/les/getColumnsUsedByTable/A/MVX"] = [{"columnName": "ACONO", "indexes": "00"}]
    http.routes["/les/getColumnsUsedByTable/B/MVX"] = [{"columnName": "BCONO", "indexes": "00"}]
    seen: list[tuple[int, int, str]] = []
    with SchemaCache() as cache:
        refresh_schema(_client(http), cache, progress=lambda d, t, n: seen.append((d, t, n)))
    assert seen == [(1, 2, "A"), (2, 2, "B")]
