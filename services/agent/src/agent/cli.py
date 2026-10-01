"""Agent CLI.

    agent investigate <incident-id>          # run one investigation, print the result as JSON
    agent investigate --detector-run ev3-v3  # every actionable incident of a detector run
    agent mcp                                # serve the tools over MCP (stdio)
    agent approvals [--status pending]       # list approval requests
    agent decide <request-id> approve|reject --by alice [--note ...]   # needs APPROVAL_DATABASE_URL

Connection settings come from the environment (DATABASE_URL, KNOWLEDGE_URL, KNOWLEDGE_API_KEY,
AGENT_PROVIDER, AGENT_MODEL, ...); see agent.runtime.Settings and agent.llm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from agent.app import to_response
from agent.runtime import Runtime, open_runtime


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


async def _investigate(rt: Runtime, args: argparse.Namespace) -> int:
    if args.incident_id:
        ids = [args.incident_id]
    else:
        ids = [
            r["incident_id"]
            for r in await rt.pool.fetch(
                "SELECT incident_id FROM incidents WHERE detector_run = $1 "
                "AND severity <> 'sev4' ORDER BY opened_at",
                args.detector_run,
            )
        ]
    failures = 0
    for incident_id in ids:
        incident = await rt.data.incident(incident_id)
        if incident is None:
            print(f"no incident {incident_id}", file=sys.stderr)
            return 1
        result = await rt.investigator().investigate(incident)
        failures += result.run.status != "succeeded"
        _print(to_response(result).model_dump(mode="json"))
    return 1 if failures else 0


async def _main(args: argparse.Namespace) -> int:
    if args.command == "mcp":
        from agent.mcp_server import serve_stdio

        # stdout is the MCP channel; keep logs on stderr.
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        async with open_runtime() as rt:
            await serve_stdio(rt.toolbox)
        return 0
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    async with open_runtime() as rt:
        if args.command == "investigate":
            return await _investigate(rt, args)
        if args.command == "approvals":
            _print(await rt.approvals.list(args.status, args.limit))
            return 0
        if args.command == "decide":
            if rt.decider is None:
                print("set APPROVAL_DATABASE_URL (approval_service role)", file=sys.stderr)
                return 2
            _print(await rt.decider.decide(args.request_id, args.decision == "approve",
                                           args.by, args.note))  # fmt: skip
            return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("investigate", help="Investigate an incident now")
    target = inv.add_mutually_exclusive_group(required=True)
    target.add_argument("incident_id", nargs="?", type=uuid.UUID)
    target.add_argument("--detector-run", help="Every actionable incident of this detector run")
    sub.add_parser("mcp", help="Serve the investigation tools over MCP (stdio)")
    ap = sub.add_parser("approvals", help="List approval requests")
    ap.add_argument("--status", choices=["pending", "approved", "rejected"])
    ap.add_argument("--limit", type=int, default=50)
    de = sub.add_parser("decide", help="Approve or reject a request (as a person)")
    de.add_argument("request_id", type=uuid.UUID)
    de.add_argument("decision", choices=["approve", "reject"])
    de.add_argument("--by", required=True, help="Who is deciding")
    de.add_argument("--note")
    return p


def main() -> None:
    sys.exit(asyncio.run(_main(build_parser().parse_args())))
