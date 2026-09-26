# Deploying on Microsoft Fabric and Microsoft Foundry

## Architecture

| Layer | Microsoft component | Role |
|---|---|---|
| Source control | GitHub + Fabric Git integration | Notebooks in `fabric/` sync into the workspace |
| Storage | Fabric Lakehouse (OneLake) | `Files/bronze/<dataset>/` holds the data contract; `bronze_*`, `silver_*` and `gold_*` Delta tables |
| Compute | Fabric notebooks (PySpark) | `nb_spine_01_bronze` stores immutable source rows with SHA-256; `nb_spine_02_gold` runs the engine and writes gold tables plus the evidence pack |
| Q&A over data | Fabric Data Agent | NL→SQL over the gold tables |
| Agent | Microsoft Foundry prompt agent + Fabric data agent tool | Diligence analyst with identity passthrough (users only see the data they are permitted to) |
| BI (optional) | Power BI semantic model (Direct Lake) on the gold tables | Dashboards |

## Prerequisites

- A Fabric workspace on a paid F-SKU or trial capacity. The data agent needs F2 or higher, or P1.
- Tenant settings: Fabric data agent enabled, plus cross-geo processing if your capacity requires it.
- A Foundry project with a model deployment, and the **Foundry User** role for you and your end users.
- Data agent, lakehouse and Foundry project in the **same tenant**. The data agent and its lakehouse must be on capacities in the **same region**.

## 1. Deploy to Fabric (script)

```bash
uv run --with azure-identity --with requests python deploy/fabric_deploy.py --workspace "<workspace name>" --data data/example --dataset example --run
```

A browser window opens for sign-in. The script then:
1. Finds the workspace and creates `lh_p2p_spine` if it is missing.
2. Uploads the data contract to `Files/bronze/<dataset>/` in OneLake.
3. Creates or updates `nb_spine_01_bronze` and `nb_spine_02_gold`, injecting the workspace and lakehouse IDs.
4. With `--run`, runs both notebooks and waits for completion.

The gold notebook installs the engine with `%pip` from `ENGINE_SPEC`, a public git tag by default.
If your tenant blocks outbound pip, build a wheel (`python -m pip wheel . -w dist`), attach it to a
Fabric **Environment** as a custom library, attach that environment to the notebook, and clear
`ENGINE_SPEC`.

### Alternative: Git integration

Workspace settings → Git integration → connect to this repo, branch `main`, folder `fabric`.
Sync. Create the lakehouse, upload `data/<dataset>/*` to `Files/bronze/<dataset>/`, set the
notebook parameters, then run.

## 2. Fabric Data Agent (portal, about 5 minutes)

1. Workspace → **New item → Data agent** → name it `p2p-spine-agent`.
2. Add a data source: `lh_p2p_spine`. Select all `gold_*` tables and `silver_throughput_monthly`.
3. **Agent instructions**: paste [fabric/data_agent/instructions.md](../fabric/data_agent/instructions.md).
4. **Example queries**: add the pairs in [fabric/data_agent/example_queries.md](../fabric/data_agent/example_queries.md).
5. Test a question, e.g. "Which questions are contradicted?", then **Publish**.
6. Copy `workspace_id` and `artifact_id` from the URL (`.../groups/<workspace_id>/aiskills/<artifact_id>`).

## 3. Foundry agent

1. In the Foundry portal, open the project → create an agent → add the **Microsoft Fabric data agent**
   tool → enter `workspace_id` and `artifact_id`. Foundry creates the project connection. Note its name.
2. Create the agent from code (versioned, reproducible):

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<resource>.ai.azure.com/api/projects/<project>"
export FOUNDRY_MODEL_DEPLOYMENT="<deployment>"
export FABRIC_CONNECTION_NAME="<connection name>"
export SPINE_COMPANY="ExampleCo Plant A"
uv run --with azure-identity --with "azure-ai-projects>=2.7.0" python foundry/create_agent.py --ask "Which assumptions rest on grade C or D evidence?"
```

`DefaultAzureCredential` picks up `az login`, Visual Studio Code or environment credentials.
Service principals are **not** supported by the Fabric data agent tool, so run the tool as a user.

## 4. Refresh cycle

New evidence (invoice, CoA, contract) → update the data-contract CSV with a `source` → re-run
`fabric_deploy.py --run`, or re-upload and run `nb_spine_02_gold` → the question register re-answers
itself. Questions move from `OPEN_GAP` to `ANSWERED` as their metrics appear.

## Security notes

- Never commit tokens. The deploy script uses interactive sign-in only.
- Keep company data in a **private** repo. This scaffold ships synthetic data only.
- Identity passthrough means a user who cannot read the lakehouse cannot get answers from the agent.
