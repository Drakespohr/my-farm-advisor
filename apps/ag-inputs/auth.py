"""Authentication behind a small interface, so AWS Cognito can plug in later.

Pick the provider with AG_AUTH_MODE:

- unset / "disabled": every request is rejected (401). A misconfigured server never runs open.
- "dev": DevHeaderAuth reads the identity from request headers. For local development and
  tests only; never enable it on a public server.
      X-Dev-User:  stable subject id (required), e.g. "alice"
      X-Dev-Email: optional email
      X-Dev-Name:  optional display name
- "cognito": CognitoAuth (not implemented yet; returns 501). It will validate the bearer JWT
  against the Cognito user pool's JWKS using AG_COGNITO_REGION, AG_COGNITO_USER_POOL_ID, and
  AG_COGNITO_APP_CLIENT_ID, and map the token's `sub`, `email`, and `name` to an Identity.

No users are defined in code: users are created in the database the first time an identity
authenticates (see ag_inputs.get_current_user).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Identity:
    subject: str  # stable id from the identity provider (Cognito `sub`)
    email: Optional[str] = None
    name: Optional[str] = None


class AuthProvider(Protocol):
    def authenticate(self, request: Request) -> Optional[Identity]:
        """Return the caller's identity, or None if the request isn't authenticated."""


class DisabledAuth:
    def authenticate(self, request: Request) -> Optional[Identity]:
        raise HTTPException(401, "Authentication is not configured on this server (set AG_AUTH_MODE).")


class DevHeaderAuth:
    def authenticate(self, request: Request) -> Optional[Identity]:
        subject = (request.headers.get("x-dev-user") or "").strip()
        if not subject:
            return None
        return Identity(subject=subject[:255], email=request.headers.get("x-dev-email") or None,
                        name=request.headers.get("x-dev-name") or None)


class CognitoAuth:
    """Placeholder for AWS Cognito (JWT validation against the user pool JWKS)."""

    def __init__(self) -> None:
        self.region = os.getenv("AG_COGNITO_REGION")
        self.user_pool_id = os.getenv("AG_COGNITO_USER_POOL_ID")
        self.app_client_id = os.getenv("AG_COGNITO_APP_CLIENT_ID")

    def authenticate(self, request: Request) -> Optional[Identity]:
        raise HTTPException(501, "Cognito authentication is not implemented yet.")


_provider: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        mode = os.getenv("AG_AUTH_MODE", "disabled").strip().lower()
        _provider = {"dev": DevHeaderAuth, "cognito": CognitoAuth}.get(mode, DisabledAuth)()
    return _provider


def set_auth_provider(provider: Optional[AuthProvider]) -> None:
    """Swap the provider (tests, or a host app wiring in its own)."""
    global _provider
    _provider = provider
