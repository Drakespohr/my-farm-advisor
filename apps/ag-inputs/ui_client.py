"""HTTP client the Streamlit page uses to call the API (so every role check still applies).

AG_API_URL   base URL of the API (default http://localhost:8000)
AG_AUTH_MODE "dev" sends X-Dev-User headers for the user picked in the sidebar.
AG_API_TOKEN bearer token to send instead (for Cognito, once it's wired up). Never hardcode one.

Tests swap in an in-process client with set_client().
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

_client: Optional[Any] = None


def set_client(client: Optional[Any]) -> None:
    global _client
    _client = client


def client() -> Any:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=os.getenv("AG_API_URL", "http://localhost:8000"), timeout=15)
    return _client


def headers(dev_user: Optional[str]) -> dict[str, str]:
    token = os.getenv("AG_API_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}
    if os.getenv("AG_AUTH_MODE", "").lower() == "dev" and dev_user:
        return {"X-Dev-User": dev_user, "X-Dev-Name": dev_user}
    return {}


def call(method: str, path: str, dev_user: Optional[str], **kw: Any) -> httpx.Response:
    return client().request(method, path, headers=headers(dev_user), **kw)
