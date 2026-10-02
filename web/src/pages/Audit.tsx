import { Badge, Card, ErrorNote, label, when } from "../components/ui";
import { useGet } from "../hooks";

interface AuditRow {
  id: number;
  at: string;
  request_id: string;
  event: "requested" | "approved" | "rejected";
  actor: string;
  action: string;
  node_id: string;
  gpu_index: number | null;
  note: string | null;
}

const TONE = { requested: "amber", approved: "green", rejected: "red" } as const;

export function Audit() {
  const { data, error } = useGet<AuditRow[]>("/v1/ui/audit?limit=200");
  return (
    <Card title="Approval audit log (append-only)">
      <ErrorNote error={error} />
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase text-slate-500">
          <tr><th className="py-2">When</th><th>Event</th><th>Who</th><th>Action</th><th>Target</th><th>Note</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {data?.map((a) => (
            <tr key={a.id}>
              <td className="py-2 text-slate-600">{when(a.at)}</td>
              <td><Badge tone={TONE[a.event]}>{a.event}</Badge></td>
              <td className="font-mono text-xs">{a.actor}</td>
              <td>{label(a.action)}</td>
              <td className="text-slate-600">{a.node_id}{a.gpu_index !== null ? ` GPU ${a.gpu_index}` : ""}</td>
              <td className="max-w-md truncate text-slate-600" title={a.note ?? ""}>{a.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
