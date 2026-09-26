"""Deploy the spine to Microsoft Fabric via REST.

Steps: sign in (browser) -> find workspace -> create/find lakehouse -> upload the
data-contract directory to OneLake Files/bronze/<dataset>/ -> create/update the two
notebooks (parameters injected) -> optionally run them in order.

Usage:
  uv run --with azure-identity --with requests python deploy/fabric_deploy.py \
      --workspace "My Workspace" --data data/example --dataset example --run

You sign in interactively; no secrets are stored or printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from azure.identity import InteractiveBrowserCredential

API = "https://api.fabric.microsoft.com/v1"
ONELAKE = "https://onelake.dfs.fabric.microsoft.com"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
REPO = Path(__file__).resolve().parents[1]
NOTEBOOKS = ["nb_spine_01_bronze", "nb_spine_02_gold"]


class Fabric:
    def __init__(self, cred: InteractiveBrowserCredential) -> None:
        self.cred = cred

    def _h(self, scope: str = FABRIC_SCOPE) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.cred.get_token(scope).token}"}

    def call(self, method: str, path: str, body: Optional[dict] = None) -> requests.Response:
        url = path if path.startswith("http") else f"{API}{path}"
        for _ in range(8):
            r = requests.request(method, url, headers={**self._h(), "Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "10")))
                continue
            return r
        r.raise_for_status()
        return r

    def lro(self, r: requests.Response) -> dict:
        """Resolve a 201/200 or 202 long-running response to its result."""
        if r.status_code in (200, 201):
            return r.json() if r.content else {}
        if r.status_code != 202:
            raise RuntimeError(f"{r.status_code}: {r.text}")
        loc = r.headers["Location"]
        while True:
            time.sleep(int(r.headers.get("Retry-After", "5")))
            r = self.call("GET", loc)
            state = r.json().get("status") if r.content else None
            if state in ("Succeeded", None):
                res = self.call("GET", loc.rstrip("/") + "/result")
                return res.json() if res.status_code == 200 and res.content else {}
            if state == "Failed":
                raise RuntimeError(r.text)

    def items(self, ws: str, item_type: str) -> List[dict]:
        r = self.call("GET", f"/workspaces/{ws}/items?type={item_type}")
        r.raise_for_status()
        return r.json().get("value", [])

    def upload(self, ws: str, lh: str, rel: str, data: bytes) -> None:
        url = f"{ONELAKE}/{ws}/{lh}/{rel}"
        h = self._h(STORAGE_SCOPE)
        requests.put(f"{url}?resource=file", headers=h, timeout=120).raise_for_status()
        requests.patch(f"{url}?action=append&position=0", headers={**h, "Content-Type": "application/octet-stream"}, data=data, timeout=300).raise_for_status()
        requests.patch(f"{url}?action=flush&position={len(data)}", headers=h, timeout=120).raise_for_status()


def fabric_py_to_ipynb(src: str, params: Dict[str, str]) -> dict:
    """Convert Fabric notebook-content.py to ipynb, injecting parameter values."""
    marker = re.compile(r"^# (CELL|MARKDOWN|PARAMETERS CELL|METADATA) \*+\s*$", re.M)
    parts = marker.split(src)
    cells = []
    i = 1
    while i < len(parts):
        kind, body = parts[i], parts[i + 1]
        i += 2
        if kind == "METADATA":
            continue
        text = body.strip("\n")
        if kind == "MARKDOWN":
            lines = [ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in text.splitlines()]
            cells.append({"cell_type": "markdown", "metadata": {}, "source": "\n".join(lines)})
            continue
        meta: dict = {}
        if kind == "PARAMETERS CELL":
            meta = {"tags": ["parameters"]}
            for k, v in params.items():
                text = re.sub(rf"^{k} = .*$", f"{k} = {json.dumps(v)}", text, flags=re.M)
        cells.append({"cell_type": "code", "metadata": meta, "source": text, "outputs": [], "execution_count": None})
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": cells,
        "metadata": {"kernel_info": {"name": "synapse_pyspark"}, "language_info": {"name": "python"}},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True, help="Fabric workspace display name (must be on a Fabric capacity)")
    ap.add_argument("--lakehouse", default="lh_p2p_spine")
    ap.add_argument("--data", type=Path, required=True, help="Data-contract directory to upload")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--engine-spec", default="git+https://github.com/icohangar-ops/p2p-profitability-spine-scaffold@v0.1.0")
    ap.add_argument("--notebook-dir", type=Path, default=REPO / "fabric")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--create-workspace", action="store_true", help="Create the workspace on the first active capacity if missing")
    ap.add_argument("--capacity-id", default=None, help="Capacity to use with --create-workspace")
    ap.add_argument("--run", action="store_true", help="Run bronze then gold notebooks after deploy")
    a = ap.parse_args()

    fab = Fabric(InteractiveBrowserCredential(tenant_id=a.tenant))
    r = fab.call("GET", "/workspaces")
    r.raise_for_status()
    ws = next((w for w in r.json()["value"] if w["displayName"] == a.workspace), None)
    if not ws and a.create_workspace:
        caps = fab.call("GET", "/capacities")
        caps.raise_for_status()
        active = [c for c in caps.json().get("value", []) if c.get("state") == "Active" and (not a.capacity_id or c["id"] == a.capacity_id)]
        if not active:
            print("No active Fabric capacity found (start a trial or assign an F-SKU).", file=sys.stderr)
            return 2
        cap = active[0]
        print(f"creating workspace '{a.workspace}' on capacity {cap.get('displayName')} ({cap.get('sku')}, {cap.get('region')})")
        created = fab.call("POST", "/workspaces", {"displayName": a.workspace, "capacityId": cap["id"], "description": "Procurement-to-profitability spine"})
        if created.status_code not in (200, 201):
            raise RuntimeError(f"create workspace: {created.status_code} {created.text}")
        ws = created.json()
    if not ws:
        print(f"Workspace '{a.workspace}' not found. Re-run with --create-workspace, or create it in Fabric on a trial/F-SKU capacity.", file=sys.stderr)
        return 2
    wsid = ws["id"]
    print(f"workspace {a.workspace} = {wsid}")

    lh = next((i for i in fab.items(wsid, "Lakehouse") if i["displayName"] == a.lakehouse), None)
    if not lh:
        lh = fab.lro(fab.call("POST", f"/workspaces/{wsid}/items", {"displayName": a.lakehouse, "type": "Lakehouse"}))
        if not lh.get("id"):
            lh = next(i for i in fab.items(wsid, "Lakehouse") if i["displayName"] == a.lakehouse)
    lhid = lh["id"]
    print(f"lakehouse {a.lakehouse} = {lhid}")

    files = sorted(p for p in a.data.iterdir() if p.suffix in {".csv", ".json"})
    for p in files:
        fab.upload(wsid, lhid, f"Files/bronze/{a.dataset}/{p.name}", p.read_bytes())
    print(f"uploaded {len(files)} files to Files/bronze/{a.dataset}/")

    params = {"WORKSPACE": wsid, "LAKEHOUSE": lhid, "DATASET": a.dataset, "ENGINE_SPEC": a.engine_spec}
    existing = {i["displayName"]: i for i in fab.items(wsid, "Notebook")}
    ids: Dict[str, str] = {}
    for nb in NOTEBOOKS:
        src = (a.notebook_dir / f"{nb}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")
        payload = base64.b64encode(json.dumps(fabric_py_to_ipynb(src, params)).encode()).decode()
        definition = {"format": "ipynb", "parts": [{"path": "artifact.content.ipynb", "payload": payload, "payloadType": "InlineBase64"}]}
        if nb in existing:
            ids[nb] = existing[nb]["id"]
            fab.lro(fab.call("POST", f"/workspaces/{wsid}/items/{ids[nb]}/updateDefinition", {"definition": definition}))
            print(f"updated notebook {nb}")
        else:
            res = fab.lro(fab.call("POST", f"/workspaces/{wsid}/items", {"displayName": nb, "type": "Notebook", "definition": definition}))
            ids[nb] = res.get("id") or next(i["id"] for i in fab.items(wsid, "Notebook") if i["displayName"] == nb)
            print(f"created notebook {nb}")

    if a.run:
        for nb in NOTEBOOKS:
            r = fab.call("POST", f"/workspaces/{wsid}/items/{ids[nb]}/jobs/RunNotebook/instances", {})
            if r.status_code != 202:
                raise RuntimeError(f"run {nb}: {r.status_code} {r.text}")
            loc = r.headers["Location"]
            print(f"running {nb} ...")
            while True:
                time.sleep(int(r.headers.get("Retry-After", "20")))
                st = fab.call("GET", loc).json()
                if st.get("status") in ("Completed", "Failed", "Cancelled", "Deduped"):
                    print(f"{nb}: {st.get('status')} {st.get('failureReason') or ''}")
                    if st.get("status") != "Completed":
                        return 1
                    break
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
