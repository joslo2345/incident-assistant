import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Approval, type IncidentDetail as Detail } from "../api";
import { useAuth } from "../auth";
import { CitationChip } from "../components/Citation";
import { MetricChart } from "../components/MetricChart";
import { Trace } from "../components/Trace";
import { Badge, Button, Card, ErrorNote, label, when } from "../components/ui";
import { useGet } from "../hooks";

function Feedback({ runId }: { runId: string }) {
  const [sent, setSent] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const rate = async (rating: 1 | -1) => {
    const comment = rating < 0 ? window.prompt("What was wrong? (optional)") ?? undefined : undefined;
    try {
      await api.post(`/v1/ui/runs/${runId}/feedback`, { rating, comment });
      setSent(rating);
    } catch (e) {
      setError(e);
    }
  };
  return (
    <span className="flex items-center gap-1 text-sm">
      <button title="Helpful" className={`rounded px-1.5 ${sent === 1 ? "bg-emerald-100" : "hover:bg-slate-100"}`} onClick={() => rate(1)}>👍</button>
      <button title="Not helpful" className={`rounded px-1.5 ${sent === -1 ? "bg-red-100" : "hover:bg-slate-100"}`} onClick={() => rate(-1)}>👎</button>
      {error ? <span className="text-xs text-red-600">failed</span> : null}
    </span>
  );
}

function ApprovalRow({ a, canApprove, onDone }: { a: Approval; canApprove: boolean; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const decide = async (decision: "approve" | "reject") => {
    const note = window.prompt(`Note for this ${decision} (optional)`) ?? undefined;
    setBusy(true);
    try {
      await api.post(`/v1/ui/approvals/${a.request_id}/decision`, { decision, note });
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const tone = a.status === "pending" ? "amber" : a.status === "approved" ? "green" : "red";
  return (
    <li className="space-y-1 py-2">
      <div className="flex items-center gap-2">
        <Badge tone={tone}>{a.status}</Badge>
        <span className="font-medium">{label(a.action)}</span>
        <span className="text-slate-500">{a.node_id}{a.gpu_index !== null ? ` GPU ${a.gpu_index}` : ""}</span>
        {a.status === "pending" && canApprove && (
          <span className="ml-auto flex gap-2">
            <Button tone="success" disabled={busy} onClick={() => decide("approve")}>Approve</Button>
            <Button tone="danger" disabled={busy} onClick={() => decide("reject")}>Reject</Button>
          </span>
        )}
      </div>
      <p className="text-sm text-slate-600">{a.reason}</p>
      <p className="text-xs text-slate-500">
        Requested by <span className="font-mono">{a.requested_by.slice(0, 18)}</span> · {when(a.created_at)}
        {a.decided_by && <> · {a.status} by <b>{a.decided_by}</b> · {when(a.decided_at)}{a.decision_note ? ` · "${a.decision_note}"` : ""}</>}
      </p>
      {a.status === "pending" && !canApprove && <p className="text-xs text-slate-500">Approver role needed to decide.</p>}
      <ErrorNote error={error} />
    </li>
  );
}

function Chat({ detail, onAnswered }: { detail: Detail; onAnswered: () => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ask = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/v1/ui/incidents/${detail.incident.incident_id}/ask`, { question: q });
      setQ("");
      onAnswered();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      {detail.followups.map((f) => (
        <div key={f.run_id} className="space-y-1">
          <p className="text-sm"><b>{f.asked_by ?? "someone"}:</b> {f.question}</p>
          {f.answer ? (
            <div className="rounded bg-slate-50 p-2 text-sm">
              <p className="whitespace-pre-wrap">{f.answer.answer}</p>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {f.answer.citations.map((c) => <CitationChip key={c.chunk_id} citation={c} />)}
                <span className="ml-auto"><Feedback runId={f.run_id} /></span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No answer ({f.status}).</p>
          )}
        </div>
      ))}
      <form onSubmit={ask} className="flex gap-2">
        <input className="flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm" value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="Ask about this incident, e.g. has this node had this before?" minLength={3} required disabled={busy} />
        <Button type="submit" tone="primary" disabled={busy}>{busy ? "Thinking (1-3 min)..." : "Ask"}</Button>
      </form>
      <ErrorNote error={error} />
    </div>
  );
}

export function IncidentDetail() {
  const { id } = useParams();
  const { me } = useAuth();
  const { data, error, reload } = useGet<Detail>(`/v1/ui/incidents/${id}`);
  const [showTrace, setShowTrace] = useState(false);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="text-slate-500">Loading...</p>;
  const inc = data.incident;
  const d = data.diagnosis?.diagnosis;
  const gpus = [...new Set(inc.components.filter((c) => c.gpu_index !== null).map((c) => c.gpu_index as number))].slice(0, 4);
  const start = inc.evidence.reduce((m, e) => (e.window_start < m ? e.window_start : m), inc.evidence[0].window_start);
  const end = inc.evidence.reduce((m, e) => (e.window_start > m ? e.window_start : m), inc.evidence[0].window_start);

  return (
    <div className="space-y-4">
      <div>
        <Link to={`/?run=${encodeURIComponent(data.detector_run)}`} className="text-sm text-sky-700 hover:underline">← Incidents</Link>
        <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
          <Badge tone={inc.severity}>{inc.severity}</Badge> {inc.title}
        </h1>
        <p className="text-sm text-slate-500">
          Opened {when(inc.created_at)} · {inc.status} · detector suspects {label(inc.suspected_failure_type)}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card
            title="Diagnosis"
            actions={data.diagnosis && <Feedback runId={data.diagnosis.run_id} />}
          >
            {d ? (
              <div className="space-y-3 text-sm">
                <p className="text-base">
                  <b>{label(d.root_cause)}</b>{" "}
                  <span className="text-slate-500">· confidence {Math.round(d.confidence * 100)}%</span>
                </p>
                <p className="whitespace-pre-wrap text-slate-800">{d.summary}</p>
                <div className="rounded bg-slate-50 p-3">
                  <p><b>Recommended: {label(d.recommended_action.action)}</b>
                    {d.recommended_action.target && <span className="text-slate-500"> on {d.recommended_action.target.node_id}
                      {d.recommended_action.target.gpu_index !== null ? ` GPU ${d.recommended_action.target.gpu_index}` : ""}</span>}</p>
                  <p className="text-slate-700">{d.recommended_action.rationale}</p>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  <span className="text-xs text-slate-500">Sources:</span>
                  {d.citations.map((c) => <CitationChip key={c.chunk_id} citation={c} />)}
                </div>
                <p className="text-xs text-slate-500">
                  Evidence used: {d.evidence_ids.join(", ")} · {d.model} ·{" "}
                  <button className="text-sky-700 hover:underline" onClick={() => setShowTrace(!showTrace)}>
                    {showTrace ? "hide" : "show"} tool-call trace
                  </button>
                </p>
                {showTrace && <Trace runId={data.diagnosis!.run_id} />}
              </div>
            ) : (
              <p className="text-sm text-slate-500">Not investigated yet.</p>
            )}
            {me?.can_approve && (
              <div className="mt-3">
                <Button onClick={() => api.post(`/v1/ui/incidents/${inc.incident_id}/investigate`).then(reload)}>
                  {d ? "Investigate again" : "Investigate now"}
                </Button>
              </div>
            )}
          </Card>

          <Card title="Telemetry around the incident (highlighted: detector signals)">
            {gpus.length === 0 ? (
              <p className="text-sm text-slate-500">Node-level incident: open a GPU in Grafana for details.</p>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {gpus.map((g) => <MetricChart key={g} incidentId={inc.incident_id} gpu={g} start={start} end={end} />)}
              </div>
            )}
          </Card>

          <Card title="Ask a follow-up question">
            <Chat detail={data} onAnswered={reload} />
          </Card>
        </div>

        <div className="space-y-4">
          <Card title="Actions awaiting approval">
            {data.approvals.length === 0 ? (
              <p className="text-sm text-slate-500">None. The agent never acts on its own.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {data.approvals.map((a) => <ApprovalRow key={a.request_id} a={a} canApprove={!!me?.can_approve} onDone={reload} />)}
              </ul>
            )}
          </Card>
          <Card title={`Evidence (${inc.evidence.length})`}>
            <ul className="space-y-1.5 text-xs">
              {inc.evidence.map((e) => (
                <li key={e.evidence_id} className={d?.evidence_ids.includes(e.evidence_id) ? "font-medium" : "text-slate-600"}>
                  <span className="font-mono text-slate-400">{e.evidence_id}</span> {when(e.window_start)} · {e.description}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
