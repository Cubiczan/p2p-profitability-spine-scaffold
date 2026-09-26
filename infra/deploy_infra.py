"""Deploy infra/azuredeploy.json with the ARM REST API (no az / bicep CLI needed).

Steps: sign in -> list visible subscriptions -> resolve --subscription -> ensure the resource
group -> what-if (always) -> deploy only with --apply -> poll -> print outputs.

Usage:
  uv run --no-project --with azure-identity --with requests python infra/deploy_infra.py \
      --subscription "<name or id>" --resource-group rg-p2p-spine --location southeastasia
  # review the what-if, then add --apply

You sign in interactively; no secrets are stored or printed. The operator object id is read
from the token's `oid` claim (unless --principal-id is given) and granted data-plane roles.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential

ARM = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"
SUBS_API = "2022-12-01"
RG_API = "2021-04-01"
DEPLOY_API = "2025-04-01"
HERE = Path(__file__).resolve().parent
TERMINAL = {"Succeeded", "Failed", "Canceled"}


class Arm:
    def __init__(self, cred: TokenCredential) -> None:
        self.cred = cred

    def token(self) -> str:
        return self.cred.get_token(ARM_SCOPE).token

    def call(self, method: str, path: str, api: Optional[str] = None, body: Optional[dict] = None) -> requests.Response:
        url = path if path.startswith("http") else f"{ARM}{path}"
        params = {"api-version": api} if api else None
        for _ in range(8):
            r = requests.request(
                method, url, params=params, json=body, timeout=120,
                headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"},
            )
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "10")))
                continue
            return r
        return r

    def ok(self, r: requests.Response, what: str) -> dict:
        if r.status_code >= 400:
            raise RuntimeError(f"{what}: HTTP {r.status_code} {r.text}")
        return r.json() if r.content else {}


def token_oid(token: str) -> Optional[str]:
    """Read the `oid` claim from a JWT payload (no signature check; used only as a default)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("oid")
    except (IndexError, ValueError):
        return None


def load_parameters(path: Optional[Path], overrides: List[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if path:
        doc = json.loads(path.read_text(encoding="utf-8"))
        params = dict(doc.get("parameters", doc))
    for item in overrides:
        key, _, raw = item.partition("=")
        if not key or not _:
            raise SystemExit(f"--set expects KEY=VALUE, got {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        params[key] = {"value": value}
    return params


def print_whatif(result: dict) -> None:
    changes = result.get("properties", {}).get("changes", []) or []
    counts = Counter(c.get("changeType", "?") for c in changes)
    print("\nwhat-if summary: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no changes"))
    for c in changes:
        rid = c.get("resourceId", "")
        parts = rid.split("/providers/")[-1] if "/providers/" in rid else rid
        print(f"  {c.get('changeType', '?'):<11} {parts}")
        for d in c.get("delta", []) or []:
            if d.get("propertyChangeType") != "NoEffect":
                print(f"      {d.get('propertyChangeType')}: {d.get('path')}")
    for diag in result.get("properties", {}).get("diagnostics", []) or []:
        print(f"  [{diag.get('level')}] {diag.get('code')}: {diag.get('message')}")
    if result.get("error"):
        print(f"what-if error: {json.dumps(result['error'], indent=2)}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subscription", help="Subscription display name or id (omit to just list what you can see)")
    ap.add_argument("--resource-group", help="Resource group to create/ensure and deploy into")
    ap.add_argument("--location", default="southeastasia", help="Resource group and resource location")
    ap.add_argument("--template", type=Path, default=HERE / "azuredeploy.json")
    ap.add_argument("--parameters", type=Path, default=None, help="ARM parameters file (e.g. a copy of azuredeploy.parameters.example.json)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Override a template parameter; VALUE is parsed as JSON when possible")
    ap.add_argument("--principal-id", default=None, help="Operator object id for role grants (default: signed-in user's oid)")
    ap.add_argument("--principal-type", default="User", choices=["User", "Group", "ServicePrincipal"])
    ap.add_argument("--no-principal", action="store_true", help="Skip operator role grants")
    ap.add_argument("--deployment-name", default="p2p-spine-infra")
    ap.add_argument("--apply", action="store_true", help="Actually deploy (default is what-if only)")
    ap.add_argument("--auth", choices=["interactive", "default"], default="interactive",
                    help="interactive = browser sign-in; default = DefaultAzureCredential (CI / azure/login)")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--client-id", default=None, help="Public client app id for interactive sign-in")
    a = ap.parse_args()

    if a.auth == "default":
        cred: TokenCredential = DefaultAzureCredential()
    else:
        cred = InteractiveBrowserCredential(tenant_id=a.tenant, **({"client_id": a.client_id} if a.client_id else {}))
    arm = Arm(cred)

    subs = arm.ok(arm.call("GET", "/subscriptions", SUBS_API), "list subscriptions").get("value", [])
    print("subscriptions visible to you:")
    for s in subs:
        print(f"  {s.get('displayName')}  {s.get('subscriptionId')}  {s.get('state')}")
    if not subs:
        print("No subscriptions visible. Create one or ask for access, then re-run.", file=sys.stderr)
        return 2
    if not a.subscription:
        print("\nRe-run with --subscription <name or id> and --resource-group <name>.", file=sys.stderr)
        return 2
    sub = next((s for s in subs if a.subscription in (s.get("subscriptionId"), s.get("displayName"))), None)
    if not sub:
        print(f"Subscription '{a.subscription}' not found among the ones listed above.", file=sys.stderr)
        return 2
    if sub.get("state") != "Enabled":
        print(f"Subscription state is {sub.get('state')}; deployments need Enabled.", file=sys.stderr)
        return 2
    if not a.resource_group:
        print("--resource-group is required.", file=sys.stderr)
        return 2
    sid = sub["subscriptionId"]
    print(f"\nusing subscription {sub.get('displayName')} ({sid})")

    template = json.loads(a.template.read_text(encoding="utf-8"))
    params = load_parameters(a.parameters, a.set)
    params.setdefault("location", {"value": a.location})
    if not a.no_principal:
        pid = a.principal_id or token_oid(arm.token())
        if pid:
            params["principalId"] = {"value": pid}
            params["principalType"] = {"value": a.principal_type}
            print(f"operator role grants -> {a.principal_type} {pid}")
        else:
            print("could not read oid from token; skipping operator role grants (use --principal-id)")
    if params.get("deployFabricCapacity", {}).get("value") and not params.get("fabricAdminMembers", {}).get("value"):
        print("deployFabricCapacity=true needs fabricAdminMembers (UPNs or SP object ids).", file=sys.stderr)
        return 2

    rg_path = f"/subscriptions/{sid}/resourcegroups/{a.resource_group}"
    rg = arm.call("GET", rg_path, RG_API)
    if rg.status_code == 404:
        # What-if runs at resource-group scope, so the (empty, free) group is created even without --apply.
        arm.ok(arm.call("PUT", rg_path, RG_API, {"location": a.location}), "create resource group")
        print(f"created resource group {a.resource_group} in {a.location}")
    else:
        rgj = arm.ok(rg, "get resource group")
        print(f"resource group {a.resource_group} exists in {rgj.get('location')}")

    dep_path = f"{rg_path}/providers/Microsoft.Resources/deployments/{a.deployment_name}"
    props = {"mode": "Incremental", "template": template, "parameters": params}

    r = arm.call("POST", f"{dep_path}/whatIf", DEPLOY_API, {"properties": {**props, "whatIfSettings": {"resultFormat": "FullResourcePayloads"}}})
    if r.status_code == 202:
        loc = r.headers["Location"]
        while r.status_code == 202:
            time.sleep(int(r.headers.get("Retry-After", "5")))
            r = arm.call("GET", loc)
    print_whatif(arm.ok(r, "what-if"))

    if not a.apply:
        print("\nwhat-if only. Re-run with --apply to deploy.")
        return 0

    arm.ok(arm.call("PUT", dep_path, DEPLOY_API, {"properties": props}), "start deployment")
    print(f"\ndeployment {a.deployment_name} started ...")
    while True:
        time.sleep(15)
        dep = arm.ok(arm.call("GET", dep_path, DEPLOY_API), "get deployment")
        state = dep.get("properties", {}).get("provisioningState")
        print(f"  {state}")
        if state in TERMINAL:
            break

    if state != "Succeeded":
        ops = arm.ok(arm.call("GET", f"{dep_path}/operations", DEPLOY_API), "list operations").get("value", [])
        for op in ops:
            p = op.get("properties", {})
            if p.get("provisioningState") == "Failed":
                target = p.get("targetResource", {}).get("resourceName", "?")
                print(f"  FAILED {target}: {json.dumps(p.get('statusMessage'), indent=2)}", file=sys.stderr)
        err = dep.get("properties", {}).get("error")
        if err:
            print(json.dumps(err, indent=2), file=sys.stderr)
        return 1

    print("\noutputs:")
    for k, v in (dep.get("properties", {}).get("outputs") or {}).items():
        print(f"  {k} = {v.get('value')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
