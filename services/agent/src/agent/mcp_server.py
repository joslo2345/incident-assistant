"""The investigation tools as an MCP server, for MCP clients other than our own agent loop
(Claude Desktop, the MCP Inspector, another team's agent).

Built on the low-level `Server` so the tool schemas are exactly the ones the agent loop uses
(the Pydantic argument models in agent.tools), with read-only / destructive hints set. Calls go
through the same validation as in the loop. `drain_node` still only files an approval request,
recorded as requested by "mcp-client".

Run over stdio: `uv run agent mcp`.
"""

from __future__ import annotations

from typing import Any

import mcp_types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext

from agent.tools import TOOLS, Toolbox, ToolContext, call_tool

INSTRUCTIONS = (
    "Tools for investigating incidents in a GPU fleet: incident records, GPU telemetry, XID and "
    "BMC logs, runbook search, similar past incidents and node inventory. All are read-only "
    "except drain_node, which only files a request for a human to approve."
)


def build_server(toolbox: Toolbox, allowed: frozenset[str] | None = None) -> Server[Any]:
    names = sorted(allowed if allowed is not None else TOOLS)
    tools = [
        types.Tool(
            name=TOOLS[n].name,
            description=TOOLS[n].description,
            input_schema=TOOLS[n].spec.parameters,
            annotations=types.ToolAnnotations(
                read_only_hint=TOOLS[n].read_only,
                # drain_node changes nothing by itself, but it starts a state-changing process.
                destructive_hint=not TOOLS[n].read_only,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )
        for n in names
    ]

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        outcome = await call_tool(
            toolbox, params.name, params.arguments or {}, ToolContext(), frozenset(names)
        )
        return types.CallToolResult(
            content=[types.TextContent(text=outcome.content)], is_error=outcome.is_error
        )

    return Server(
        "gpu-incident-tools",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call,
    )


async def serve_stdio(toolbox: Toolbox) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(toolbox)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
