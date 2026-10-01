"""Run a command with the agent's connection settings for the local stack, read from
deploy/.env, so host-side tools never need secrets on the command line or in a config file.

    uv run python scripts/agent_env.py uv run agent investigate <incident-id>
    uv run python scripts/agent_env.py uv run agent mcp      # what `make mcp` runs

MCP clients (Claude Desktop, the MCP Inspector) can launch the server the same way:
    npx @modelcontextprotocol/inspector uv --directory <repo> run python scripts/agent_env.py \\
        uv run agent mcp
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    env = dict(
        line.split("=", 1)
        for line in (REPO / "deploy" / ".env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    db = "@localhost:5432/telemetry"
    os.environ.setdefault(
        "DATABASE_URL", f"postgresql://incident_agent:{env['AGENT_DB_PASSWORD']}{db}"
    )
    os.environ.setdefault(
        "APPROVAL_DATABASE_URL", f"postgresql://approval_service:{env['APPROVAL_DB_PASSWORD']}{db}"
    )
    os.environ.setdefault("KNOWLEDGE_URL", "http://localhost:8001")
    os.environ.setdefault("KNOWLEDGE_API_KEY", env["KNOWLEDGE_API_KEY"])
    os.chdir(REPO)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
