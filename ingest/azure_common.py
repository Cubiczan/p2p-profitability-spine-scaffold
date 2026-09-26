"""Shared helpers for the Azure-dependent tools in search/ and ingest/.

Only the standard library is imported at module level so the offline mapping and chunking code
(and its tests) run without any Azure packages. `azure-identity` is imported lazily when a token
is actually requested.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

HeaderFn = Callable[[], Dict[str, str]]

# Windows paths longer than this are opened through the \\?\ prefix or a short temp copy.
LONG_PATH_THRESHOLD = 240


def long_path(p: Path | str, force: bool = False) -> str:
    """Return a path string usable for os-level IO even when longer than MAX_PATH on Windows.

    force=True prefixes regardless of length (use for directory walks whose children may be long).
    """
    s = os.path.abspath(str(p))
    if os.name != "nt" or s.startswith("\\\\?\\") or (len(s) <= LONG_PATH_THRESHOLD and not force):
        return s
    if s.startswith("\\\\"):  # UNC share: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def bearer_headers(auth: str, scope: str, client_id: Optional[str] = None, tenant_id: Optional[str] = None) -> HeaderFn:
    """Entra ID bearer-token headers. auth = 'default' | 'interactive'. Tokens are cached and refreshed."""
    from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential  # lazy: optional extra

    if auth == "interactive":
        kw: Dict[str, Any] = {}
        if client_id:
            kw["client_id"] = client_id
        if tenant_id:
            kw["tenant_id"] = tenant_id
        cred = InteractiveBrowserCredential(**kw)
    else:
        cred = DefaultAzureCredential(managed_identity_client_id=client_id) if client_id else DefaultAzureCredential()

    state: Dict[str, Any] = {"token": None, "expires_on": 0}

    def headers() -> Dict[str, str]:
        if state["token"] is None or state["expires_on"] - time.time() < 300:
            tok = cred.get_token(scope)
            state["token"], state["expires_on"] = tok.token, tok.expires_on
        return {"Authorization": f"Bearer {state['token']}"}

    return headers


def key_headers(header_name: str, env_var: str) -> Optional[HeaderFn]:
    """Static API-key header read from an environment variable. The key is never logged."""
    key = os.environ.get(env_var, "").strip()
    if not key:
        return None
    return lambda: {header_name: key}


def http_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Any = None,
    raw_body: Optional[bytes] = None,
    timeout: float = 120.0,
    retries: int = 4,
) -> Tuple[int, Dict[str, str], Any]:
    """Minimal JSON-over-HTTPS client with retry on 429/5xx (honours Retry-After)."""
    data = raw_body if raw_body is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    hdrs = {"Accept": "application/json", **headers}
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                return resp.status, dict(resp.headers), (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                attempt += 1
                wait = float(e.headers.get("Retry-After") or 2**attempt)
                print(f"  HTTP {e.code}; retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(min(wait, 60))
                continue
            try:
                detail = json.loads(payload)
            except ValueError:
                detail = payload.decode("utf-8", "replace")[:2000]
            # Strip the query string (may carry api-version only; never keys) and never echo headers.
            raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {detail}") from None
