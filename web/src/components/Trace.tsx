import { useState } from "react";
import type { Run } from "../api";
import { useGet } from "../hooks";
import { Badge } from "./ui";

function short(v: unknown, n = 160): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > n ? `${s.slice(0, n)}...` : s;
}

export function Trace({ runId }: { runId: string }) {
  const { data } = useGet<Run>(`/v1/ui/runs/${runId}`);
  const [open, setOpen] = useState<number | null>(null);
  if (!data) return <p className="text-sm text-slate-500">Loading trace...</p>;
  return (
    <div className="space-y-1 text-sm">
      <p className="mb-2 text-xs text-slate-500">
        {data.steps} model calls · {data.tool_calls} tool calls · {(data.input_tokens + data.output_tokens).toLocaleString()} tokens ·{" "}
        {data.latency_ms ? `${Math.round(data.latency_ms / 1000)} s` : "-"} · {data.model}
      </p>
      {data.trace.map((s) => (
        <div key={s.seq} className={`rounded border ${s.is_error ? "border-red-200 bg-red-50" : "border-slate-200"}`}>
          <button className="flex w-full items-center gap-2 px-2 py-1 text-left" onClick={() => setOpen(open === s.seq ? null : s.seq)}>
            <Badge tone={s.kind === "model" ? "blue" : s.is_error ? "red" : "green"}>{s.kind}</Badge>
            <span className="font-mono text-xs">{s.kind === "model" ? "model" : s.name}</span>
            <span className="truncate text-xs text-slate-500">
              {s.kind === "tool" ? short(s.input, 90) : short((s.output as { tool_calls?: { name: string }[] })?.tool_calls?.map((c) => c.name), 90)}
            </span>
            <span className="ml-auto text-xs text-slate-400">{(s.latency_ms / 1000).toFixed(1)} s</span>
          </button>
          {open === s.seq && (
            <pre className="max-h-72 overflow-auto border-t border-slate-100 bg-slate-50 p-2 text-xs">
              {JSON.stringify(s.kind === "model" ? s.output : { input: s.input, output: s.output }, null, 2)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
