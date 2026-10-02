import { Link } from "react-router-dom";
import { Badge, Card, ErrorNote, label, when } from "../components/ui";
import { useGet } from "../hooks";

interface Report {
  days: number;
  up: number;
  down: number;
  by_root_cause: { root_cause: string | null; up: number; down: number }[];
  recent: {
    created_at: string;
    username: string;
    rating: number;
    comment: string | null;
    source: string;
    incident_id: string;
    root_cause: string | null;
    kind: string | null;
  }[];
}

export function Feedback() {
  const { data, error } = useGet<Report>("/v1/ui/feedback?days=30");
  const total = (data?.up ?? 0) + (data?.down ?? 0);
  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      <div className="grid grid-cols-3 gap-4">
        <Card title="Ratings, last 30 days"><p className="text-3xl font-semibold">{total}</p></Card>
        <Card title="Helpful"><p className="text-3xl font-semibold text-emerald-700">{data?.up ?? 0}</p></Card>
        <Card title="Not helpful"><p className="text-3xl font-semibold text-red-700">{data?.down ?? 0}</p></Card>
      </div>
      <Card title="By diagnosed root cause">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr><th className="py-2">Root cause</th><th>Helpful</th><th>Not helpful</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.by_root_cause.map((r) => (
              <tr key={r.root_cause ?? "none"}>
                <td className="py-2">{label(r.root_cause ?? "follow-up answers")}</td>
                <td>{r.up}</td>
                <td className={r.down ? "font-medium text-red-700" : ""}>{r.down}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Recent feedback (candidates for new eval cases)">
        <ul className="divide-y divide-slate-100 text-sm">
          {data?.recent.map((f, i) => (
            <li key={i} className="flex items-start gap-3 py-2">
              <span>{f.rating > 0 ? "👍" : "👎"}</span>
              <div className="flex-1">
                <Link className="text-sky-700 hover:underline" to={`/incidents/${f.incident_id}`}>
                  {f.kind === "followup" ? "Follow-up answer" : label(f.root_cause)}
                </Link>{" "}
                <span className="text-slate-500">by {f.username} · {when(f.created_at)}</span>{" "}
                <Badge>{f.source}</Badge>
                {f.comment && <p className="mt-0.5 text-slate-700">{f.comment}</p>}
              </div>
            </li>
          ))}
          {data?.recent.length === 0 && <li className="py-4 text-slate-500">No feedback yet.</li>}
        </ul>
      </Card>
    </div>
  );
}
