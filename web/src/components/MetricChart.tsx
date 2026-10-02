import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { MetricPoint } from "../api";
import { useGet } from "../hooks";

interface Series {
  series: MetricPoint[];
}

const hhmm = (t: string) => new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

export function MetricChart({ incidentId, gpu, start, end }: { incidentId: string; gpu: number; start: string; end: string }) {
  const { data } = useGet<Series>(`/v1/ui/incidents/${incidentId}/metrics?gpu=${gpu}&before_min=45&after_min=20`);
  const points = (data?.series ?? []).map((p) => ({ ...p, t: p.t }));
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-600">GPU {gpu}</p>
      <div className="h-48">
        <ResponsiveContainer>
          <LineChart data={points} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="#eef2f7" />
            <XAxis dataKey="t" tickFormatter={hhmm} tick={{ fontSize: 11 }} minTickGap={40} />
            <YAxis yAxisId="c" tick={{ fontSize: 11 }} domain={[20, "auto"]} unit="°" />
            <YAxis yAxisId="w" orientation="right" tick={{ fontSize: 11 }} unit="W" />
            <Tooltip labelFormatter={(t) => hhmm(String(t))} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <ReferenceArea yAxisId="c" x1={start} x2={end} fill="#fde68a" fillOpacity={0.35} />
            <Line yAxisId="c" dataKey="temp_max_c" name="temp °C" stroke="#dc2626" dot={false} strokeWidth={1.5} />
            <Line yAxisId="w" dataKey="power_avg_w" name="power W" stroke="#2563eb" dot={false} strokeWidth={1.5} />
            <Line yAxisId="w" dataKey="power_limit_w" name="power limit W" stroke="#64748b" dot={false} strokeDasharray="4 3" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
