import { useState } from "react";
import { api, type Chunk, type Citation } from "../api";

export function CitationChip({ citation }: { citation: Citation }) {
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const [open, setOpen] = useState(false);
  const show = async () => {
    setOpen(true);
    if (!chunk) setChunk(await api.get<Chunk>(`/v1/ui/chunks/${encodeURIComponent(citation.chunk_id)}`));
  };
  return (
    <>
      <button onClick={show} className="rounded bg-sky-50 px-1.5 py-0.5 font-mono text-xs text-sky-800 ring-1 ring-sky-200 hover:bg-sky-100">
        {citation.chunk_id}
      </button>
      {open && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30 p-6" onClick={() => setOpen(false)}>
          <div className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-lg bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-start justify-between">
              <div>
                <p className="font-semibold">{chunk?.title ?? "Loading..."}</p>
                <p className="text-sm text-slate-500">{chunk?.heading}</p>
              </div>
              <button className="text-slate-400 hover:text-slate-700" onClick={() => setOpen(false)}>✕</button>
            </div>
            <pre className="whitespace-pre-wrap font-sans text-sm text-slate-800">{chunk?.text}</pre>
            <p className="mt-3 font-mono text-xs text-slate-400">{citation.chunk_id}</p>
          </div>
        </div>
      )}
    </>
  );
}
