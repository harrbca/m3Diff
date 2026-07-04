"""Tests for .ionapi parsing (secrets never leaked)."""
from __future__ import annotations

import json

import pytest

from m3diff.schema.ionapi import load_ionapi

_FULL = {
    "ci": "client-id",
    "cs": "client-secret",
    "ti": "TENANT_X",
    "saak": "access-key",
    "sask": "secret-key",
    "pu": "https://gateway.example/",
    "ot": "/oauth/token",
    "oa": "/oauth/authorize",
    "or": "/oauth/revoke",
    "ev": "prod",
}


def _write(tmp_path, data):
    path = tmp_path / "creds.ionapi"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_parses_all_fields(tmp_path):
    creds = load_ionapi(_write(tmp_path, _FULL))
    assert creds.client_id == "client-id"
    assert creds.tenant == "TENANT_X"
    assert creds.platform_url == "https://gateway.example/"
    assert creds.token_path == "/oauth/token"
    assert creds.environment == "prod"


def test_missing_required_field_raises(tmp_path):
    data = dict(_FULL)
    del data["sask"]
    with pytest.raises(ValueError):
        load_ionapi(_write(tmp_path, data))


def test_repr_redacts_secrets(tmp_path):
    creds = load_ionapi(_write(tmp_path, _FULL))
    text = repr(creds)
    assert "client-secret" not in text
    assert "secret-key" not in text
    assert "redacted" in text
    assert "TENANT_X" in text  # non-secret context is fine
