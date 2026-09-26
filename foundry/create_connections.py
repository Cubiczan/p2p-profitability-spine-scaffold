"""Create the Foundry project connections the analyst agent uses (ARM REST, no keys stored).

- Fabric data agent: category CustomKeys with workspace_id / artifact_id
  (https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric)
- Azure AI Search: category CognitiveSearch, authType AAD (project managed identity has
  Search Index Data Reader from infra/azuredeploy.json)
  (https://github.com/microsoft-foundry/foundry-samples/.../01-connections/connection-ai-search.bicep)

Usage:
  python foundry/create_connections.py --subscription <id> --resource-group <rg> --account <foundry account> \
      --project spine --fabric-workspace-id <guid> --fabric-artifact-id <guid> --search-service <name> \
      --client-id 1950a258-227b-4e31-a9cf-717495945fc2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
from fabric_deploy import _interactive_credential  # noqa: E402

ARM = "https://management.azure.com"
API = "2025-04-01-preview"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subscription", required=True)
    ap.add_argument("--resource-group", required=True)
    ap.add_argument("--account", required=True, help="Foundry (AIServices) account name")
    ap.add_argument("--project", default="spine")
    ap.add_argument("--fabric-workspace-id", default="")
    ap.add_argument("--fabric-artifact-id", default="")
    ap.add_argument("--fabric-connection-name", default="fabric-spine-agent")
    ap.add_argument("--search-service", default="")
    ap.add_argument("--search-connection-name", default="spine-evidence-search")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--client-id", default=None)
    a = ap.parse_args()

    cred = _interactive_credential(a.tenant, a.client_id)
    h = {"Authorization": f"Bearer {cred.get_token(ARM + '/.default').token}", "Content-Type": "application/json"}
    base = (f"{ARM}/subscriptions/{a.subscription}/resourceGroups/{a.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{a.account}/projects/{a.project}/connections")

    def put(name: str, props: dict) -> None:
        r = requests.put(f"{base}/{name}?api-version={API}", headers=h, json={"properties": props}, timeout=120)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"{name}: {r.status_code} {r.text}")
        print(f"connection {name}: {r.json().get('id')}")

    if a.fabric_workspace_id and a.fabric_artifact_id:
        put(a.fabric_connection_name, {
            "category": "CustomKeys",
            "authType": "CustomKeys",
            "target": "-",
            "isSharedToAll": True,
            "credentials": {"keys": {"workspace_id": a.fabric_workspace_id, "artifact_id": a.fabric_artifact_id}},
            "metadata": {"type": "fabric_dataagent_preview"},  # value the Foundry portal writes; IDs live in credentials.keys
        })

    if a.search_service:
        sr = requests.get(f"{ARM}/subscriptions/{a.subscription}/resourceGroups/{a.resource_group}/providers/Microsoft.Search/searchServices/{a.search_service}?api-version=2025-05-01", headers=h, timeout=60)
        sr.raise_for_status()
        svc = sr.json()
        put(a.search_connection_name, {
            "category": "CognitiveSearch",
            "authType": "AAD",
            "target": svc["properties"].get("endpoint") or f"https://{a.search_service}.search.windows.net",
            "isSharedToAll": True,
            "metadata": {"ApiType": "Azure", "ResourceId": svc["id"], "location": svc["location"]},
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
