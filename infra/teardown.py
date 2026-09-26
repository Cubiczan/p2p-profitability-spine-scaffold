"""Tear down the Azure side of the spine to stop charges, safely.

1. Moves every Fabric workspace assigned to the resource group's Fabric capacity onto a fallback
   capacity (e.g. the Fabric trial), so notebooks, lakehouse and data agents keep working.
2. Deletes the resource group (Foundry account/project/model, AI Search, Key Vault, App Insights,
   Log Analytics, Fabric F-SKU). Irreversible - requires --confirm <resource-group-name>.

Soft-delete notes: the Foundry (Cognitive Services) account and Key Vault go to soft-delete; they
incur no charges there but their names stay reserved (Key Vault for the retention period). Use
--purge to purge the Cognitive Services account after deletion so the name can be reused.

Redeploy later with infra/deploy_infra.py (about 10 minutes), then re-run
foundry/create_connections.py and foundry/create_agent.py.

Usage:
  python infra/teardown.py --subscription <id> --resource-group <rg> --fallback-capacity-id <trial capacity guid> \
      --client-id 1950a258-227b-4e31-a9cf-717495945fc2                # dry run: shows what would happen
  ... --confirm <rg>                                                  # actually delete
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from fabric_deploy import Fabric, _interactive_credential  # noqa: E402

ARM = "https://management.azure.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscription", required=True)
    ap.add_argument("--resource-group", required=True)
    ap.add_argument("--fallback-capacity-id", default="", help="Fabric capacity to move workspaces to (e.g. trial)")
    ap.add_argument("--confirm", default="", help="Type the resource group name to actually delete")
    ap.add_argument("--purge", action="store_true", help="Purge soft-deleted Cognitive Services accounts after deletion")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--client-id", default=None)
    a = ap.parse_args()
    apply = a.confirm == a.resource_group

    cred = _interactive_credential(a.tenant, a.client_id)
    h = {"Authorization": f"Bearer {cred.get_token(ARM + '/.default').token}"}
    rg_url = f"{ARM}/subscriptions/{a.subscription}/resourceGroups/{a.resource_group}"
    res = requests.get(f"{rg_url}/resources?api-version=2021-04-01", headers=h, timeout=60).json().get("value", [])
    print(f"resource group {a.resource_group}: {len(res)} resources")
    for r in res:
        print(f"  {r['type']:<50} {r['name']}")

    fab = Fabric(cred)
    caps = {c["displayName"]: c for c in fab.call("GET", "/capacities").json().get("value", [])}
    rg_caps = [caps[r["name"]] for r in res if r["type"].lower() == "microsoft.fabric/capacities" and r["name"] in caps]
    for cap in rg_caps:
        wss = [w for w in fab.call("GET", "/workspaces").json().get("value", []) if w.get("capacityId") == cap["id"]]
        for w in wss:
            print(f"workspace {w['displayName']} is on {cap['displayName']} -> move to {a.fallback_capacity_id or '(none given)'}")
            if apply:
                if not a.fallback_capacity_id:
                    print("  refusing to delete: give --fallback-capacity-id so the workspace is not orphaned", file=sys.stderr)
                    return 2
                r = fab.call("POST", f"/workspaces/{w['id']}/assignToCapacity", {"capacityId": a.fallback_capacity_id})
                print(f"  reassign: {r.status_code}")
                for _ in range(30):
                    st = fab.call("GET", f"/workspaces/{w['id']}").json()
                    if st.get("capacityId") == a.fallback_capacity_id and st.get("capacityAssignmentProgress") in (None, "Completed"):
                        break
                    time.sleep(10)

    if not apply:
        print(f"\ndry run. Re-run with --confirm {a.resource_group} to move workspaces and delete the resource group.")
        return 0

    accounts = [r for r in res if r["type"].lower() == "microsoft.cognitiveservices/accounts"]
    d = requests.delete(f"{rg_url}?api-version=2021-04-01", headers=h, timeout=60)
    print(f"delete resource group: {d.status_code}")
    if d.status_code not in (200, 202):
        print(d.text, file=sys.stderr)
        return 1
    loc = d.headers.get("Location")
    while loc:
        time.sleep(int(d.headers.get("Retry-After", "20")))
        p = requests.get(loc, headers=h, timeout=60)
        if p.status_code != 202:
            print(f"resource group deletion finished: {p.status_code}")
            break
    if a.purge:
        for acct in accounts:
            u = (f"{ARM}/subscriptions/{a.subscription}/providers/Microsoft.CognitiveServices/locations/{acct['location']}"
                 f"/resourceGroups/{a.resource_group}/deletedAccounts/{acct['name']}?api-version=2025-06-01")
            print(f"purge {acct['name']}: {requests.delete(u, headers=h, timeout=60).status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
