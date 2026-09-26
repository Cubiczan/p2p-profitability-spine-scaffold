"""Create (or version) a Microsoft Foundry prompt agent wired to the Fabric data agent.

Prerequisites (see docs/microsoft-deployment.md):
  1. Fabric data agent published over the spine lakehouse.
  2. In the Foundry portal, add the "Microsoft Fabric data agent" tool once so Foundry creates
     the project connection; note its connection name.

Environment:
  FOUNDRY_PROJECT_ENDPOINT   https://<resource>.ai.azure.com/api/projects/<project>
  FOUNDRY_MODEL_DEPLOYMENT   model deployment name in the project
  FABRIC_CONNECTION_NAME     project connection name for the Fabric data agent
  SPINE_COMPANY              display name used in the instructions

Optional evidence search (both must be set; otherwise the agent is Fabric-only as before):
  AI_SEARCH_CONNECTION_NAME  project connection name for the Azure AI Search resource
  AI_SEARCH_INDEX_NAME       index built by search/build_index.py
  AI_SEARCH_QUERY_TYPE       simple | semantic (default semantic; needs semantic ranker enabled)
  AI_SEARCH_TOP_K            chunks returned per query (default 5)

Run:
  uv run --with azure-identity --with "azure-ai-projects>=2.7.0" python foundry/create_agent.py --ask "What is the plant's real capacity?"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AISearchIndexResource,
    AzureAISearchTool,
    AzureAISearchToolResource,
    FabricDataAgentToolParameters,
    MicrosoftFabricPreviewTool,
    PromptAgentDefinition,
    ToolProjectConnection,
)
from azure.identity import DefaultAzureCredential

HERE = Path(__file__).resolve().parent


def search_tool(project: AIProjectClient) -> AzureAISearchTool | None:
    """Azure AI Search tool over the evidence index, or None when not configured.

    Class names and fields verified against azure-ai-projects 2.7.0 and the SDK sample
    samples/agents/tools/sample_agent_ai_search.py.
    """
    conn_name = os.environ.get("AI_SEARCH_CONNECTION_NAME", "").strip()
    index_name = os.environ.get("AI_SEARCH_INDEX_NAME", "").strip()
    if not (conn_name and index_name):
        return None
    conn = project.connections.get(conn_name)
    return AzureAISearchTool(
        azure_ai_search=AzureAISearchToolResource(
            indexes=[
                AISearchIndexResource(
                    project_connection_id=conn.id,
                    index_name=index_name,
                    query_type=os.environ.get("AI_SEARCH_QUERY_TYPE", "semantic"),
                    top_k=int(os.environ.get("AI_SEARCH_TOP_K", "5")),
                )
            ]
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="p2p-spine-analyst")
    ap.add_argument("--instructions", type=Path, default=HERE / "agent_instructions.md")
    ap.add_argument("--ask", default="", help="Optional smoke-test question")
    a = ap.parse_args()

    project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=DefaultAzureCredential())
    fabric_conn = project.connections.get(os.environ["FABRIC_CONNECTION_NAME"])
    instructions = a.instructions.read_text(encoding="utf-8").replace("{company}", os.environ.get("SPINE_COMPANY", "the plant"))

    tools: list = [
        MicrosoftFabricPreviewTool(
            fabric_dataagent_preview=FabricDataAgentToolParameters(project_connections=[ToolProjectConnection(project_connection_id=fabric_conn.id)])
        )
    ]
    search = search_tool(project)
    if search is not None:
        tools.append(search)
    print("tools: " + ", ".join(t.type for t in tools))

    agent = project.agents.create_version(
        agent_name=a.name,
        definition=PromptAgentDefinition(
            model=os.environ["FOUNDRY_MODEL_DEPLOYMENT"],
            instructions=instructions,
            tools=tools,
        ),
    )
    print(f"agent {agent.name} version {agent.version} (id {agent.id})")

    if a.ask:
        openai = project.get_openai_client(agent_name=agent.name)
        response = openai.responses.create(tool_choice="required", input=a.ask)
        print(response.output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
