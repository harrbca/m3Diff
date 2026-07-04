"""Parse a standard Infor ``.ionapi`` credential file (ADR-007).

We read the file ourselves rather than depend on the private InforSDK, so the
project stays publishable and stdlib-only at its core. The file is a secret: its
values are never logged, and ``repr`` deliberately redacts them.

Fields (from the ``.ionapi`` JSON):
  ci  client id            cs  client secret       ti  tenant
  saak service access key  sask service secret key pu  platform (gateway) URL
  ot  OAuth token path     oa  OAuth authorize     or  OAuth revoke
  ev  environment
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_REQUIRED = ("ci", "cs", "ti", "saak", "sask", "pu", "ot")


@dataclass(frozen=True, slots=True, repr=False)
class IonApiCredentials:
    client_id: str
    client_secret: str
    tenant: str
    service_access_key: str
    service_secret_key: str
    platform_url: str
    token_path: str
    authorize_path: str = ""
    revoke_path: str = ""
    environment: str = ""

    def __repr__(self) -> str:
        # Never leak secrets in logs or tracebacks.
        return f"IonApiCredentials(tenant={self.tenant!r}, platform_url={self.platform_url!r}, <secrets redacted>)"


def load_ionapi(path: str | os.PathLike[str]) -> IonApiCredentials:
    """Load and validate a ``.ionapi`` file. Raises ValueError if fields are missing."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in _REQUIRED if not data.get(key)]
    if missing:
        raise ValueError(f".ionapi is missing required fields: {missing}")
    return IonApiCredentials(
        client_id=data["ci"],
        client_secret=data["cs"],
        tenant=data["ti"],
        service_access_key=data["saak"],
        service_secret_key=data["sask"],
        platform_url=data["pu"],
        token_path=data["ot"],
        authorize_path=data.get("oa", ""),
        revoke_path=data.get("or", ""),
        environment=data.get("ev", ""),
    )
