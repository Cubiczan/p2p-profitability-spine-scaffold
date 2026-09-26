"""Show the latest ARM deployment state and resources in a resource group (read-only).

Usage: python infra/status.py --subscription <id> --resource-group <rg> [--client-id ...] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from fabric_deploy import _interactive_credential  # noqa: E402

ARM = "https://management.azure.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscription", required=True)
    ap.add_argument("--resource-group", required=True)
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--json", type=Path, default=None, help="write deployment outputs + resource list here")
    a = ap.parse_args()
    cred = _interactive_credential(a.tenant, a.client_id)
    h = {"Authorization": f"Bearer {cred.get_token(ARM + '/.default').token}"}
    rg = f"{ARM}/subscriptions/{a.subscription}/resourceGroups/{a.resource_group}"
    deps = requests.get(f"{rg}/providers/Microsoft.Resources/deployments?api-version=2025-04-01", headers=h, timeout=60).json().get("value", [])
    deps.sort(key=lambda d: d["properties"].get("timestamp", ""), reverse=True)
    out: dict = {"subscription": a.subscription, "resource_group": a.resource_group, "deployments": [], "resources": []}
    for d in deps[:3]:
        p = d["properties"]
        print(f"deployment {d['name']}: {p.get('provisioningState')} at {p.get('timestamp')}")
        out["deployments"].append({"name": d["name"], "state": p.get("provisioningState"), "timestamp": p.get("timestamp"),
                                   "outputs": {k: v.get("value") for k, v in (p.get("outputs") or {}).items()}})
    res = requests.get(f"{rg}/resources?api-version=2021-04-01", headers=h, timeout=60).json().get("value", [])
    for r in sorted(res, key=lambda r: r["type"]):
        print(f"  {r['type']:<55} {r['name']}  {r.get('sku', {}).get('name', '')}")
        out["resources"].append({"type": r["type"], "name": r["name"], "sku": r.get("sku", {}).get("name", ""), "location": r.get("location")})
    if deps:
        for k, v in out["deployments"][0]["outputs"].items():
            print(f"  output {k} = {v}")
    if a.json:
        a.json.write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
