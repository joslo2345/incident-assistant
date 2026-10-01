"""Write JSON Schema files for the contracts, and the OpenAPI spec, to docs/schemas/.

Usage:
    uv run python scripts/export_schemas.py           # write files
    uv run python scripts/export_schemas.py --check   # fail if committed files are stale
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.app import create_app as create_agent_app
from agent.diagnosis import SUBMIT_SPEC
from agent.runtime import Settings as AgentSettings
from agent.tools import TOOLS
from incident_contracts import Incident, TelemetryBatch
from ingest.app import app as ingest_app
from knowledge.app import Settings as KnowledgeSettings
from knowledge.app import create_app as create_knowledge_app

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "schemas"


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _model(model: type[BaseModel]) -> Callable[[], str]:
    return lambda: _dump(model.model_json_schema())


OUTPUTS: dict[str, Callable[[], str]] = {
    "telemetry_batch.schema.json": _model(TelemetryBatch),
    "incident.schema.json": _model(Incident),
    "ingest.openapi.json": lambda: _dump(ingest_app.openapi()),
    # No lifespan runs for openapi(), so no models or database are needed here.
    "knowledge.openapi.json": lambda: _dump(create_knowledge_app(KnowledgeSettings()).openapi()),
    "agent.openapi.json": lambda: _dump(create_agent_app(AgentSettings()).openapi()),
    # The agent's tools as the model (and MCP clients) see them.
    "agent_tools.json": lambda: _dump(
        {
            t.name: {
                "description": t.description,
                "read_only": t.read_only,
                "parameters": t.spec.parameters,
            }
            for t in TOOLS.values()
        }
        | {
            SUBMIT_SPEC.name: {
                "description": SUBMIT_SPEC.description,
                "read_only": True,
                "parameters": SUBMIT_SPEC.parameters,
            }
        }
    ),
}


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for filename, render in OUTPUTS.items():
        path = OUT_DIR / filename
        content = render()
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(filename)
        else:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            print(f"wrote {path.relative_to(Path.cwd())}")
    if stale:
        print(f"stale schemas (run scripts/export_schemas.py): {', '.join(stale)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
