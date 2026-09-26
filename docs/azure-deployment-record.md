# Azure deployment record and lessons learned

A reference deployment of this scaffold was run end to end in September 2026. This page records
what was deployed, what broke and how it was fixed, so the next deployment is faster.

## What was deployed (`infra/azuredeploy.json`, one resource group, Southeast Asia)

| Resource | SKU | Purpose | Ongoing cost |
|---|---|---|---|
| Foundry account (`Microsoft.CognitiveServices/accounts`, kind AIServices) + project | S0 | Agent runtime, model host, Document Intelligence endpoint | Pay per use |
| Model deployment | `gpt-5-mini` 2025-08-07, GlobalStandard, 10k TPM | Analyst agent orchestration | Per token |
| Azure AI Search | Basic | Evidence index (page-anchored chunks of the data room) | Hourly (largest fixed cost) |
| Key Vault (RBAC) | Standard | Secrets | Negligible |
| Log Analytics + Application Insights | Pay-as-you-go | Agent tracing | Negligible at this volume |
| Microsoft Fabric capacity (optional) | F2 | Required for the Foundry → Fabric data agent tool | Hourly; can be paused |
| Role assignments | – | Operator and project identity → Search / Key Vault / Foundry | – |

Fabric items (workspace, lakehouse, notebooks, Data Agent) are created by `deploy/fabric_deploy.py`
and `deploy/fabric_data_agent.py`. They live in Fabric, not in the Azure resource group.

## Order of operations

```bash
python infra/deploy_infra.py --client-id <public client> --subscription <id> --resource-group <rg> --location southeastasia            # what-if
python infra/deploy_infra.py ... --apply                                                                                                  # deploy
python deploy/fabric_deploy.py --workspace "<ws>" --create-workspace --data <data dir> --dataset <name> --run --client-id <public client>
python deploy/fabric_data_agent.py --workspace "<ws>" --client-id <public client>
python foundry/create_connections.py --subscription <id> --resource-group <rg> --account <foundry account> --search-service <search> --client-id <public client>
# Fabric data agent connection: add it in the Foundry portal (Build > Tools > Connect a tool > Fabric Data Agent)
python search/build_index.py --root <data room> --endpoint <search endpoint> --index <name> --auth interactive --client-id <public client> --docintel-endpoint <ai services endpoint>
FABRIC_CONNECTION_NAME=<portal connection> python foundry/create_agent.py --auth interactive --client-id <public client> --ask "..."
python infra/teardown.py --subscription <id> --resource-group <rg> --fallback-capacity-id <trial capacity> [--confirm <rg>]
```

## Issues hit and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Sign-in fails: *"Need admin approval"* / `access_denied` | Tenant blocks the default public client app | `--client-id 1950a258-227b-4e31-a9cf-717495945fc2` (Microsoft Azure PowerShell first-party client) |
| A sign-in prompt on every script | No shared token cache | All scripts now use an encrypted OS token cache plus a saved account record (`~/.p2p-spine/`) |
| `InsufficientQuota ... gpt-5.4-mini ... quota limit is 0` | New subscription with zero quota for that model | List quota with the `locations/<region>/usages` API; template default switched to `gpt-5-mini` (quota available) |
| Foundry: *"Workspace ID and Artifact ID are required from connection details"* | Connection created through ARM with a metadata type the tool does not read | Create the Fabric connection in the Foundry portal (metadata `type = fabric_dataagent_preview`); `create_connections.py` updated to match |
| Foundry: *"FTL64 SKU Not Supported"* | Fabric **trial** capacity is not supported by the Foundry → Fabric data agent tool | Deploy an F2 (`deployFabricCapacity=true`) and move the workspace to it |
| Foundry: *"DisallowedForCrossGeo"* | Capacity region lacks Azure OpenAI and the tenant blocks cross-geo processing | Capacity-level delegated setting "Users can use Copilot, AI Agents…" plus the **tenant** setting allowing Azure OpenAI processing outside the region (tenant admin only) |
| Fabric Data Agent shows *"No tables selected"* after API create | Table selection in the definition not applied | Tick the lakehouse in the Data Agent explorer and **Publish** |
| `429 rate_limit_exceeded` on agent answers | 10k TPM too small for 5 retrieved chunks per question | Deploy with `modelCapacity=50` (template parameter) |
| Index upload timed out after 300 s | Indexer used its own credential and opened an unseen prompt | Indexer now uses the shared token cache |

## Teardown

`infra/teardown.py` moves Fabric workspaces off the resource group's F-SKU onto a fallback
capacity first (otherwise they are orphaned), then deletes the resource group. The Foundry account
and Key Vault go to soft-delete (no charge; names stay reserved). Redeploying takes about 10 minutes.
