# Azure infrastructure

`azuredeploy.json` is a single ARM template for the Azure side of the spine. The Fabric side
(workspace, lakehouse, notebooks) is deployed by [`deploy/fabric_deploy.py`](../deploy/fabric_deploy.py).
`deploy_infra.py` deploys the template through the ARM REST API. You do not need the `az` or
`bicep` CLI.

## What gets deployed

| Resource | Type | Notes |
|---|---|---|
| Log Analytics workspace | `Microsoft.OperationalInsights/workspaces` | PerGB2018, 30-day retention (parameter) |
| Application Insights | `Microsoft.Insights/components` | Workspace-based (`IngestionMode = LogAnalytics`) |
| Key Vault | `Microsoft.KeyVault/vaults` | RBAC authorization, soft delete (90 days), purge protection controlled by a parameter (off by default, **irreversible** once on) |
| Foundry resource | `Microsoft.CognitiveServices/accounts`, kind `AIServices` | `allowProjectManagement: true`, custom subdomain, system identity, Entra-only by default (`disableLocalAuth`). The same account serves Azure OpenAI and Document Intelligence (`<endpoint>/documentintelligence/...`). |
| Foundry project | `Microsoft.CognitiveServices/accounts/projects` | System identity |
| Model deployment | `Microsoft.CognitiveServices/accounts/deployments` | Default `gpt-5.4-mini` version `2026-03-17`, `GlobalStandard`, capacity 10 (10K TPM) |
| Azure AI Search | `Microsoft.Search/searchServices` | `basic`, 1 replica / 1 partition, system identity, accepts Entra ID or API key |
| Fabric capacity (optional) | `Microsoft.Fabric/capacities` | Created only when `deployFabricCapacity = true`, default `F2` |
| Role assignments | `Microsoft.Authorization/roleAssignments` | See below |

Resource names are `<namePrefix>-<kind>-<6-char hash of the RG id>`, for example
`p2pspine-ais-abc123`. The Fabric capacity name has no hyphens (`p2pspinefababc123`) because
capacity names must match `^[a-z][a-z0-9]*$`.

### Role assignments

| Principal | Role | Scope | Role definition id |
|---|---|---|---|
| Foundry project identity | Search Index Data Reader | Search service | `1407120a-92aa-4202-b7e9-c0e197c71c8f` |
| Foundry project identity | Search Service Contributor | Search service | `7ca78c08-252a-4471-8644-bb5ff32d4ba0` |
| Foundry account identity | Search Index Data Reader | Search service | `1407120a-92aa-4202-b7e9-c0e197c71c8f` |
| Foundry account identity | Search Service Contributor | Search service | `7ca78c08-252a-4471-8644-bb5ff32d4ba0` |
| `principalId` (operator, optional) | Key Vault Secrets Officer | Key Vault | `b86a8fe4-44ce-4948-aee5-eccb2c155cd7` |
| `principalId` (operator, optional) | Search Index Data Contributor | Search service | `8ebe5a00-799e-43f5-93ac-243d3dce84a7` |
| `principalId` (operator, optional) | Foundry User (formerly "Azure AI User") | Foundry account, inherited by projects | `53ca6127-db72-4b80-b1b0-d745d6d5456d` |

Assignment names are `guid(scope, principal, role)`, so re-deploying the template does not
create duplicate assignments. If you do not pass `--principal-id`, `deploy_infra.py` reads the
signed-in user's object id from the token's `oid` claim. To skip the operator grants, pass
`--no-principal`.

The account granting these roles needs `Microsoft.Authorization/roleAssignments/write`, for
example Owner or User Access Administrator on the resource group. Contributor alone is not
enough, and the role assignments will fail.

### Outputs

`foundryProjectEndpoint` (`https://<account>.services.ai.azure.com/api/projects/<project>`),
`aiServicesEndpoint`, `aiServicesName`, `modelDeploymentName`, `searchEndpoint`,
`keyVaultUri`, `appInsightsConnectionString`, `fabricCapacityName`.

Microsoft does not treat the Application Insights connection string as an authentication
secret: it identifies the telemetry target, and anyone holding it can only send telemetry
([Connection strings in Application Insights](https://learn.microsoft.com/en-us/azure/azure-monitor/app/connection-strings)).
The script prints it with the other outputs. If you want to restrict ingestion, set
`DisableLocalAuth` on the component.

## Cost notes

- **Azure AI Search `basic`** is billed per hour from the moment it is created and cannot be
  paused. Delete it when you don't need it, or pass `--set searchSku=free` (one free service
  per subscription, which has limits).
- **Fabric F2** (optional) is billed per second while it is **Active**. Pause it in the Azure
  portal (Fabric capacity → **Pause**) when it is idle, and resume it before running
  notebooks. For evaluation, a Fabric trial capacity costs nothing. The template does not
  create a capacity unless you pass `deployFabricCapacity=true`.
- **Model deployment (GlobalStandard)** is pay-per-token. You pay nothing while it is idle, but
  capacity consumes quota.
- **Log Analytics / Application Insights** are billed per GB ingested and are cheap at spine volumes.
- **Key Vault** is billed per operation and costs close to nothing.
- **Foundry account and project** have no standing charge. You pay for the models and tools you
  use.

## Prerequisites

1. An Azure subscription in the same Entra tenant as your Fabric tenant. You need Owner, or
   Contributor plus User Access Administrator, on the target resource group or subscription.
2. Resource providers registered on the subscription: `Microsoft.CognitiveServices`,
   `Microsoft.Search`, `Microsoft.KeyVault`, `Microsoft.OperationalInsights`,
   `Microsoft.Insights`, and `Microsoft.Fabric` (only if you deploy a capacity). To register
   them, go to Azure portal → Subscription → **Resource providers** → **Register**. Many
   subscriptions register these automatically on the first deployment.
3. Model quota for the chosen model and SKU in the region (Foundry portal → **Quota**). If
   `gpt-5.4-mini` is not available to you, override it:
   `--set modelName=gpt-4.1-mini --set modelVersion=2025-04-14 --set modelDeploymentName=gpt-4.1-mini`.
4. Python 3.10+ and [uv](https://docs.astral.sh/uv/). You do not need to install anything
   globally.

## Usage

```bash
# 1. See which subscriptions you can use (the browser sign-in opens)
uv run --no-project --with azure-identity --with requests python infra/deploy_infra.py

# 2. What-if only (creates the empty resource group if missing, deploys nothing else)
uv run --no-project --with azure-identity --with requests python infra/deploy_infra.py \
    --subscription "<subscription name or id>" --resource-group rg-p2p-spine --location southeastasia

# 3. Deploy
uv run --no-project --with azure-identity --with requests python infra/deploy_infra.py \
    --subscription "<subscription name or id>" --resource-group rg-p2p-spine --location southeastasia --apply

# Optional: with a parameters file and a Fabric F2 capacity
cp infra/azuredeploy.parameters.example.json infra/azuredeploy.parameters.json   # edit admins
uv run --no-project --with azure-identity --with requests python infra/deploy_infra.py \
    --subscription "<...>" --resource-group rg-p2p-spine \
    --parameters infra/azuredeploy.parameters.json --set deployFabricCapacity=true --apply
```

Options: `--tenant <tenant id>` and `--client-id <public client id>` work the same way as in
`fabric_deploy.py`. If the default public client is blocked, see
[docs/microsoft-deployment.md](../docs/microsoft-deployment.md). Use `--auth default` for
DefaultAzureCredential, for example in CI. With `--set KEY=VALUE`, VALUE is parsed as JSON,
so `true`, `10` and `["a@b.com"]` work.

After deployment, feed the outputs to the Foundry agent script:

```bash
export FOUNDRY_PROJECT_ENDPOINT="<foundryProjectEndpoint>"
export FOUNDRY_MODEL_DEPLOYMENT="<modelDeploymentName>"
```

Then continue with section 3 of [docs/microsoft-deployment.md](../docs/microsoft-deployment.md).
To attach the workspace to the new capacity, go to Fabric workspace settings → **License
info** → select the capacity. Alternatively, run `fabric_deploy.py --create-workspace
--capacity-id <id>`.

## GitHub OIDC app for Fabric deploys (manual, Entra)

ARM templates cannot create Entra ID objects (app registrations, federated credentials), so
you create the app once in the portal. The workflow
[`.github/workflows/fabric-deploy.yml`](../.github/workflows/fabric-deploy.yml) uses the app
with `azure/login@v2` and OIDC, so no client secret is involved.

1. **Register the app.** Open the [Microsoft Entra admin center](https://entra.microsoft.com) →
   **Entra ID** → **App registrations** → **New registration**. Set Name to
   `gh-p2p-spine-fabric-deploy`, set Supported account types to **Accounts in this
   organizational directory only**, and leave Redirect URI empty. Select **Register**.
2. **Copy the ids.** On **Overview**, copy the **Application (client) ID** and the **Directory
   (tenant) ID**.
3. **Add the federated credential.** In the app, go to **Certificates & secrets** →
   **Federated credentials** tab → **Add credential**.
   - Federated credential scenario: **GitHub Actions deploying Azure resources**
   - Organization: `<your GitHub org or user>`. Repository: `<repo name>`.
   - Entity type: **Branch**. GitHub branch name: `main`.
   - Name: `github-main`
   - Check the auto-filled values. Issuer should be `https://token.actions.githubusercontent.com`,
     Audience `api://AzureADTokenExchange`, and Subject
     `repo:<org>/<repo>:ref:refs/heads/main`. Matching is exact and case-sensitive.
   - Select **Add**.

   Manual `workflow_dispatch` runs use the same credential as long as you run them from
   `main`. Runs from other branches need their own credential.
4. **Add GitHub secrets.** In the repo, go to **Settings** → **Secrets and variables** →
   **Actions**:
   - Secret `AZURE_CLIENT_ID` = Application (client) ID
   - Secret `AZURE_TENANT_ID` = Directory (tenant) ID
   - Variable `FABRIC_WORKSPACE` = target workspace display name. Push-triggered runs are
     skipped until this variable is set.
5. **Allow service principals in Fabric.** This needs a Fabric administrator. Go to the
   Fabric **Admin portal** → **Tenant settings** → **Developer settings** → enable **Service
   principals can call Fabric public APIs**. Restrict it to a security group that contains the
   app's service principal. If you want the workflow to create workspaces, also enable
   **Service principals can create workspaces, connections, and deployment pipelines**.
6. **Add the service principal to the workspace.** In the Fabric workspace, go to **Manage
   access** → **Add people or groups** → search for the app name → role **Contributor** (or
   Admin) → **Add**.
7. Run the workflow from **Actions** → **fabric-deploy** → **Run workflow**.

No Azure subscription is needed for the Fabric workflow (`allow-no-subscriptions: true`). The
Fabric data agent tool in Foundry does not support service principals. Create the Foundry
agent as a user (see `foundry/create_agent.py`).

## Verified API versions and sources

The resource API versions below are GA. None of them is a preview version: the Foundry
`accounts/projects` child has been GA since `2025-06-01`. I checked each against the Microsoft
Learn template reference on 2026-09-26.

| Resource type | apiVersion | Newer versions listed (not used) | Reference |
|---|---|---|---|
| `Microsoft.OperationalInsights/workspaces` | `2025-02-01` | 2025-07-01, 2026-03-01 | https://learn.microsoft.com/en-us/azure/templates/microsoft.operationalinsights/workspaces |
| `Microsoft.Insights/components` | `2020-02-02` | latest GA | https://learn.microsoft.com/en-us/azure/templates/microsoft.insights/components |
| `Microsoft.KeyVault/vaults` | `2025-05-01` | 2026-02-01 (GA), 2026-03-01-preview | https://learn.microsoft.com/en-us/azure/templates/microsoft.keyvault/vaults |
| `Microsoft.CognitiveServices/accounts` | `2025-06-01` | up to 2026-07-01 (GA) | https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/accounts |
| `Microsoft.CognitiveServices/accounts/projects` | `2025-06-01` | up to 2026-07-01 (GA) | https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/accounts/projects |
| `Microsoft.CognitiveServices/accounts/deployments` | `2025-06-01` | up to 2026-07-01 (GA) | https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/accounts/deployments |
| `Microsoft.Search/searchServices` | `2025-05-01` | latest GA, newer are preview | https://learn.microsoft.com/en-us/azure/templates/microsoft.search/2025-05-01/searchservices |
| `Microsoft.Fabric/capacities` | `2023-11-01` | latest GA, newer are preview | https://learn.microsoft.com/en-us/azure/templates/microsoft.fabric/capacities |
| `Microsoft.Authorization/roleAssignments` | `2022-04-01` | latest GA | https://learn.microsoft.com/en-us/azure/templates/microsoft.authorization/roleassignments |

`2025-06-01` is used for all Cognitive Services types because it is the first GA version with
`allowProjectManagement` and `accounts/projects`, and Microsoft's Foundry samples use it. You
can move to a newer GA version without changing the template shape.

Other sources:

- ARM REST API used by `deploy_infra.py`: [Deployments - What If](https://learn.microsoft.com/en-us/rest/api/resources/deployments/what-if) and [Deployments - Create Or Update](https://learn.microsoft.com/en-us/rest/api/resources/deployments/create-or-update) (`api-version=2025-04-01`). Subscriptions are listed with `2022-12-01` and resource groups use `2021-04-01`.
- Role definition ids: [Azure built-in roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles) and [Security roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/security) (Key Vault Secrets Officer). Foundry User (`53ca6127-...`) is the renamed "Azure AI User". Assign it by id, not by name.
- Model availability: [Region availability for Foundry Models sold by Azure](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability). `southeastasia` Global Standard lists gpt-4.1 / gpt-4.1-mini (2025-04-14), gpt-5 / gpt-5-mini (2025-08-07), gpt-5.1, gpt-5.2, gpt-5.4 / gpt-5.4-mini (2026-03-17), gpt-5.5 and gpt-5.6-*. gpt-4o is **not** listed there for Global Standard.
- GitHub OIDC: [Create a trust relationship (federated credential)](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust), [Azure/login](https://github.com/Azure/login).
- Fabric tenant settings: [Developer admin settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer).
