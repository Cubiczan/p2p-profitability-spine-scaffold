"""Azure AI Document Intelligence (v4.0 GA) analyze call over REST.

POST {endpoint}/documentintelligence/documentModels/{modelId}:analyze?api-version=2024-11-30
body {"base64Source": "..."} -> 202 + Operation-Location, poll until status is succeeded/failed.
Reference: https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document
"""

from __future__ import annotations

import base64
import time
import urllib.parse
from typing import Any, Dict, Optional

from ingest.azure_common import HeaderFn, bearer_headers, http_json, key_headers

DI_API_VERSION = "2024-11-30"  # v4.0 GA
DI_SCOPE = "https://cognitiveservices.azure.com/.default"
DI_KEY_ENV = "AZURE_DOCINTEL_KEY"


def di_headers(auth: str = "default", client_id: Optional[str] = None, tenant_id: Optional[str] = None) -> HeaderFn:
    """Key from AZURE_DOCINTEL_KEY if set, else Entra ID (requires a custom-subdomain endpoint)."""
    return key_headers("Ocp-Apim-Subscription-Key", DI_KEY_ENV) or bearer_headers(auth, DI_SCOPE, client_id, tenant_id)


def analyze(
    endpoint: str,
    model_id: str,
    content: bytes,
    headers: HeaderFn,
    api_version: str = DI_API_VERSION,
    features: Optional[list[str]] = None,
    poll_seconds: float = 2.0,
    timeout_seconds: float = 600.0,
) -> Dict[str, Any]:
    """Run a model on document bytes; return the full operation JSON (with `analyzeResult`)."""
    q: Dict[str, str] = {"api-version": api_version}
    if features:
        q["features"] = ",".join(features)
    url = f"{endpoint.rstrip('/')}/documentintelligence/documentModels/{urllib.parse.quote(model_id)}:analyze?{urllib.parse.urlencode(q)}"
    status, resp_headers, _ = http_json("POST", url, headers(), body={"base64Source": base64.b64encode(content).decode("ascii")})
    op = {k.lower(): v for k, v in resp_headers.items()}.get("operation-location")
    if status != 202 or not op:
        raise RuntimeError(f"Document Intelligence analyze did not return 202 + Operation-Location (got {status})")
    deadline = time.time() + timeout_seconds
    while True:
        _, _, body = http_json("GET", op, headers())
        state = (body or {}).get("status", "")
        if state == "succeeded":
            return body
        if state == "failed":
            raise RuntimeError(f"Document Intelligence analysis failed: {body.get('error')}")
        if time.time() > deadline:
            raise TimeoutError(f"Document Intelligence analysis still '{state}' after {timeout_seconds:.0f}s")
        time.sleep(poll_seconds)


def result_of(response: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either the operation envelope ({status, analyzeResult}) or a bare analyzeResult."""
    return response.get("analyzeResult", response)


def page_texts(response: Dict[str, Any]) -> list[tuple[int, str]]:
    """[(page_number, text)] from a prebuilt-read / prebuilt-layout result."""
    res = result_of(response)
    content = res.get("content", "")
    out: list[tuple[int, str]] = []
    for page in res.get("pages", []):
        lines = [ln.get("content", "") for ln in page.get("lines", [])]
        if not lines and page.get("spans"):
            lines = [content[s["offset"] : s["offset"] + s["length"]] for s in page["spans"]]
        out.append((int(page.get("pageNumber", len(out) + 1)), "\n".join(lines)))
    return out
