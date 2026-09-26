"""Create or update a Fabric Data Agent over the spine lakehouse (draft + published).

Definition format: https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition
Instructions come from fabric/data_agent/instructions.md (text after the first '---' line) and
example queries from fabric/data_agent/example_queries.md (**question** followed by a ```sql block).

Usage:
  python deploy/fabric_data_agent.py --workspace "<workspace>" --client-id 1950a258-227b-4e31-a9cf-717495945fc2
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fabric_deploy import Fabric, _interactive_credential  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition"


def _b64(obj: object) -> str:
    return base64.b64encode(json.dumps(obj, indent=2).encode()).decode()


def _instructions(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("\n---\n", 1)[1].strip() if "\n---\n" in text else text.strip()


def _few_shots(path: Path) -> List[Dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    pairs = re.findall(r"\*\*(.+?)\*\*\s*```sql\s*(.+?)```", text, flags=re.S)
    return [{"id": str(uuid.uuid5(uuid.NAMESPACE_URL, q.strip())), "question": q.strip(), "query": sql.strip()} for q, sql in pairs]


def build_definition(ws: str, lh: str, lh_name: str, tables: List[str], instructions: str, shots: List[Dict[str, str]], description: str) -> dict:
    folder = f"lakehouse_tables-{lh_name}"
    datasource = {
        "$schema": "1.0.0",
        "artifactId": lh,
        "workspaceId": ws,
        "displayName": lh_name,
        "type": "lakehouse_tables",
        "userDescription": "Spine gold and silver tables: question register, unit economics, settlements, grading, capacity, evidence, findings.",
        "dataSourceInstructions": "Start with gold_question_register; use gold_metrics for single numbers. Payables are fractions; money USD; mass t.",
        "elements": [
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lh}/dbo")),
                "display_name": "dbo",
                "type": "lakehouse_tables.schema",
                "is_selected": True,
                "children": [
                    {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lh}/{t}")), "display_name": t, "type": "lakehouse_tables.table", "is_selected": True}
                    for t in tables
                ],
            }
        ],
    }
    stage = {"$schema": f"{SCHEMA}/stageConfiguration/1.0.0/schema.json", "aiInstructions": instructions}
    fewshots = {"$schema": f"{SCHEMA}/fewShots/1.0.0/schema.json", "fewShots": shots}
    parts = [("Files/Config/data_agent.json", {"$schema": f"{SCHEMA}/dataAgent/2.1.0/schema.json"})]
    for stage_name in ("draft", "published"):
        parts += [
            (f"Files/Config/{stage_name}/stage_config.json", stage),
            (f"Files/Config/{stage_name}/{folder}/datasource.json", datasource),
            (f"Files/Config/{stage_name}/{folder}/fewshots.json", fewshots),
        ]
    parts.append(("Files/Config/publish_info.json", {"$schema": f"{SCHEMA}/publishInfo/1.0.0/schema.json", "description": description}))
    return {"parts": [{"path": p, "payload": _b64(o), "payloadType": "InlineBase64"} for p, o in parts]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--lakehouse", default="lh_p2p_spine")
    ap.add_argument("--name", default="p2p-spine-agent")
    ap.add_argument("--instructions", type=Path, default=REPO / "fabric" / "data_agent" / "instructions.md")
    ap.add_argument("--examples", type=Path, default=REPO / "fabric" / "data_agent" / "example_queries.md")
    ap.add_argument("--table-prefixes", default="gold_,silver_")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--client-id", default=None)
    a = ap.parse_args()

    fab = Fabric(_interactive_credential(a.tenant, a.client_id))
    r = fab.call("GET", "/workspaces")
    r.raise_for_status()
    ws = next((w for w in r.json()["value"] if w["displayName"] == a.workspace), None)
    if not ws:
        print(f"workspace {a.workspace} not found", file=sys.stderr)
        return 2
    wsid = ws["id"]
    lh = next(i for i in fab.items(wsid, "Lakehouse") if i["displayName"] == a.lakehouse)
    tr = fab.call("GET", f"/workspaces/{wsid}/lakehouses/{lh['id']}/tables")
    tr.raise_for_status()
    prefixes = tuple(p for p in a.table_prefixes.split(",") if p)
    tables = sorted(t["name"] for t in tr.json().get("data", []) if t["name"].startswith(prefixes))
    shots = _few_shots(a.examples)
    print(f"{len(tables)} tables, {len(shots)} example queries")
    definition = build_definition(wsid, lh["id"], a.lakehouse, tables, _instructions(a.instructions), shots, f"{a.name} - spine gold tables")

    existing = next((i for i in fab.items(wsid, "DataAgent") if i["displayName"] == a.name), None)
    if existing:
        fab.lro(fab.call("POST", f"/workspaces/{wsid}/items/{existing['id']}/updateDefinition", {"definition": definition}))
        agent_id = existing["id"]
        print(f"updated data agent {a.name}")
    else:
        res = fab.lro(fab.call("POST", f"/workspaces/{wsid}/items", {"displayName": a.name, "type": "DataAgent", "definition": definition}))
        agent_id = res.get("id") or next(i["id"] for i in fab.items(wsid, "DataAgent") if i["displayName"] == a.name)
        print(f"created data agent {a.name}")
    print(f"workspace_id={wsid}")
    print(f"artifact_id={agent_id}")
    print(f"url=https://app.fabric.microsoft.com/groups/{wsid}/aiskills/{agent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
