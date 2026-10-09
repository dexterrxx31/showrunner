"""Optional API-key protection for state-changing endpoints.

Unset `SHOWRUNNER_API_KEY` keeps the open demo behavior. When set, every
request that is not GET, HEAD, or OPTIONS must carry the key, so viewers can
still pull manifests, guides, and the demo UI without credentials.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from app import config

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _supplied_key(request: Request) -> str:
    header = request.headers.get("x-api-key")
    if header:
        return header
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def require_write_key(request: Request) -> None:
    expected = config.API_KEY
    if not expected or request.method in _READ_ONLY_METHODS:
        return
    supplied = _supplied_key(request)
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
