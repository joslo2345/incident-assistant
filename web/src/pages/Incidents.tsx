import { Link, useSearchParams } from "react-router-dom";
import type { IncidentRow } from "../api";
import { Badge, Card, ErrorNote, label, when } from "../components/ui";
import { useGet } from "../hooks";

interface DetectorRun {
  detector_run: string;
  incidents: number;
}

export function Incidents() {
  const [params, setParams] = useSearchParams();
  const run = params.get("run") ?? "live";
  const runs = useGet<DetectorRun[]>("/v1/ui/detector-runs");
  const { data, error } = useGet<IncidentRow[]>(`/v1/ui/incidents?run=${encodeURIComponent(run)}&limit=200`);

  return (
    <Card
      title="Incidents"
      actions={
        <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={run}
                onChange={(e) => setParams({ run: e.target.value })}>
          {(runs.data ?? [{ detector_run: "live", incidents: 0 }]).map((r) => (
            <option key={r.detector_run} value={r.detector_run}>
              {r.detector_run} ({r.incidents})
            </option>
          ))}
        </select>
      }
    >
      <ErrorNote error={error} />
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="py-2">Severity</th>
            <th>Incident</th>
            <th>Opened</th>
            <th>Detector says</th>
            <th>Agent diagnosis</th>
            <th>Action</th>
            <th />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {data?.map((i) => (
            <tr key={i.incident_id} className="hover:bg-slate-50">
              <td className="py-2"><Badge tone={i.severity}>{i.severity}</Badge></td>
              <td>
                <Link className="font-medium text-sky-700 hover:underline" to={`/incidents/${i.incident_id}`}>
                  {i.title}
                </Link>
                <div className="text-xs text-slate-500">{i.node_id} · {i.status}</div>
              </td>
              <td className="text-slate-600">{when(i.opened_at)}</td>
              <td className="text-slate-600">{label(i.suspected_failure_type)}</td>
              <td>
                {i.root_cause ? (
                  <span>
                    {label(i.root_cause)}{" "}
                    <span className="text-xs text-slate-500">{Math.round((i.confidence ?? 0) * 100)}%</span>
                  </span>
                ) : (
                  <span className="text-slate-400">not investigated</span>
                )}
              </td>
              <td>{label(i.action)}</td>
              <td>{i.pending > 0 && <Badge tone="amber">{i.pending} pending</Badge>}</td>
            </tr>
          ))}
          {data?.length === 0 && (
            <tr><td colSpan={7} className="py-6 text-center text-slate-500">No incidents in this run.</td></tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}
