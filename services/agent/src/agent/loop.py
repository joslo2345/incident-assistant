"""The investigation loop: give the model the incident, let it call tools, stop on a valid
diagnosis or when a budget runs out.

Guardrails:
- Tool allowlist: only the tools offered to this run can be called (anything else is an error).
- Budgets: model calls (steps), total tokens, and invalid diagnosis submissions. Near the step or
  token limit the model is offered only `submit_diagnosis` and told to finish.
- Timeouts: per model call, per tool call, and for the whole run.
- Output validation: schema plus run-level checks (diagnosis.check), with the problems sent back.
- A state-changing recommendation (drain, GPU reset) becomes an approval request, never an action.

Every model and tool call is written to the trace store as it happens, so a run that crashes or
times out still leaves a complete trace up to that point.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from agent.data import Approvals
from agent.diagnosis import SUBMIT, SUBMIT_SPEC, check
from agent.llm import Provider, ProviderError, ToolResult, ToolSpec, Turn
from agent.tools import TOOLS, Toolbox, ToolContext, call_tool
from agent.trace import RunSummary, Step, TraceStore
from incident_contracts import ActionType, Diagnosis, Incident

log = logging.getLogger("agent.loop")

SYSTEM_PROMPT = """\
You are an on-call engineer investigating an incident in a GPU datacenter (NVIDIA H100 and A100 \
nodes, 8 GPUs each). A rule-based detector opened the incident; its suspected failure type is a \
hypothesis for you to confirm or reject, not a conclusion.

How to investigate:
1. Look at the telemetry around the incident window with get_metrics: the affected GPU's time \
series, and the node summary to compare it with its neighbours.
2. Read the node's XID and BMC log entries for the same window with get_logs.
3. Search the runbooks for the failure you suspect with search_runbooks, and check \
find_similar_incidents when it would help.
4. Submit your diagnosis with submit_diagnosis.

Be efficient: a typical investigation needs 4 to 7 tool calls. Ask for narrow time windows.

Judgement:
- Base the root cause on what you observed, and say what you observed in the summary.
- A heavy job can make GPUs hot and power-hungry. Without hardware errors (XID, ECC, PCIe or \
NVLink errors, BMC faults) or temperatures beyond the slowdown limit, that is noisy_neighbor, not \
a fault.
- Data can be missing: BMC logs are sometimes not collected, and a GPU that falls off the bus \
stops reporting. Reason from what is present and lower your confidence when key data is absent.
- Use unknown when the evidence does not support a cause.
- Citations must be chunk_ids that a tool returned in this investigation. Cite the runbook \
section that supports your recommended action.
- Recommend drain_node or reset_gpu only when the evidence shows the hardware is failing; both go \
to a human for approval. Prefer monitor or none for workload behaviour.
"""


# Identifies the prompt a run used, so eval reports can tell prompt versions apart.
PROMPT_SHA = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Budget:
    max_steps: int = 12  # model calls
    max_tokens: int = 200_000  # input + output, summed over the run
    # Prompt size of a single call. A local model's context is fixed (32k for qwen3-agent) and
    # Ollama drops the oldest tokens past it without an error, so wrap up well before that.
    max_context_tokens: int = 26_000
    max_invalid_submissions: int = 2
    model_timeout_s: float = 300.0
    tool_timeout_s: float = 20.0
    run_timeout_s: float = 1200.0
    wrap_up_at_fraction: float = 0.8  # of max_tokens: from here on, only submit_diagnosis


@dataclass
class RunResult:
    run: RunSummary
    diagnosis: Diagnosis | None
    approval_request: dict[str, Any] | None = None
    trace: list[Step] = field(default_factory=list)


def render_incident(incident: Incident, detector_run: str | None = None) -> str:
    start = min(e.window_start for e in incident.evidence)
    end = max(e.window_end for e in incident.evidence)
    gpus = sorted({c.gpu_index for c in incident.components if c.gpu_index is not None})
    lines = [
        f"Incident {incident.incident_id}: {incident.title}",
        f"Severity: {incident.severity.value}. Detector's suspected failure type: "
        f"{incident.suspected_failure_type.value}.",
        f"Node: {incident.components[0].node_id}. GPUs flagged: {gpus or 'none (node-level)'}.",
        f"Signals seen between {_iso(start)} and {_iso(end)}. Suggested window for queries: "
        f"{_iso(start - timedelta(minutes=30))} to {_iso(end + timedelta(minutes=15))}.",
        "",
        "Evidence:",
    ]
    for e in incident.evidence:
        where = f"GPU {e.component.gpu_index}" if e.component.gpu_index is not None else "node"
        value = "" if e.observed_value is None else f" value={e.observed_value:g}"
        limit = "" if e.threshold is None else f" threshold={e.threshold:g}"
        lines.append(
            f"- {e.evidence_id} at {_iso(e.window_start)} ({where}, {e.detector.value}, "
            f"{e.signal}{value}{limit}): {e.description}"
        )
    lines += ["", "Investigate and submit your diagnosis."]
    return "\n".join(lines)


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Investigator:
    def __init__(
        self,
        provider: Provider,
        toolbox: Toolbox,
        approvals: Approvals,
        traces: TraceStore,
        budget: Budget | None = None,
        tools: frozenset[str] | None = None,
        labels: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.toolbox = toolbox
        self.approvals = approvals
        self.traces = traces
        self.budget = budget or Budget()
        self.labels = labels or {}  # stored with each run, e.g. an eval round and case
        self.allowed = tools if tools is not None else frozenset(TOOLS)
        if unknown := self.allowed - set(TOOLS):
            raise ValueError(f"unknown tools in allowlist: {sorted(unknown)}")

    async def investigate(self, incident: Incident) -> RunResult:
        run = RunSummary(
            run_id=uuid.uuid4(),
            incident_id=incident.incident_id,
            provider=self.provider.name,
            model=self.provider.model,
            started_at=datetime.now(UTC),
            config={
                "budget": asdict(self.budget),
                "tools": sorted(self.allowed),
                "prompt_sha": PROMPT_SHA,
                **self.labels,
            },
        )
        await self.traces.start(run)
        state = _RunState(run, incident, ToolContext(f"agent:{run.run_id}", incident.incident_id,
                                                     run.run_id))  # fmt: skip
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._loop(state), self.budget.run_timeout_s)
        except TimeoutError:
            self._fail(state, "timeout", f"run exceeded {self.budget.run_timeout_s:g} s")
        except ProviderError as exc:
            self._fail(state, "error", f"model unavailable: {exc}")
        except Exception as exc:
            log.exception("investigation %s crashed", run.run_id)
            self._fail(state, "error", f"{type(exc).__name__}: {exc}")
        run.latency_ms = int((time.perf_counter() - started) * 1000)

        request = None
        if state.diagnosis is not None:
            run.status = "succeeded"
            run.diagnosis = state.diagnosis.model_dump(mode="json")
            request = await self._file_approval(state.diagnosis, incident, run)
        await self.traces.finish(run, datetime.now(UTC))
        return RunResult(run, state.diagnosis, request, state.steps)

    async def _loop(self, s: _RunState) -> None:
        session = self.provider.start(SYSTEM_PROMPT, render_incident(s.incident))
        tools = [TOOLS[n].spec for n in sorted(self.allowed)] + [SUBMIT_SPEC]
        b = self.budget
        wrapping_up = False
        while s.diagnosis is None:
            if s.run.steps >= b.max_steps:
                self._fail(s, "budget_exceeded", f"no valid diagnosis after {b.max_steps} steps")
                return
            if s.tokens >= b.max_tokens:
                self._fail(s, "budget_exceeded", f"token budget of {b.max_tokens} used up")
                return
            last_chance = (
                s.run.steps == b.max_steps - 1
                or s.tokens >= b.wrap_up_at_fraction * b.max_tokens
                or s.last_input_tokens >= b.max_context_tokens
            )
            if last_chance and not wrapping_up:
                wrapping_up = True
                session.add_user(
                    "Budget almost used up. Call submit_diagnosis now with what you have; lower "
                    "your confidence if the investigation is incomplete."
                )
            offered = [SUBMIT_SPEC] if wrapping_up else tools
            turn = await self._model_call(s, session.next(offered), offered)
            if turn.stop_reason == "refusal":
                self._fail(s, "refused", "the model declined the request")
                return
            if not turn.tool_calls:
                s.nudges += 1
                if s.nudges > 2:
                    self._fail(s, "invalid_output", "the model stopped calling tools")
                    return
                session.add_user(
                    "Continue the investigation with the tools, or call submit_diagnosis."
                )
                continue
            results = []
            for call in turn.tool_calls:
                if call.name == SUBMIT and s.diagnosis is None:
                    results.append(await self._submit(s, call.id, call.arguments, turn.model))
                elif call.name == SUBMIT:
                    results.append(ToolResult(call.id, call.name, "Already accepted.", False))
                elif wrapping_up:
                    results.append(
                        ToolResult(call.id, call.name, "Error: only submit_diagnosis now.", True)
                    )
                else:
                    results.append(await self._tool_call(s, call.id, call.name, call.arguments))
            session.add_tool_results(results)
            if s.invalid > b.max_invalid_submissions:
                self._fail(s, "invalid_output", f"{s.invalid} invalid diagnosis submissions")
                return

    async def _model_call(self, s: _RunState, pending: Any, offered: list[ToolSpec]) -> Turn:
        started_at, t0 = datetime.now(UTC), time.perf_counter()
        try:
            turn: Turn = await asyncio.wait_for(pending, self.budget.model_timeout_s)
        except TimeoutError as exc:
            raise ProviderError(f"model call exceeded {self.budget.model_timeout_s:g} s") from exc
        latency = int((time.perf_counter() - t0) * 1000)
        s.run.steps += 1
        s.run.input_tokens += (
            turn.usage.input_tokens + turn.usage.cache_read_tokens + (turn.usage.cache_write_tokens)
        )
        s.run.output_tokens += turn.usage.output_tokens
        s.last_input_tokens = turn.usage.input_tokens + turn.usage.cache_read_tokens
        s.run.cost_usd += self.provider.price.cost(turn.usage)
        await self._record(
            s,
            Step(
                seq=len(s.steps), kind="model", name=turn.model, started_at=started_at,
                latency_ms=latency, input={"tools_offered": [t.name for t in offered]},
                output={
                    "text": turn.text,
                    "reasoning": turn.reasoning,
                    "tool_calls": [
                        {"id": c.id, "name": c.name, "arguments": c.arguments
                         if c.arguments is not None else c.raw_arguments}
                        for c in turn.tool_calls
                    ],
                    "stop_reason": turn.stop_reason,
                    "usage": asdict(turn.usage),
                },
                input_tokens=turn.usage.input_tokens, output_tokens=turn.usage.output_tokens,
            ),
        )  # fmt: skip
        return turn

    async def _tool_call(
        self, s: _RunState, call_id: str, name: str, arguments: dict[str, Any] | None
    ) -> ToolResult:
        started_at, t0 = datetime.now(UTC), time.perf_counter()
        try:
            outcome = await asyncio.wait_for(
                call_tool(self.toolbox, name, arguments, s.ctx, self.allowed),
                self.budget.tool_timeout_s,
            )
            content, is_error = outcome.content, outcome.is_error
        except TimeoutError:
            content, is_error = f"Error: {name} timed out; try a narrower query.", True
        except Exception as exc:  # a tool bug or an outage shouldn't end the investigation
            log.warning("tool %s failed: %s", name, exc)
            content, is_error = (
                f"Error: {name} failed ({type(exc).__name__}); try another tool.",
                True,
            )
        s.run.tool_calls += 1
        await self._record(
            s,
            Step(
                seq=len(s.steps), kind="tool", name=name, started_at=started_at,
                latency_ms=int((time.perf_counter() - t0) * 1000), input=arguments,
                output=content, is_error=is_error,
            ),
        )  # fmt: skip
        return ToolResult(call_id, name, content, is_error)

    async def _submit(
        self, s: _RunState, call_id: str, arguments: dict[str, Any] | None, model: str
    ) -> ToolResult:
        diagnosis, problems = check(arguments, s.incident, s.ctx, model, s.run.run_id)
        await self._record(
            s,
            Step(
                seq=len(s.steps), kind="tool", name=SUBMIT, started_at=datetime.now(UTC),
                latency_ms=0, input=arguments, output={"problems": problems},
                is_error=bool(problems),
            ),
        )  # fmt: skip
        if diagnosis is None:
            s.invalid += 1
            return ToolResult(
                call_id, SUBMIT,
                "Error: diagnosis rejected. Fix these and call submit_diagnosis again:\n- "
                + "\n- ".join(problems),
                True,
            )  # fmt: skip
        s.diagnosis = diagnosis
        return ToolResult(call_id, SUBMIT, "Diagnosis accepted.", False)

    async def _record(self, s: _RunState, step: Step) -> None:
        s.steps.append(step)
        await self.traces.step(s.run.run_id, step)

    async def _file_approval(
        self, d: Diagnosis, incident: Incident, run: RunSummary
    ) -> dict[str, Any] | None:
        action = d.recommended_action
        if not action.requires_approval or action.target is None:
            return None
        request, _ = await self.approvals.request(
            "drain_node" if action.action == ActionType.DRAIN_NODE else "reset_gpu",
            action.target.node_id,
            action.target.gpu_index if action.action == ActionType.RESET_GPU else None,
            action.rationale,
            f"agent:{run.run_id}",
            incident.incident_id,
            run.run_id,
        )
        return request

    def _fail(self, s: _RunState, status: str, error: str) -> None:
        s.run.status = status
        s.run.error = error
        s.diagnosis = None


@dataclass
class _RunState:
    run: RunSummary
    incident: Incident
    ctx: ToolContext
    diagnosis: Diagnosis | None = None
    steps: list[Step] = field(default_factory=list)
    last_input_tokens: int = 0
    invalid: int = 0
    nudges: int = 0

    @property
    def tokens(self) -> int:
        return self.run.input_tokens + self.run.output_tokens
